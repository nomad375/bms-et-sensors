import threading
import time
from collections import deque

from influxdb_client import InfluxDBClient, Point  # type: ignore
from influxdb_client.client.write_api import ASYNCHRONOUS, WriteOptions  # type: ignore
from influxdb_client.domain.write_precision import WritePrecision  # type: ignore


def run_stream_loop(
    *,
    stream_enabled,
    influx_url,
    influx_token,
    influx_org,
    influx_bucket,
    measurement,
    source_radio,
    read_timeout_ms,
    idle_sleep,
    batch_size,
    flush_interval_ms,
    queue_max,
    queue_wait_ms,
    drop_warn_sec,
    drop_log_throttle_sec,
    log_interval_sec,
    only_channel_1,
    state,
    log_func,
    internal_connect,
    mark_base_disconnected,
    metric_inc,
    metric_set,
    metric_max,
    point_channel_fn,
    point_value_fn,
    point_time_ns_fn,
    sample_rate_to_hz_fn,
    resampled_enabled,
    resampled_measurement,
    resampled_include_raw_ts,
):
    if not stream_enabled:
        log_func("[mscl-stream] Disabled via MSCL_STREAM_ENABLED")
        return
    if not all([influx_token, influx_org, influx_bucket]):
        log_func("[mscl-stream] Missing INFLUX_TOKEN / INFLUX_ORG / INFLUX_BUCKET; stream disabled")
        return

    log_func(
        f"[mscl-stream] Influx target: {influx_url} bucket={influx_bucket} org={influx_org} measurement={measurement}"
    )
    if resampled_enabled:
        log_func(f"[mscl-stream] Resampled stream enabled: measurement={resampled_measurement}")
    db_client = InfluxDBClient(url=influx_url, token=influx_token, org=influx_org)
    write_api = db_client.write_api(
        write_options=WriteOptions(
            batch_size=batch_size,
            flush_interval=flush_interval_ms,
            jitter_interval=200,
            retry_interval=1000,
            max_retries=3,
            max_retry_delay=10000,
            max_retry_time=60000,
            exponential_base=2,
        ),
        write_type=ASYNCHRONOUS,
    )

    queue_cond = threading.Condition()
    packet_queue = deque()
    last_ch1_ts = 0.0
    last_drop_log_ts = 0.0
    last_batch_log_ts = 0.0
    last_diag = {}

    def note_diag(channel, value):
        last_diag[channel] = value

    def maybe_log_batch(now_ts, channel_counts, point_count):
        nonlocal last_batch_log_ts
        if (now_ts - last_batch_log_ts) < log_interval_sec:
            return
        last_batch_log_ts = now_ts
        channels_txt = ", ".join(f"{k}:{v}" for k, v in sorted(channel_counts.items()))
        log_func(f"[mscl-stream] Logged {point_count} points ({channels_txt})")

    def packet_rate_label(packet):
        for getter in (
            lambda: packet.sampleRate().prettyStr(),
            lambda: packet.sampleRate().toString(),
            lambda: str(packet.sampleRate()),
        ):
            try:
                s = str(getter()).strip()
                if s:
                    return s
            except Exception:
                continue
        return "unknown"

    def maybe_log_drop(now_ts):
        nonlocal last_drop_log_ts
        if last_ch1_ts <= 0:
            return
        gap = now_ts - last_ch1_ts
        if gap < drop_warn_sec:
            return
        if now_ts - last_drop_log_ts < drop_log_throttle_sec:
            return
        last_drop_log_ts = now_ts
        diag_summary = ", ".join(
            f"{k}={last_diag.get(k)}"
            for k in (
                "diagnostic_state",
                "diagnostic_syncFailures",
                "diagnostic_totalDroppedPackets",
                "diagnostic_lowBatteryFlag",
                "diagnostic_memoryFull",
            )
        )
        log_func(f"[mscl-stream] Warning: no ch1 data for {gap:.1f}s; {diag_summary}")

    def reader_loop():
        backoff = 1.0
        backoff_max = 10.0
        disconnected = True
        while True:
            try:
                pause_until = float(getattr(state, "STREAM_PAUSE_UNTIL", 0.0) or 0.0)
                now_ts = time.time()
                if now_ts < pause_until:
                    time.sleep(min(0.25, max(0.05, pause_until - now_ts)))
                    continue

                ok, _ = internal_connect()
                if not ok or state.BASE_STATION is None:
                    metric_inc("base_reconnect_attempts")
                    disconnected = True
                    time.sleep(backoff)
                    backoff = min(backoff_max, backoff * 1.7)
                    continue
                if disconnected:
                    metric_inc("base_reconnect_successes")
                    disconnected = False

                with state.OP_LOCK:
                    base_station = state.BASE_STATION
                    if base_station is None:
                        time.sleep(idle_sleep)
                        continue
                    packets = base_station.getData(read_timeout_ms)

                if not packets:
                    backoff = 1.0
                    time.sleep(idle_sleep)
                    continue

                with queue_cond:
                    packet_queue.extend(packets)
                    dropped = 0
                    while len(packet_queue) > queue_max:
                        packet_queue.popleft()
                        dropped += 1
                    q_depth = len(packet_queue)
                    queue_cond.notify_all()
                metric_inc("stream_packets_read", len(packets))
                metric_set("stream_queue_depth", q_depth)
                metric_max("stream_queue_hwm", q_depth)
                if dropped:
                    metric_inc("stream_queue_dropped_packets", dropped)
                backoff = 1.0
            except Exception as e:
                log_func(f"[mscl-stream] Reader error: {e}")
                metric_inc("stream_reader_errors")
                mark_base_disconnected()
                disconnected = True
                time.sleep(backoff)
                backoff = min(backoff_max, backoff * 1.7)

    threading.Thread(target=reader_loop, daemon=True).start()

    def build_resampled_rows(raw_rows):
        grouped = {}
        for row in raw_rows:
            sec = int(row["t_ns"]) // 1_000_000_000
            key = (row["node_id"], row["channel"], sec)
            grouped.setdefault(key, []).append(row)

        out = []
        for (_node_id, _channel, sec), rows in grouped.items():
            if len(rows) <= 1:
                row = dict(rows[0])
                row["t_resampled_ns"] = int(row["t_ns"])
                out.append(row)
                continue

            rows_sorted = sorted(rows, key=lambda r: int(r["t_ns"]))
            n = len(rows_sorted)
            sec_start = int(sec) * 1_000_000_000
            span_limit = 999_999_999

            hz_vals = []
            for r in rows_sorted:
                hz = r.get("rate_hz")
                if isinstance(hz, (int, float)) and float(hz) > 0:
                    hz_vals.append(float(hz))
            step_from_rate = None
            if hz_vals:
                try:
                    step_from_rate = max(1, int(round(1_000_000_000.0 / max(hz_vals))))
                except Exception:
                    step_from_rate = None

            step_auto = max(1, span_limit // max(1, n - 1))
            if step_from_rate is not None and (step_from_rate * (n - 1)) <= span_limit:
                step_ns = step_from_rate
            else:
                step_ns = step_auto

            used_span = int(step_ns) * (n - 1)
            start_ns = sec_start + ((span_limit - used_span) // 2)
            for idx, row in enumerate(rows_sorted):
                t_resampled_ns = int(start_ns + (idx * step_ns))
                row_out = dict(row)
                row_out["t_resampled_ns"] = t_resampled_ns
                out.append(row_out)
        return out

    while True:
        try:
            with queue_cond:
                if not packet_queue:
                    queue_cond.wait(timeout=queue_wait_ms / 1000.0)
                packets = []
                while packet_queue:
                    packets.append(packet_queue.popleft())

            if not packets:
                time.sleep(idle_sleep)
                continue

            raw_rows = []
            channel_counts = {}
            packet_rate_counts = {}
            for packet in packets:
                node_address = str(packet.nodeAddress())
                rate_lbl = packet_rate_label(packet)
                rate_hz = None
                try:
                    if sample_rate_to_hz_fn is not None:
                        rate_hz = sample_rate_to_hz_fn(rate_lbl)
                except Exception:
                    rate_hz = None
                packet_rate_counts[rate_lbl] = packet_rate_counts.get(rate_lbl, 0) + 1
                for dp in packet.data():
                    channel = point_channel_fn(dp)
                    if only_channel_1 and channel not in ("channel_1", "ch1"):
                        continue
                    value = point_value_fn(dp)
                    if value is None:
                        continue
                    if channel.startswith("diagnostic_"):
                        note_diag(channel, value)
                    t_ns = point_time_ns_fn(dp)
                    raw_rows.append(
                        {
                            "node_id": node_address,
                            "channel": channel,
                            "source": source_radio,
                            "value": value,
                            "t_ns": int(t_ns),
                            "rate_hz": rate_hz,
                        }
                    )
                    channel_counts[channel] = channel_counts.get(channel, 0) + 1
                    if channel in ("channel_1", "ch1"):
                        last_ch1_ts = time.time()

            points = []
            point_key_counts = {}
            for row in raw_rows:
                t_ns = int(row["t_ns"])
                key = (row["node_id"], row["channel"], t_ns)
                dup_idx = point_key_counts.get(key, 0)
                point_key_counts[key] = dup_idx + 1
                if dup_idx:
                    t_ns += dup_idx
                point = (
                    Point(measurement)
                    .tag("node_id", row["node_id"])
                    .tag("channel", row["channel"])
                    .tag("source", row["source"])
                    .field("value", row["value"])
                    .time(t_ns, WritePrecision.NS)
                )
                points.append(point)

            resampled_points = []
            if resampled_enabled and raw_rows:
                resampled_rows = build_resampled_rows(raw_rows)
                resampled_key_counts = {}
                for row in resampled_rows:
                    t_resampled_ns = int(row["t_resampled_ns"])
                    key = (row["node_id"], row["channel"], t_resampled_ns)
                    dup_idx = resampled_key_counts.get(key, 0)
                    resampled_key_counts[key] = dup_idx + 1
                    if dup_idx:
                        t_resampled_ns += dup_idx
                    point = (
                        Point(resampled_measurement)
                        .tag("node_id", row["node_id"])
                        .tag("channel", row["channel"])
                        .tag("source", row["source"])
                        .tag("time_model", "resampled_uniform_second")
                        .field("value", row["value"])
                    )
                    if resampled_include_raw_ts:
                        point = point.field("raw_ts_ns", int(row["t_ns"]))
                    point = point.time(t_resampled_ns, WritePrecision.NS)
                    resampled_points.append(point)

            if points:
                write_api.write(influx_bucket, influx_org, points)
                if resampled_points:
                    write_api.write(influx_bucket, influx_org, resampled_points)
                metric_inc("stream_write_calls")
                metric_inc("stream_points_written", len(points))
                if resampled_points:
                    metric_inc("stream_points_written_resampled", len(resampled_points))
                maybe_log_batch(time.time(), channel_counts, len(points))
                if packet_rate_counts:
                    rate_txt = ", ".join(f"{k}:{v}" for k, v in sorted(packet_rate_counts.items()))
                    log_func(f"[mscl-stream] Packet rates ({rate_txt})")
            maybe_log_drop(time.time())
        except Exception as e:
            log_func(f"[mscl-stream] Writer error: {e}")
            metric_inc("stream_writer_errors")
            time.sleep(idle_sleep)


__all__ = ["run_stream_loop"]
