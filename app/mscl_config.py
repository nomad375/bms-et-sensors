import logging
import os
import time
import threading
import csv
import io
import json
from collections import deque
from datetime import datetime, timezone

from flask import Flask, render_template, request, jsonify, send_file, Response  # type: ignore
from influxdb_client import InfluxDBClient, Point  # type: ignore
from influxdb_client.client.write_api import ASYNCHRONOUS, SYNCHRONOUS, WriteOptions  # type: ignore
from influxdb_client.domain.write_precision import WritePrecision  # type: ignore

from mscl_constants import (
    COMM_PROTOCOL_MAP,
    DATA_MODE_LABELS,
    DEFAULT_MODE_LABELS,
    INPUT_RANGE_LABELS,
    LOW_PASS_LABELS,
    PRIMARY_INPUT_RANGES,
    PRIMARY_UNIT_ORDER,
    RATE_MAP,
    RTD_SENSOR_LABELS,
    RTD_WIRE_LABELS,
    SAMPLING_MODE_LABELS,
    SAMPLING_MODE_MAP,
    STORAGE_LIMIT_LABELS,
    TC_LINK_200_RATE_ENUMS,
    TEMP_UNIT_ORDER,
    THERMISTOR_SENSOR_LABELS,
    THERMOCOUPLE_SENSOR_LABELS,
    TRANSDUCER_LABELS,
    TX_POWER_ENUM_TO_DBM,
    UNIT_LABELS,
    _is_temp_unit,
    _unit_family,
    _wt,
)

import mscl_state as state
import MSCL as mscl  # type: ignore

log = state.log
internal_connect = state.internal_connect
ensure_beacon_on = state.ensure_beacon_on
ch1_mask = state.ch1_mask
ch2_mask = state.ch2_mask
set_idle_with_retry = state.set_idle_with_retry
_get_temp_sensor_options = state._get_temp_sensor_options
_set_temp_sensor_options = state._set_temp_sensor_options
_filter_default_modes = state._filter_default_modes
_feature_supported = state._feature_supported
_node_state_info = state._node_state_info
close_base_station = state.close_base_station
mark_base_disconnected = state.mark_base_disconnected
metric_inc = state.metric_inc
metric_set = state.metric_set
metric_max = state.metric_max
metric_snapshot = state.metric_snapshot

app = Flask(__name__)

# Suppress Flask request logs (GET/POST lines)
logging.getLogger("werkzeug").setLevel(logging.WARNING)

INFLUX_URL = os.getenv("INFLUX_URL", "http://influxdb:8086")
INFLUX_TOKEN = os.getenv("INFLUX_TOKEN")
INFLUX_ORG = os.getenv("INFLUX_ORG")
INFLUX_BUCKET = os.getenv("INFLUX_BUCKET")
MSCL_MEASUREMENT = os.getenv("MSCL_MEASUREMENT", "mscl_sensors")
MSCL_ONLY_CHANNEL_1 = os.getenv("MSCL_ONLY_CHANNEL_1", "false").lower() == "true"
MSCL_STREAM_ENABLED = os.getenv("MSCL_STREAM_ENABLED", "true").lower() not in ("0", "false", "no")
MSCL_STREAM_READ_TIMEOUT_MS = int(os.getenv("MSCL_STREAM_READ_TIMEOUT_MS", "20"))
MSCL_STREAM_IDLE_SLEEP = float(os.getenv("MSCL_STREAM_IDLE_SLEEP", "0.005"))
MSCL_STREAM_BATCH_SIZE = int(os.getenv("MSCL_STREAM_BATCH_SIZE", "5000"))
MSCL_STREAM_FLUSH_INTERVAL_MS = int(os.getenv("MSCL_STREAM_FLUSH_INTERVAL_MS", "500"))
MSCL_STREAM_QUEUE_MAX = int(os.getenv("MSCL_STREAM_QUEUE_MAX", "5000"))
MSCL_STREAM_QUEUE_WAIT_MS = int(os.getenv("MSCL_STREAM_QUEUE_WAIT_MS", "200"))
MSCL_STREAM_DROP_WARN_SEC = float(os.getenv("MSCL_STREAM_DROP_WARN_SEC", "30"))
MSCL_STREAM_DROP_LOG_THROTTLE_SEC = float(os.getenv("MSCL_STREAM_DROP_LOG_THROTTLE_SEC", "30"))
MSCL_STREAM_LOG_INTERVAL_SEC = float(os.getenv("MSCL_STREAM_LOG_INTERVAL_SEC", "5"))
MSCL_EXPORT_ALIGN_MIN_SKEW_SEC = float(os.getenv("MSCL_EXPORT_ALIGN_MIN_SKEW_SEC", "2.0"))
MSCL_EXPORT_OFFSET_RECALC_THRESHOLD_SEC = float(os.getenv("MSCL_EXPORT_OFFSET_RECALC_THRESHOLD_SEC", "3.0"))
MSCL_EXPORT_OFFSET_RECALC_MAX_SKEW_SEC = float(os.getenv("MSCL_EXPORT_OFFSET_RECALC_MAX_SKEW_SEC", "30.0"))
MSCL_EXPORT_INFLUX_BATCH = int(os.getenv("MSCL_EXPORT_INFLUX_BATCH", "5000"))
MSCL_SOURCE_RADIO = os.getenv("MSCL_SOURCE_RADIO", "mscl_config_stream")
MSCL_SOURCE_NODE_EXPORT = os.getenv("MSCL_SOURCE_NODE_EXPORT", "mscl_node_export")
MSCL_META_MEASUREMENT = os.getenv("MSCL_META_MEASUREMENT", "mscl_meta")
MSCL_META_OFFSET_METRIC = "node_export_clock_offset_ns"


def _point_channel(dp):
    try:
        name = dp.channelName()
        if name:
            return str(name)
    except Exception:
        pass
    try:
        return f"ch{int(dp.channelId())}"
    except Exception:
        return "channel"


def _point_value(dp):
    for getter in (
        lambda: dp.as_float(),
        lambda: dp.as_double(),
        lambda: dp.as_int32(),
        lambda: dp.as_uint32(),
        lambda: dp.as_int16(),
        lambda: dp.as_uint16(),
        lambda: dp.as_int8(),
        lambda: dp.as_uint8(),
        lambda: dp.value(),
    ):
        try:
            return float(getter())
        except Exception:
            continue
    return None


def _point_time_ns(dp):
    """Best-effort datapoint timestamp in unix ns, with current-time fallback."""
    try:
        ts = dp.as_Timestamp()
        sec = int(ts.seconds())
        nsec = int(ts.nanoseconds())
        if sec > 0:
            if nsec < 0:
                nsec = 0
            elif nsec > 999_999_999:
                nsec = 999_999_999
            return sec * 1_000_000_000 + nsec
    except Exception:
        pass
    return time.time_ns()


def _timestamp_to_ns(ts):
    try:
        sec = int(ts.seconds())
        nsec = int(ts.nanoseconds())
        if sec <= 0:
            return None
        if nsec < 0:
            nsec = 0
        elif nsec > 999_999_999:
            nsec = 999_999_999
        return (sec * 1_000_000_000) + nsec
    except Exception:
        return None


def _ns_to_iso_utc(ts_ns):
    try:
        ts_ns = int(ts_ns)
        sec = ts_ns // 1_000_000_000
        nsec = ts_ns % 1_000_000_000
        dt = datetime.fromtimestamp(sec, tz=timezone.utc)
        return f"{dt.strftime('%Y-%m-%dT%H:%M:%S')}.{nsec:09d}Z"
    except Exception:
        return None


def _logged_sweep_time_ns(sweep):
    try:
        return _timestamp_to_ns(sweep.timestamp()) or time.time_ns()
    except Exception:
        return time.time_ns()


def _coerce_logged_sweeps(batch):
    # MSCL Python bindings may return either LoggedDataSweep or LoggedDataSweeps.
    if batch is None:
        return []
    if hasattr(batch, "data") and callable(getattr(batch, "data", None)):
        return [batch]
    try:
        return list(batch)
    except Exception:
        return [batch]


def _logged_sweep_rows(node_id, session_index, sample_rate_text, sweep):
    rows = []
    ts_ns = _logged_sweep_time_ns(sweep)
    ts_iso = _ns_to_iso_utc(ts_ns)
    try:
        tick = int(sweep.tick())
    except Exception:
        tick = None
    try:
        cal_applied = bool(sweep.calApplied())
    except Exception:
        cal_applied = None

    try:
        datapoints = sweep.data()
    except Exception:
        datapoints = []

    for dp in datapoints:
        value = _point_value(dp)
        if value is None:
            continue
        channel = _point_channel(dp)
        channel_id = None
        try:
            channel_id = int(dp.channelId())
        except Exception:
            pass
        rows.append(
            {
                "timestamp_utc": ts_iso,
                "timestamp_ns": int(ts_ns),
                "node_id": int(node_id),
                "session_index": session_index,
                "sample_rate": sample_rate_text,
                "channel": channel,
                "channel_id": channel_id,
                "value": float(value),
                "tick": tick,
                "cal_applied": cal_applied,
            }
        )
    return rows


def _pause_stream_reader(seconds, reason=""):
    try:
        sec = max(0.0, float(seconds))
    except Exception:
        sec = 0.0
    if sec <= 0:
        return
    until = time.time() + sec
    prev = float(getattr(state, "STREAM_PAUSE_UNTIL", 0.0) or 0.0)
    if until > prev:
        state.STREAM_PAUSE_UNTIL = until
        if reason:
            log(f"[mscl-stream] pause {sec:.1f}s reason={reason}")
        else:
            log(f"[mscl-stream] pause {sec:.1f}s")


def _query_bool(name, default=False):
    raw = request.args.get(name, None)
    if raw is None:
        return bool(default)
    s = str(raw).strip().lower()
    if s in ("1", "true", "yes", "on"):
        return True
    if s in ("0", "false", "no", "off"):
        return False
    return bool(default)


def _parse_iso_utc_to_ns(raw_value, name):
    s = str(raw_value or "").strip()
    if not s:
        raise ValueError(f"Missing {name}")
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        raise ValueError(f"Invalid {name}. Use ISO datetime (example: 2026-02-11T12:00:00Z).")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt_utc = dt.astimezone(timezone.utc)
    return int(dt_utc.timestamp() * 1_000_000_000)


def _load_persisted_export_offset_ns(node_id):
    if not all([INFLUX_TOKEN, INFLUX_ORG, INFLUX_BUCKET]):
        return None
    node_tag = str(int(node_id))
    bucket_q = json.dumps(INFLUX_BUCKET)
    measurement_q = json.dumps(MSCL_META_MEASUREMENT)
    node_q = json.dumps(node_tag)
    metric_q = json.dumps(MSCL_META_OFFSET_METRIC)
    flux = (
        f'from(bucket: {bucket_q})\n'
        f'  |> range(start: -3650d)\n'
        f'  |> filter(fn: (r) => r._measurement == {measurement_q})\n'
        f'  |> filter(fn: (r) => r._field == "value")\n'
        f'  |> filter(fn: (r) => r.node_id == {node_q})\n'
        f'  |> filter(fn: (r) => r.metric == {metric_q})\n'
        f'  |> last()'
    )
    try:
        with InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG) as db_client:
            for rec in db_client.query_api().query_stream(query=flux, org=INFLUX_ORG):
                try:
                    return int(rec.get_value())
                except Exception:
                    continue
    except Exception as e:
        log(f"[mscl-web] [EXPORT-STORAGE] offset-load failed node_id={node_id}: {e}")
    return None


def _persist_export_offset_ns(node_id, offset_ns):
    if not all([INFLUX_TOKEN, INFLUX_ORG, INFLUX_BUCKET]):
        return
    try:
        node_tag = str(int(node_id))
        off = int(offset_ns)
    except Exception:
        return
    point = (
        Point(MSCL_META_MEASUREMENT)
        .tag("node_id", node_tag)
        .tag("metric", MSCL_META_OFFSET_METRIC)
        .field("value", off)
        .time(time.time_ns(), WritePrecision.NS)
    )
    try:
        with InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG) as db_client:
            db_client.write_api(write_options=SYNCHRONOUS).write(INFLUX_BUCKET, INFLUX_ORG, [point])
    except Exception as e:
        log(f"[mscl-web] [EXPORT-STORAGE] offset-persist failed node_id={node_id}: {e}")


def _compute_export_clock_offset_ns(rows, node_id=None, min_skew_sec=2.0):
    """Estimate node->host clock offset using the newest datalog timestamp."""
    if not rows:
        return 0, 0
    max_node_ts = 0
    for row in rows:
        try:
            t = int(row.get("timestamp_ns"))
        except Exception:
            continue
        if t > max_node_ts:
            max_node_ts = t
    if max_node_ts <= 0:
        return 0, 0
    skew_ns = int(time.time_ns()) - int(max_node_ts)
    min_skew_ns = int(float(min_skew_sec) * 1_000_000_000)
    drift_threshold_ns = int(max(0.0, float(MSCL_EXPORT_OFFSET_RECALC_THRESHOLD_SEC)) * 1_000_000_000)
    recalc_max_skew_ns = int(max(0.0, float(MSCL_EXPORT_OFFSET_RECALC_MAX_SKEW_SEC)) * 1_000_000_000)

    # Keep a stable per-node offset to avoid shifting the same historical points
    # to different timestamps on repeated exports.
    chosen = 0 if abs(skew_ns) <= min_skew_ns else int(skew_ns)
    node_key = None
    try:
        if node_id is not None:
            node_key = int(node_id)
    except Exception:
        node_key = None

    def _should_recalc(existing_offset_ns):
        # Recalculate only for near-real-time exports; for historical exports
        # (large skew) keep stable offset to avoid timestamp churn.
        if abs(skew_ns) > recalc_max_skew_ns:
            return False
        return abs(int(existing_offset_ns) - int(skew_ns)) > drift_threshold_ns

    if node_key is not None:
        cached = state.NODE_EXPORT_CLOCK_OFFSET_NS.get(node_key)
        if cached is not None:
            try:
                cached_i = int(cached)
                if _should_recalc(cached_i):
                    state.NODE_EXPORT_CLOCK_OFFSET_NS[node_key] = int(chosen)
                    _persist_export_offset_ns(node_key, chosen)
                    log(
                        f"[mscl-web] [EXPORT-STORAGE] offset-recalc node_id={node_key} "
                        f"from={cached_i} to={chosen} skew_ns={skew_ns}"
                    )
                    return int(chosen), skew_ns
                return cached_i, skew_ns
            except Exception:
                pass
        persisted = _load_persisted_export_offset_ns(node_key)
        if persisted is not None:
            try:
                persisted_i = int(persisted)
                if _should_recalc(persisted_i):
                    state.NODE_EXPORT_CLOCK_OFFSET_NS[node_key] = int(chosen)
                    _persist_export_offset_ns(node_key, chosen)
                    log(
                        f"[mscl-web] [EXPORT-STORAGE] offset-refresh node_id={node_key} "
                        f"from={persisted_i} to={chosen} skew_ns={skew_ns}"
                    )
                    return int(chosen), skew_ns
                state.NODE_EXPORT_CLOCK_OFFSET_NS[node_key] = persisted_i
                return persisted_i, skew_ns
            except Exception:
                pass

    if node_key is not None:
        state.NODE_EXPORT_CLOCK_OFFSET_NS[node_key] = int(chosen)
        _persist_export_offset_ns(node_key, chosen)
    return chosen, skew_ns


def _sample_rate_text_to_hz(rate_text):
    s = str(rate_text or "").strip().lower().replace("-", " ")
    if not s:
        return None

    # Matches "64 hz", "1 khz", etc.
    hz = _rate_label_to_hz(s)
    if hz is not None:
        try:
            hz_v = float(hz)
            if hz_v > 0:
                return hz_v
        except Exception:
            pass

    # Matches "64 hertz" style from some MSCL wrappers.
    if "hertz" in s:
        head = s.split("hertz", 1)[0].strip()
        try:
            v = float(head.split()[-1])
            if v > 0:
                return v
        except Exception:
            pass

    # Matches "every N seconds/minutes/hours".
    sec = _rate_label_to_interval_seconds(s)
    if sec is not None:
        try:
            sec_v = float(sec)
            if sec_v > 0:
                return 1.0 / sec_v
        except Exception:
            pass
    return None


def _backfill_rows_to_influx_stream(node_id, rows, time_offset_ns=0, source_tag=MSCL_SOURCE_NODE_EXPORT):
    if not rows:
        return {"written": 0, "skipped_existing": 0}
    if not all([INFLUX_TOKEN, INFLUX_ORG, INFLUX_BUCKET]):
        raise RuntimeError("Influx is not configured (missing token/org/bucket)")

    node_tag = str(int(node_id))
    source_tag = str(source_tag or MSCL_SOURCE_NODE_EXPORT)
    batch = []
    total_written = 0
    total_skipped_existing = 0
    point_key_counts = {}
    channel_ranges = {}
    raw_channel_ranges = {}
    candidates = []
    tick_time_bases = {}

    with InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG) as db_client:
        query_api = db_client.query_api()
        write_api = db_client.write_api(write_options=SYNCHRONOUS)

        for row in rows:
            channel = str(row.get("channel") or "").strip()
            if not channel:
                continue
            try:
                value = float(row.get("value"))
                raw_ts_ns = int(row.get("timestamp_ns"))
            except Exception:
                continue
            tick_raw = row.get("tick")
            tick_val = None
            try:
                if tick_raw is not None:
                    tick_val = int(tick_raw)
            except Exception:
                tick_val = None
            session_idx = row.get("session_index")
            try:
                if session_idx is not None:
                    session_idx = int(session_idx)
            except Exception:
                session_idx = None
            rate_hz = _sample_rate_text_to_hz(row.get("sample_rate"))

            ts_base_ns = int(raw_ts_ns)
            # Node datalog timestamps are often coarse (1-second resolution).
            # Reconstruct sub-second timing from sweep tick + sample rate when available.
            if tick_val is not None and rate_hz is not None and float(rate_hz) > 0:
                base_key = (str(channel), session_idx)
                base = tick_time_bases.get(base_key)
                if base is None:
                    base = {"tick": int(tick_val), "ts": int(raw_ts_ns), "rate_hz": float(rate_hz)}
                    tick_time_bases[base_key] = base
                try:
                    step_ns = int(round(1_000_000_000.0 / float(base["rate_hz"])))
                    rel = int(tick_val) - int(base["tick"])
                    ts_base_ns = int(base["ts"]) + (rel * step_ns)
                except Exception:
                    ts_base_ns = int(raw_ts_ns)

            ts_ns = int(ts_base_ns) + int(time_offset_ns)
            if ts_ns <= 0:
                continue

            # Keep point identity consistent with live stream writer for points created in this run.
            key = (node_tag, channel, ts_ns)
            dup_idx = point_key_counts.get(key, 0)
            point_key_counts[key] = dup_idx + 1
            if dup_idx:
                ts_ns += dup_idx

            rng = channel_ranges.get(channel)
            if rng is None:
                channel_ranges[channel] = [ts_ns, ts_ns]
            else:
                if ts_ns < rng[0]:
                    rng[0] = ts_ns
                if ts_ns > rng[1]:
                    rng[1] = ts_ns
            raw_rng = raw_channel_ranges.get(channel)
            if raw_rng is None:
                raw_channel_ranges[channel] = [raw_ts_ns, raw_ts_ns]
            else:
                if raw_ts_ns < raw_rng[0]:
                    raw_rng[0] = raw_ts_ns
                if raw_ts_ns > raw_rng[1]:
                    raw_rng[1] = raw_ts_ns
            candidates.append((channel, ts_ns, value, raw_ts_ns, tick_val))

        existing_by_channel = {}
        existing_raw_by_channel = {}
        bucket_q = json.dumps(INFLUX_BUCKET)
        measurement_q = json.dumps(MSCL_MEASUREMENT)
        node_q = json.dumps(node_tag)
        source_q = json.dumps(source_tag)

        for channel, rng in channel_ranges.items():
            start_ns = int(rng[0])
            stop_ns = int(rng[1]) + 1
            start_iso = _ns_to_iso_utc(start_ns)
            stop_iso = _ns_to_iso_utc(stop_ns)
            if not start_iso or not stop_iso:
                existing_by_channel[channel] = set()
                continue
            ch_q = json.dumps(str(channel))
            start_q = json.dumps(start_iso)
            stop_q = json.dumps(stop_iso)
            flux = (
                f'from(bucket: {bucket_q})\n'
                f'  |> range(start: time(v: {start_q}), stop: time(v: {stop_q}))\n'
                f'  |> filter(fn: (r) => r._measurement == {measurement_q})\n'
                f'  |> filter(fn: (r) => r._field == "value")\n'
                f'  |> filter(fn: (r) => r.node_id == {node_q})\n'
                f'  |> filter(fn: (r) => r.channel == {ch_q})\n'
                f'  |> filter(fn: (r) => r.source == {source_q})\n'
                f'  |> map(fn: (r) => ({{ r with _value: uint(v: r._time) }}))\n'
                f'  |> keep(columns: ["_value"])'
            )
            exists = set()
            for rec in query_api.query_stream(query=flux, org=INFLUX_ORG):
                try:
                    exists.add(int(rec.get_value()))
                except Exception:
                    continue
            existing_by_channel[channel] = exists

        # Protect add-only semantics even when export clock offset changes:
        # dedupe by original node timestamp + sweep tick (if available).
        for channel, rng in raw_channel_ranges.items():
            raw_start_ns = int(rng[0])
            raw_stop_ns = int(rng[1])
            ch_q = json.dumps(str(channel))
            flux = (
                f'from(bucket: {bucket_q})\n'
                f'  |> range(start: -3650d)\n'
                f'  |> filter(fn: (r) => r._measurement == {measurement_q})\n'
                f'  |> filter(fn: (r) => r._field == "node_ts_raw_ns" or r._field == "node_tick")\n'
                f'  |> filter(fn: (r) => r.node_id == {node_q})\n'
                f'  |> filter(fn: (r) => r.channel == {ch_q})\n'
                f'  |> filter(fn: (r) => r.source == {source_q})\n'
                f'  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")\n'
                f'  |> filter(fn: (r) => exists r.node_ts_raw_ns)\n'
                f'  |> filter(fn: (r) => r.node_ts_raw_ns >= {raw_start_ns}.0 and r.node_ts_raw_ns <= {raw_stop_ns}.0)\n'
                f'  |> keep(columns: ["node_ts_raw_ns", "node_tick"])'
            )
            raw_exists = {"pairs": set(), "raws": set()}
            for rec in query_api.query_stream(query=flux, org=INFLUX_ORG):
                try:
                    vals = getattr(rec, "values", {}) or {}
                    raw_v = vals.get("node_ts_raw_ns")
                    if raw_v is None:
                        continue
                    raw_i = int(float(raw_v))
                    raw_exists["raws"].add(raw_i)
                    tick_v = vals.get("node_tick")
                    if tick_v is not None:
                        try:
                            raw_exists["pairs"].add((raw_i, int(float(tick_v))))
                        except Exception:
                            pass
                except Exception:
                    continue
            existing_raw_by_channel[channel] = raw_exists

        for channel, ts_ns, value, raw_ts_ns, tick_val in candidates:
            exists = existing_by_channel.get(channel)
            raw_exists = existing_raw_by_channel.get(channel)
            if raw_exists is not None:
                raw_i = int(raw_ts_ns)
                if tick_val is not None and (raw_i, int(tick_val)) in raw_exists["pairs"]:
                    total_skipped_existing += 1
                    continue
                if tick_val is None and raw_i in raw_exists["raws"]:
                    total_skipped_existing += 1
                    continue
            if exists is not None and ts_ns in exists:
                total_skipped_existing += 1
                continue

            point = (
                Point(MSCL_MEASUREMENT)
                .tag("node_id", node_tag)
                .tag("channel", channel)
                .tag("source", source_tag)
                .tag("time_alignment", "node_to_host")
                .field("value", value)
                .field("node_ts_raw_ns", int(raw_ts_ns))
                .field("clock_offset_ns", int(time_offset_ns))
                .time(ts_ns, WritePrecision.NS)
            )
            if tick_val is not None:
                point = point.field("node_tick", int(tick_val))
            batch.append(point)
            if exists is not None:
                exists.add(ts_ns)
            if raw_exists is not None:
                raw_i = int(raw_ts_ns)
                raw_exists["raws"].add(raw_i)
                if tick_val is not None:
                    raw_exists["pairs"].add((raw_i, int(tick_val)))

            if len(batch) >= max(1, int(MSCL_EXPORT_INFLUX_BATCH)):
                write_api.write(INFLUX_BUCKET, INFLUX_ORG, batch)
                total_written += len(batch)
                batch = []

        if batch:
            write_api.write(INFLUX_BUCKET, INFLUX_ORG, batch)
            total_written += len(batch)

    return {"written": int(total_written), "skipped_existing": int(total_skipped_existing)}


def _stream_loop():
    if not MSCL_STREAM_ENABLED:
        log("[mscl-stream] Disabled via MSCL_STREAM_ENABLED")
        return
    if not all([INFLUX_TOKEN, INFLUX_ORG, INFLUX_BUCKET]):
        log("[mscl-stream] Missing INFLUX_TOKEN / INFLUX_ORG / INFLUX_BUCKET; stream disabled")
        return

    log(f"[mscl-stream] Influx target: {INFLUX_URL} bucket={INFLUX_BUCKET} org={INFLUX_ORG} measurement={MSCL_MEASUREMENT}")
    db_client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
    write_api = db_client.write_api(
        write_options=WriteOptions(
            batch_size=MSCL_STREAM_BATCH_SIZE,
            flush_interval=MSCL_STREAM_FLUSH_INTERVAL_MS,
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

    def _note_diag(channel, value):
        last_diag[channel] = value

    def _maybe_log_batch(now_ts, channel_counts, point_count):
        nonlocal last_batch_log_ts
        if (now_ts - last_batch_log_ts) < MSCL_STREAM_LOG_INTERVAL_SEC:
            return
        last_batch_log_ts = now_ts
        channels_txt = ", ".join(f"{k}:{v}" for k, v in sorted(channel_counts.items()))
        log(f"[mscl-stream] Logged {point_count} points ({channels_txt})")

    def _packet_rate_label(packet):
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

    def _maybe_log_drop(now_ts):
        nonlocal last_drop_log_ts
        if last_ch1_ts <= 0:
            return
        gap = now_ts - last_ch1_ts
        if gap < MSCL_STREAM_DROP_WARN_SEC:
            return
        if now_ts - last_drop_log_ts < MSCL_STREAM_DROP_LOG_THROTTLE_SEC:
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
        log(f"[mscl-stream] Warning: no ch1 data for {gap:.1f}s; {diag_summary}")

    def _reader_loop():
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
                        time.sleep(MSCL_STREAM_IDLE_SLEEP)
                        continue
                    packets = base_station.getData(MSCL_STREAM_READ_TIMEOUT_MS)

                if not packets:
                    backoff = 1.0
                    time.sleep(MSCL_STREAM_IDLE_SLEEP)
                    continue

                with queue_cond:
                    packet_queue.extend(packets)
                    dropped = 0
                    while len(packet_queue) > MSCL_STREAM_QUEUE_MAX:
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
                log(f"[mscl-stream] Reader error: {e}")
                metric_inc("stream_reader_errors")
                mark_base_disconnected()
                disconnected = True
                time.sleep(backoff)
                backoff = min(backoff_max, backoff * 1.7)

    threading.Thread(target=_reader_loop, daemon=True).start()

    while True:
        try:
            with queue_cond:
                if not packet_queue:
                    queue_cond.wait(timeout=MSCL_STREAM_QUEUE_WAIT_MS / 1000.0)
                packets = []
                while packet_queue:
                    packets.append(packet_queue.popleft())

            if not packets:
                time.sleep(MSCL_STREAM_IDLE_SLEEP)
                continue

            points = []
            channel_counts = {}
            packet_rate_counts = {}
            point_key_counts = {}
            for packet in packets:
                node_address = str(packet.nodeAddress())
                rate_lbl = _packet_rate_label(packet)
                packet_rate_counts[rate_lbl] = packet_rate_counts.get(rate_lbl, 0) + 1
                for dp in packet.data():
                    channel = _point_channel(dp)
                    if MSCL_ONLY_CHANNEL_1 and channel not in ("channel_1", "ch1"):
                        continue
                    value = _point_value(dp)
                    if value is None:
                        continue
                    if channel.startswith("diagnostic_"):
                        _note_diag(channel, value)
                    t_ns = _point_time_ns(dp)
                    # Avoid overwriting points that share identical timestamp/tag set.
                    key = (node_address, channel, t_ns)
                    dup_idx = point_key_counts.get(key, 0)
                    point_key_counts[key] = dup_idx + 1
                    if dup_idx:
                        t_ns += dup_idx
                    point = (
                        Point(MSCL_MEASUREMENT)
                        .tag("node_id", node_address)
                        .tag("channel", channel)
                        .tag("source", MSCL_SOURCE_RADIO)
                        .field("value", value)
                        .time(t_ns, WritePrecision.NS)
                    )
                    points.append(point)
                    channel_counts[channel] = channel_counts.get(channel, 0) + 1
                    if channel in ("channel_1", "ch1"):
                        last_ch1_ts = time.time()

            if points:
                write_api.write(INFLUX_BUCKET, INFLUX_ORG, points)
                metric_inc("stream_write_calls")
                metric_inc("stream_points_written", len(points))
                _maybe_log_batch(time.time(), channel_counts, len(points))
                if packet_rate_counts:
                    rate_txt = ", ".join(f"{k}:{v}" for k, v in sorted(packet_rate_counts.items()))
                    log(f"[mscl-stream] Packet rates ({rate_txt})")
            _maybe_log_drop(time.time())

        except Exception as e:
            log(f"[mscl-stream] Writer error: {e}")
            metric_inc("stream_writer_errors")
            time.sleep(MSCL_STREAM_IDLE_SLEEP)


def _start_streamer():
    t = threading.Thread(target=_stream_loop, daemon=True)
    t.start()


def send_idle_sensorconnect_style(node, node_id, stage_tag):
    """Set-to-idle flow from official example: setToIdle -> complete() -> result()."""
    command_sent = False
    transport_alive = False
    state_confirmed = False
    state_text = None
    last_reason = "not completed"
    idle_result = "pending"

    try:
        status = node.setToIdle()
        command_sent = True
        log(f"[mscl-web] [PREP-IDLE] {stage_tag} node setToIdle started node_id={node_id}")
    except Exception as e:
        last_reason = f"setToIdle failed: {e}"
        idle_result = "failed"
        log(f"[mscl-web] [PREP-IDLE] {stage_tag} node setToIdle failed node_id={node_id}: {e}")
        return {
            "command_sent": command_sent,
            "transport_alive": transport_alive,
            "state_confirmed": state_confirmed,
            "state_text": state_text,
            "reason": last_reason,
            "idle_result": idle_result,
        }

    complete = False
    for poll in range(1, 41):  # about 12s max (40 * 300ms)
        try:
            if status.complete(300):
                complete = True
                transport_alive = True
                break
            # Keep logs concise: report only at coarse milestones.
            if poll in (10, 20, 30, 40):
                log(f"[mscl-web] [PREP-IDLE] {stage_tag} waiting node_id={node_id} poll {poll}/40")
        except Exception as e:
            last_reason = f"status.complete failed: {e}"
            log(f"[mscl-web] [PREP-IDLE] {stage_tag} wait failed node_id={node_id} poll {poll}/40 ({last_reason})")
            break

    if not complete:
        return {
            "command_sent": command_sent,
            "transport_alive": transport_alive,
            "state_confirmed": False,
            "state_text": state_text,
            "reason": last_reason,
            "idle_result": idle_result,
        }

    try:
        result = status.result()
        success_val = getattr(mscl.SetToIdleStatus, "setToIdleResult_success", None)
        canceled_val = getattr(mscl.SetToIdleStatus, "setToIdleResult_canceled", None)

        if result == success_val:
            state_confirmed = True
            state_text = "Idle"
            last_reason = "confirmed:status.result=success"
            idle_result = "success"
            log(f"[mscl-web] [PREP-IDLE] {stage_tag} confirmed node_id={node_id} by status.result")
        elif canceled_val is not None and result == canceled_val:
            last_reason = "status.result=canceled"
            idle_result = "canceled"
            log(f"[mscl-web] [PREP-IDLE] {stage_tag} canceled node_id={node_id}")
        else:
            last_reason = f"status.result={result}"
            idle_result = "failed"
            log(f"[mscl-web] [PREP-IDLE] {stage_tag} not-confirmed node_id={node_id} ({last_reason})")
    except Exception as e:
        last_reason = f"status.result failed: {e}"
        idle_result = "failed"
        log(f"[mscl-web] [PREP-IDLE] {stage_tag} result read failed node_id={node_id}: {e}")

    return {
        "command_sent": command_sent,
        "transport_alive": transport_alive,
        "state_confirmed": state_confirmed,
        "state_text": state_text,
        "reason": last_reason,
        "idle_result": idle_result,
    }

def _start_sampling_best_effort(node, node_id):
    """Try primary sync start, then sync-resend, then non-sync fallback."""
    errors = []
    try:
        if callable(getattr(node, "startSyncSampling", None)):
            node.startSyncSampling()
            log(f"[mscl-web] [SAMPLE] startSyncSampling sent node_id={node_id}")
            return "sync"
    except Exception as e:
        log(f"[mscl-web] [SAMPLE] startSyncSampling failed node_id={node_id}: {e}")
        errors.append(f"startSync={e}")
    try:
        if callable(getattr(node, "startNonSyncSampling", None)):
            node.startNonSyncSampling()
            log(f"[mscl-web] [SAMPLE] startNonSyncSampling sent node_id={node_id}")
            return "non-sync"
    except Exception as e:
        log(f"[mscl-web] [SAMPLE] startNonSyncSampling failed node_id={node_id}: {e}")
        errors.append(f"non-sync={e}")
    try:
        if callable(getattr(node, "resendStartSyncSampling", None)):
            node.resendStartSyncSampling()
            log(f"[mscl-web] [SAMPLE] resendStartSyncSampling sent node_id={node_id}")
            return "sync-resend"
    except Exception as e:
        log(f"[mscl-web] [SAMPLE] resendStartSyncSampling failed node_id={node_id}: {e}")
        errors.append(f"resendSync={e}")
    raise RuntimeError("; ".join(errors) if errors else "No sampling start method available")


def _start_sampling_via_sync_network(node, node_id):
    """Prefer SensorConnect-like sync network start when available."""
    errors = []
    sync_cls = getattr(mscl, "SyncSamplingNetwork", None)
    if sync_cls is None:
        raise RuntimeError("SyncSamplingNetwork is not available in MSCL")

    def _mk():
        return sync_cls(state.BASE_STATION)

    def _try(label, fn):
        try:
            fn()
            log(f"[mscl-web] [SAMPLE] {label} sent node_id={node_id}")
            return True
        except Exception as e:
            errors.append(f"{label}={e}")
            log(f"[mscl-web] [SAMPLE] {label} failed node_id={node_id}: {e}")
            return False

    # Attempt 1: standard network apply + start.
    def _a1():
        net = _mk()
        try:
            cached = state.NODE_READ_CACHE.get(int(node_id), {})
            cp = int(cached.get("comm_protocol")) if isinstance(cached, dict) and cached.get("comm_protocol") is not None else None
            if cp is None:
                try:
                    cp = int(node.communicationProtocol())
                except Exception:
                    cp = 1
            net.communicationProtocol(int(cp))
            log(f"[mscl-web] [SAMPLE] sync-network set commProtocol node_id={node_id}: {cp}")
        except Exception as e:
            log(f"[mscl-web] [SAMPLE] sync-network set commProtocol failed node_id={node_id}: {e}")
        try:
            net.lossless(True)
            log(f"[mscl-web] [SAMPLE] sync-network set lossless node_id={node_id}: True")
        except Exception as e:
            log(f"[mscl-web] [SAMPLE] sync-network set lossless failed node_id={node_id}: {e}")
        net.addNode(node)
        try:
            net.refresh()
        except Exception:
            pass
        net.applyConfiguration()
        try:
            net_ok = bool(net.ok()) if callable(getattr(net, "ok", None)) else None
            net_bw = float(net.percentBandwidth()) if callable(getattr(net, "percentBandwidth", None)) else None
            ninfo = None
            try:
                ninfo = net.getNodeNetworkInfo(int(node_id))
            except Exception:
                ninfo = None
            if ninfo is not None:
                try:
                    ns = int(ninfo.status())
                except Exception:
                    ns = None
                try:
                    nbw = float(ninfo.percentBandwidth())
                except Exception:
                    nbw = None
                try:
                    tdma = int(ninfo.tdmaAddress())
                except Exception:
                    tdma = None
                log(
                    f"[mscl-web] [SAMPLE] sync-network info node_id={node_id}: "
                    f"net_ok={net_ok} net_bw={net_bw} node_status={ns} node_bw={nbw} tdma={tdma}"
                )
            else:
                log(f"[mscl-web] [SAMPLE] sync-network info node_id={node_id}: net_ok={net_ok} net_bw={net_bw}")
        except Exception as e:
            log(f"[mscl-web] [SAMPLE] sync-network info read failed node_id={node_id}: {e}")
        try:
            if callable(getattr(net, "ok", None)) and (not bool(net.ok())):
                raise RuntimeError("SyncSamplingNetwork not OK after applyConfiguration")
        except Exception:
            raise
        net.startSampling()

    if _try("sync-network(startSampling)", _a1):
        return "sync-network"

    # Attempt 2: start without beacon path.
    def _a2():
        net = _mk()
        try:
            cached = state.NODE_READ_CACHE.get(int(node_id), {})
            cp = int(cached.get("comm_protocol")) if isinstance(cached, dict) and cached.get("comm_protocol") is not None else None
            if cp is None:
                try:
                    cp = int(node.communicationProtocol())
                except Exception:
                    cp = 1
            net.communicationProtocol(int(cp))
        except Exception:
            pass
        try:
            net.lossless(True)
        except Exception:
            pass
        net.addNode(node)
        try:
            net.refresh()
        except Exception:
            pass
        net.applyConfiguration()
        try:
            if callable(getattr(net, "ok", None)) and (not bool(net.ok())):
                raise RuntimeError("SyncSamplingNetwork not OK after applyConfiguration")
        except Exception:
            raise
        net.startSampling_noBeacon()

    if _try("sync-network(startSampling_noBeacon)", _a2):
        return "sync-network-no-beacon"

    raise RuntimeError("; ".join(errors) if errors else "SyncSamplingNetwork start failed")

def _schedule_idle_after(node_id, seconds, token):
    if seconds <= 0:
        return
    time.sleep(seconds)
    with state.OP_LOCK:
        if state.SAMPLE_STOP_TOKENS.get(node_id) != token:
            return
        ok, msg = internal_connect()
        if not ok or state.BASE_STATION is None:
            log(f"[mscl-web] [SAMPLE] auto-idle skipped node_id={node_id}: {msg}")
            run = state.SAMPLE_RUNS.get(node_id, {})
            run.update({
                "auto_idle_confirmed": False,
                "auto_idle_last_reason": msg,
                "auto_idle_attempts": 0,
                "auto_idle_at": int(time.time()),
            })
            state.SAMPLE_RUNS[node_id] = run
            return
        try:
            ensure_beacon_on()
            node = mscl.WirelessNode(node_id, state.BASE_STATION)
            node.readWriteRetries(15)

            idle_status = {}
            confirmed = False
            attempts_done = 0
            for attempt in range(1, 4):
                attempts_done = attempt
                idle_status = send_idle_sensorconnect_style(node, node_id, f"auto-idle#{attempt}")
                if bool(idle_status.get("state_confirmed")):
                    confirmed = True
                    break
                # Fallback state check when SetToIdle status polling is inconclusive.
                try:
                    _, st_txt, _ = _node_state_info(node)
                    if str(st_txt or "").strip().lower() == "idle":
                        confirmed = True
                        idle_status = dict(idle_status or {})
                        idle_status["state_confirmed"] = True
                        idle_status["reason"] = "confirmed by node state"
                        break
                except Exception:
                    pass
                if attempt < 3:
                    time.sleep(0.8 * attempt)

            run = state.SAMPLE_RUNS.get(node_id, {})
            if confirmed:
                run.update({
                    "state": "stopped",
                    "stopped_at": int(time.time()),
                    "idle_result": idle_status.get("idle_result"),
                    "stop_reason": "auto-idle",
                    "auto_idle_confirmed": True,
                    "auto_idle_last_reason": idle_status.get("reason"),
                    "auto_idle_attempts": attempts_done,
                    "auto_idle_at": int(time.time()),
                })
                state.SAMPLE_RUNS[node_id] = run
                log(
                    f"[mscl-web] [SAMPLE] auto-idle confirmed node_id={node_id} "
                    f"after {seconds}s attempts={attempts_done} reason={idle_status.get('reason')}"
                )
            else:
                run.update({
                    "auto_idle_confirmed": False,
                    "auto_idle_last_reason": idle_status.get("reason"),
                    "auto_idle_attempts": attempts_done,
                    "auto_idle_at": int(time.time()),
                })
                state.SAMPLE_RUNS[node_id] = run
                log(
                    f"[mscl-web] [SAMPLE] auto-idle not confirmed node_id={node_id} "
                    f"after {seconds}s attempts={attempts_done} reason={idle_status.get('reason')}"
                )
        except Exception as e:
            log(f"[mscl-web] [SAMPLE] auto-idle failed node_id={node_id}: {e}")
            run = state.SAMPLE_RUNS.get(node_id, {})
            run.update({
                "auto_idle_confirmed": False,
                "auto_idle_last_reason": str(e),
                "auto_idle_at": int(time.time()),
            })
            state.SAMPLE_RUNS[node_id] = run


def _sampling_duration_to_seconds(duration_value, duration_units, continuous):
    if continuous:
        return 0
    try:
        value = float(duration_value)
    except Exception:
        value = 0.0
    if value < 0:
        value = 0.0
    unit = str(duration_units or "seconds").lower()
    mult = 1.0
    if unit.startswith("min"):
        mult = 60.0
    elif unit.startswith("hour"):
        mult = 3600.0
    seconds = int(value * mult)
    return max(0, min(seconds, 86400))


def _is_sampling_active(node_id):
    run = state.SAMPLE_RUNS.get(node_id)
    if not run or run.get("state") != "running":
        return False
    duration_sec = int(run.get("duration_sec") or 0)
    if duration_sec <= 0:
        return True
    started_at = int(run.get("started_at") or 0)
    if started_at <= 0:
        return True
    return (time.time() - started_at) < duration_sec


def _set_sampling_mode_on_node(cfg, mode_key):
    mode_key = str(mode_key or "transmit").lower()
    mode_value = SAMPLING_MODE_MAP.get(mode_key, SAMPLING_MODE_MAP["transmit"])
    errs = []
    try:
        cfg.dataCollectionMethod(int(mode_value))
        return mode_value, None
    except Exception as e:
        errs.append(str(e))

    # Compatibility fallback for nodes that only expose dataMode in config.
    # Keep this fallback for transmit only to avoid mapping log-modes to derived data.
    if mode_key == "transmit":
        try:
            cfg.dataMode(int(getattr(mscl.WirelessTypes, "dataMode_raw", 1)))
            return mode_value, None
        except Exception as e:
            errs.append(str(e))
    return None, " | ".join(errs) if errs else "no setter available"


def _is_tc_link_200_model(model):
    s = str(model or "").strip().lower()
    return ("tc-link-200" in s) or s.startswith("63104100")


def _rate_label_to_hz(label):
    s = str(label or "").strip().lower()
    if "hz" not in s:
        return None
    parts = s.split()
    if not parts:
        return None
    try:
        v = float(parts[0])
    except Exception:
        return None
    if "khz" in s:
        return v * 1000.0
    return v


def _rate_label_to_interval_seconds(label):
    s = str(label or "").strip().lower()
    if not s.startswith("every "):
        return None
    parts = s.split()
    if len(parts) < 3:
        return None
    try:
        v = float(parts[1])
    except Exception:
        return None
    unit = parts[2]
    if unit.startswith("second"):
        return v
    if unit.startswith("minute"):
        return v * 60.0
    if unit.startswith("hour"):
        return v * 3600.0
    return None


def _filter_sample_rates_for_model(model, supported_rates, current_rate):
    rates = []
    seen = set()
    for r in list(supported_rates or []):
        try:
            rid = int(r.get("enum_val"))
        except Exception:
            continue
        if rid in seen:
            continue
        seen.add(rid)
        rates.append({"enum_val": rid, "str_val": str(r.get("str_val") or RATE_MAP.get(rid, f"Value {rid}"))})

    def _allowed_tc200_oem(rate_item):
        allowed_interval_sec = {2, 5, 10, 30, 60, 120, 300, 600, 1800, 3600}
        try:
            rid = int(rate_item.get("enum_val"))
        except Exception:
            rid = None
        lbl = str(rate_item.get("str_val") or "").strip()
        hz = _rate_label_to_hz(lbl)
        interval_sec = _rate_label_to_interval_seconds(lbl)
        if rid is not None and rid in TC_LINK_200_RATE_ENUMS:
            return True
        if hz is not None and hz <= 128.0:
            return True
        if interval_sec is not None and int(interval_sec) in allowed_interval_sec:
            return True
        return False

    if _is_tc_link_200_model(model):
        rates = [x for x in rates if _allowed_tc200_oem(x)]
        rates.sort(
            key=lambda x: (
                0 if _rate_label_to_hz(x.get("str_val")) is not None else (1 if _rate_label_to_interval_seconds(x.get("str_val")) is not None else 2),
                -(_rate_label_to_hz(x.get("str_val")) or 0.0),
                _rate_label_to_interval_seconds(x.get("str_val")) or 0.0,
                str(x.get("str_val") or ""),
            )
        )

    # Keep currently reported rate visible even if it is outside list from features.
    if current_rate is not None:
        try:
            cur = int(current_rate)
            cur_item = {"enum_val": cur, "str_val": RATE_MAP.get(cur, f"Value {cur}")}
            if _is_tc_link_200_model(model) and not _allowed_tc200_oem(cur_item):
                return rates
            if all(int(x.get("enum_val")) != cur for x in rates if x.get("enum_val") is not None):
                rates.insert(0, cur_item)
        except Exception:
            pass
    return rates


def _sample_rate_label(rate_enum, rate_obj=None):
    try:
        rid = int(rate_enum)
    except Exception:
        rid = None

    # Prefer MSCL object text when available (e.g. "every 30 seconds"),
    # but ignore plain numeric echoes like "107".
    txt = str(rate_obj if rate_obj is not None else "").strip()
    if txt and txt.lower() not in ("none", "null"):
        txt_l = txt.lower()
        numeric_echo = False
        if rid is not None:
            try:
                numeric_echo = int(float(txt_l)) == rid and txt_l.replace(".", "", 1).isdigit()
            except Exception:
                numeric_echo = False
        if not numeric_echo:
            return txt

    if rid is not None and rid in RATE_MAP:
        return RATE_MAP[rid]
    if rid is not None:
        return f"Value {rid}"
    return "N/A"


def _is_tc_link_200_oem_model(model):
    return "tc-link-200-oem" in str(model or "").strip().lower()


def _filter_default_modes_for_model(model, default_mode_options, current_default_mode=None):
    opts = []
    for item in list(default_mode_options or []):
        try:
            vi = int(item.get("value"))
        except Exception:
            continue
        label = str(item.get("label") or DEFAULT_MODE_LABELS.get(vi, f"Value {vi}"))
        if vi == 6:
            label = "Sample"
        opts.append({"value": vi, "label": label})

    if _is_tc_link_200_oem_model(model):
        allowed = {0, 5, 6}
        opts = [x for x in opts if int(x.get("value")) in allowed]
        order = {0: 0, 5: 1, 6: 2}
        opts.sort(key=lambda x: order.get(int(x.get("value")), 99))

    if current_default_mode is not None:
        try:
            cur = int(current_default_mode)
            if all(int(x.get("value")) != cur for x in opts):
                label = "Sample" if cur == 6 else DEFAULT_MODE_LABELS.get(cur, f"Value {cur}")
                opts.insert(0, {"value": cur, "label": label})
        except Exception:
            pass
    return opts


def _tx_power_options_for_model(model, current_power=None):
    base = [16, 10, 5, 0]
    if _is_tc_link_200_oem_model(model):
        base = [10, 5, 0]

    opts = [{"value": p, "label": f"{p} dBm"} for p in base]
    if current_power is not None:
        try:
            cur = int(current_power)
            if all(int(x.get("value")) != cur for x in opts):
                opts.insert(0, {"value": cur, "label": f"{cur} dBm"})
        except Exception:
            pass
    return opts


def _start_sampling_run(node_id, body):
    ok, msg = internal_connect()
    if not ok or state.BASE_STATION is None:
        return {"success": False, "error": f"Base station not connected: {msg}"}

    mode_key = str(body.get("log_transmit_mode") or "transmit").lower()
    # Current product scope: keep only float/raw sampling payload type.
    data_type = "float"
    continuous = bool(body.get("continuous", False))
    duration_value = body.get("duration_value", 60)
    duration_units = body.get("duration_units", "seconds")
    duration_sec = _sampling_duration_to_seconds(duration_value, duration_units, continuous)
    sample_rate = body.get("sample_rate")
    if sample_rate is not None and str(sample_rate) != "":
        try:
            sample_rate = int(sample_rate)
        except Exception:
            sample_rate = None
    else:
        sample_rate = None

    try:
        ensure_beacon_on()
        node = mscl.WirelessNode(node_id, state.BASE_STATION)
        node.readWriteRetries(10)
        cfg = mscl.WirelessNodeConfig()
        # Keep runtime sampling config explicit and minimal.
        try:
            cfg.samplingMode(mscl.WirelessTypes.samplingMode_sync)
        except Exception:
            pass
        try:
            cfg.unlimitedDuration(True)
        except Exception:
            pass
        # Preserve active channels for runtime start; default config may enable
        # extra channels and reduce max supported sample rate on this node.
        try:
            active_mask = mscl.ChannelMask()
            enabled = []
            cached = state.NODE_READ_CACHE.get(int(node_id), {})
            ch_list = cached.get("channels") if isinstance(cached, dict) else None
            if isinstance(ch_list, list) and ch_list:
                for ch in ch_list:
                    try:
                        cid = int(ch.get("id"))
                        cen = bool(ch.get("enabled"))
                    except Exception:
                        continue
                    if cen and cid in (1, 2):
                        enabled.append(cid)
            if not enabled:
                try:
                    cur_mask = node.getActiveChannels()
                    for cid in (1, 2):
                        try:
                            if cur_mask.enabled(cid):
                                enabled.append(cid)
                        except Exception:
                            continue
                except Exception:
                    pass
            if not enabled:
                enabled = [1]
            for cid in enabled:
                active_mask.enable(int(cid))
            cfg.activeChannels(active_mask)
        except Exception as e:
            log(f"[mscl-web] [S-RUN] activeChannels not set node_id={node_id}: {e}")

        sample_rate_set = False
        if sample_rate is not None:
            try:
                cfg.sampleRate(int(sample_rate))
                sample_rate_set = True
            except Exception as e:
                log(f"[mscl-web] [S-RUN] sampleRate not set node_id={node_id}: {e}")

        mode_value, mode_err = _set_sampling_mode_on_node(cfg, mode_key)
        if mode_value is None:
            return {
                "success": False,
                "error": f"Log/Transmit mode not set: {mode_err}",
                "rate": sample_rate,
                "mode": mode_key,
            }

        apply_ok = False
        apply_err = None
        try:
            # SensorConnect-like flow:
            # 1) verify/apply node config
            # 2) add node to sync network
            # 3) apply network configuration
            # 4) start network sampling
            try:
                issues = mscl.ConfigIssues()
                if not node.verifyConfig(cfg, issues):
                    issue_texts = []
                    try:
                        for issue in issues:
                            issue_texts.append(str(issue.description()))
                    except Exception:
                        pass
                    joined = "; ".join([t for t in issue_texts if t]) or "verifyConfig returned false"
                    return {
                        "success": False,
                        "error": f"Sampling config verify failed: {joined}",
                        "rate": sample_rate,
                        "mode": mode_key,
                    }
            except Exception as e:
                log(f"[mscl-web] [S-RUN] verifyConfig warning node_id={node_id}: {e}")

            node.applyConfig(cfg)
            try:
                eff_rate = int(node.getSampleRate())
                log(f"[mscl-web] [S-RUN] post-apply sampleRate node_id={node_id}: {eff_rate} ({RATE_MAP.get(eff_rate, 'unknown')})")
            except Exception as e:
                log(f"[mscl-web] [S-RUN] post-apply sampleRate read failed node_id={node_id}: {e}")
            try:
                dm = int(node.getDataMode())
                log(f"[mscl-web] [S-RUN] post-apply dataMode node_id={node_id}: {dm}")
            except Exception as e:
                log(f"[mscl-web] [S-RUN] post-apply dataMode read failed node_id={node_id}: {e}")
            try:
                cm = int(node.getDataCollectionMethod())
                cm_txt = next((k for k, v in SAMPLING_MODE_MAP.items() if int(v) == cm), f"value:{cm}")
                log(f"[mscl-web] [S-RUN] post-apply collectionMethod node_id={node_id}: {cm} ({cm_txt})")
            except Exception as e:
                log(f"[mscl-web] [S-RUN] post-apply collectionMethod read failed node_id={node_id}: {e}")
            try:
                sm = node.getSamplingMode()
                log(f"[mscl-web] [S-RUN] post-apply samplingMode node_id={node_id}: {sm}")
            except Exception as e:
                log(f"[mscl-web] [S-RUN] post-apply samplingMode read failed node_id={node_id}: {e}")
            start_mode = _start_sampling_via_sync_network(node, node_id)
            try:
                eff_rate2 = int(node.getSampleRate())
                log(f"[mscl-web] [S-RUN] post-start sampleRate node_id={node_id}: {eff_rate2} ({RATE_MAP.get(eff_rate2, 'unknown')})")
            except Exception as e:
                log(f"[mscl-web] [S-RUN] post-start sampleRate read failed node_id={node_id}: {e}")
            try:
                dm2 = int(node.getDataMode())
                log(f"[mscl-web] [S-RUN] post-start dataMode node_id={node_id}: {dm2}")
            except Exception as e:
                log(f"[mscl-web] [S-RUN] post-start dataMode read failed node_id={node_id}: {e}")
            try:
                cm2 = int(node.getDataCollectionMethod())
                cm2_txt = next((k for k, v in SAMPLING_MODE_MAP.items() if int(v) == cm2), f"value:{cm2}")
                log(f"[mscl-web] [S-RUN] post-start collectionMethod node_id={node_id}: {cm2} ({cm2_txt})")
            except Exception as e:
                log(f"[mscl-web] [S-RUN] post-start collectionMethod read failed node_id={node_id}: {e}")
            try:
                sm2 = node.getSamplingMode()
                log(f"[mscl-web] [S-RUN] post-start samplingMode node_id={node_id}: {sm2}")
            except Exception as e:
                log(f"[mscl-web] [S-RUN] post-start samplingMode read failed node_id={node_id}: {e}")
            apply_ok = True
        except Exception as net_err:
            log(f"[mscl-web] [S-RUN] sync-network start failed node_id={node_id}: {net_err}")
            return {
                "success": False,
                "error": f"Sync network start failed: {net_err}",
                "rate": sample_rate,
                "mode": mode_key,
            }
        token = time.time()
        state.SAMPLE_STOP_TOKENS[node_id] = token
        run = {
            "run_id": f"{node_id}-{int(token)}",
            "state": "running",
            "started_at": int(time.time()),
            "duration_sec": int(duration_sec),
            "continuous": bool(duration_sec == 0),
            "mode_key": mode_key,
            "mode_label": SAMPLING_MODE_LABELS.get(mode_key, mode_key),
            "mode_value": mode_value,
            "data_type": data_type,
            "sample_rate": sample_rate,
            "sample_rate_set": sample_rate_set,
            "apply_config_ok": apply_ok,
            "start_method": start_mode,
        }
        state.SAMPLE_RUNS[node_id] = run
        if duration_sec > 0:
            t = threading.Thread(target=_schedule_idle_after, args=(node_id, duration_sec, token), daemon=True)
            t.start()
        if sample_rate is not None:
            rate_txt = RATE_MAP.get(int(sample_rate), f"{sample_rate} (unknown)")
            rate_log = f"{sample_rate} ({rate_txt})"
        else:
            rate_log = "keep"
        log(
            f"[mscl-web] [S-RUN] start ok node_id={node_id} mode={run['mode_label']} "
            f"dur={duration_sec}s rate={rate_log}"
        )
        return {"success": True, "run": run}
    except Exception as e:
        log(f"[mscl-web] [S-RUN] start failed node_id={node_id}: {e}")
        return {"success": False, "error": str(e)}

@app.route('/')
def index():
    return render_template("mscl_web_config.html")

@app.route('/api/connect', methods=['POST'])
def api_connect():
    with state.OP_LOCK:
        s, p = internal_connect()
        return jsonify(success=s, port=p)


@app.route('/api/disconnect', methods=['POST'])
def api_disconnect():
    with state.OP_LOCK:
        state.close_base_station()
        state.LAST_BASE_STATUS.update({"connected": False, "message": "Disconnected", "ts": time.strftime("%H:%M:%S")})
        return jsonify(success=True, message="Disconnected")

@app.route('/api/status')
def api_status():
    with state.OP_LOCK:
        ok = state.BASE_STATION is not None
        msg = state.LAST_BASE_STATUS.get("message", "Not connected")
        port = state.LAST_BASE_STATUS.get("port", "N/A")
        now = time.time()
        def _trim_ts(value):
            try:
                s = str(value)
                if "." in s:
                    return s.split(".")[0]
                return s
            except Exception:
                return value
        def _comm_age_sec(value):
            if not value:
                return None
            try:
                s = str(value).split(".")[0]
                dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
                ts_local = dt.timestamp()
                ts_utc = dt.replace(tzinfo=timezone.utc).timestamp()
                return min(abs(now - ts_local), abs(now - ts_utc))
            except Exception:
                return None
        base_model = None
        base_fw = None
        base_serial = None
        base_region = None
        base_radio = None
        base_last_comm = None
        base_link = None
        ping_age_sec = None
        comm_age_sec = None
        link_health = "offline"
        link_health_reason = "No active BaseStation object"
        if ok and state.BASE_STATION is not None:
            try:
                base_model = str(state.BASE_STATION.model())
            except Exception:
                base_model = None
            try:
                base_fw = str(state.BASE_STATION.firmwareVersion())
            except Exception:
                base_fw = None
            try:
                base_serial = str(state.BASE_STATION.serial())
            except Exception:
                try:
                    base_serial = str(state.BASE_STATION.serialNumber())
                except Exception:
                    base_serial = None
            try:
                base_region = str(state.BASE_STATION.regionCode())
            except Exception:
                base_region = None
            try:
                base_radio = str(state.BASE_STATION.frequency())
            except Exception:
                base_radio = None
            try:
                base_last_comm = _trim_ts(state.BASE_STATION.lastCommunicationTime())
            except Exception:
                base_last_comm = None
            try:
                base_link = str(state.BASE_STATION.lastDeviceState())
            except Exception:
                base_link = None
            if state.LAST_PING_OK_TS > 0:
                ping_age_sec = max(0.0, now - state.LAST_PING_OK_TS)
            comm_age_sec = _comm_age_sec(base_last_comm)
            if ping_age_sec is not None and ping_age_sec <= state.PING_TTL_SEC:
                link_health = "healthy"
                link_health_reason = f"Ping fresh ({ping_age_sec:.1f}s)"
            elif ping_age_sec is not None and ping_age_sec <= (state.PING_TTL_SEC * 3):
                link_health = "degraded"
                link_health_reason = f"Ping stale ({ping_age_sec:.1f}s)"
            elif ping_age_sec is not None:
                link_health = "offline"
                link_health_reason = f"No fresh ping ({ping_age_sec:.1f}s)"
            else:
                link_health = "degraded"
                link_health_reason = "No successful ping yet"

            if comm_age_sec is not None:
                if comm_age_sec > 120:
                    link_health = "offline"
                    link_health_reason = f"No base comm {comm_age_sec:.0f}s"
                elif comm_age_sec > 30 and link_health == "healthy":
                    link_health = "degraded"
                    link_health_reason = f"Base comm stale {comm_age_sec:.0f}s"
        return jsonify(
            connected=bool(ok),
            port=port,
            message=msg,
            beacon_state=state.BASE_BEACON_STATE,
            base_connection=f"Serial, {port}, {state.BAUDRATE}" if port and port != "N/A" else None,
            ts=state.LAST_BASE_STATUS.get("ts"),
            base_model=base_model,
            base_fw=base_fw,
            base_serial=base_serial,
            base_region=base_region,
            base_radio=base_radio,
            base_last_comm=base_last_comm,
            base_link=base_link,
            link_health=link_health,
            link_health_reason=link_health_reason,
            ping_age_sec=round(ping_age_sec, 2) if ping_age_sec is not None else None,
            comm_age_sec=round(comm_age_sec, 2) if comm_age_sec is not None else None,
        )

@app.route('/api/reconnect', methods=['POST'])
def api_reconnect():
    with state.OP_LOCK:
        mark_base_disconnected()
        ok, msg = internal_connect(force_ping=True)
        return jsonify(success=bool(ok), message=msg)

@app.route('/api/beacon', methods=['POST'])
def api_beacon():
    with state.OP_LOCK:
        ok, msg = internal_connect()
        if not ok or state.BASE_STATION is None:
            return jsonify(success=False, error=f"Base station not connected: {msg}")
        body = request.json or {}
        requested = body.get("enabled", None)
        if requested is None:
            target = not bool(state.BASE_BEACON_STATE)
        else:
            target = bool(requested)
        try:
            if target:
                state.BASE_STATION.enableBeacon()
                state.BASE_BEACON_STATE = True
                log("[mscl-web] [BEACON] enabled")
                return jsonify(success=True, beacon_state=True, message="Beacon ON")
            disable_methods = ("disableBeacon", "setBeaconOff")
            disabled = False
            for m in disable_methods:
                fn = getattr(state.BASE_STATION, m, None)
                if callable(fn):
                    fn()
                    disabled = True
                    break
            if not disabled:
                return jsonify(success=False, error="Beacon OFF is not supported by this MSCL API build")
            state.BASE_BEACON_STATE = False
            log("[mscl-web] [BEACON] disabled")
            return jsonify(success=True, beacon_state=False, message="Beacon OFF")
        except Exception as e:
            return jsonify(success=False, error=str(e))

@app.route('/api/diagnostics/<int:node_id>')
def api_diagnostics(node_id):
    with state.OP_LOCK:
        ok, msg = internal_connect()
        if not ok or state.BASE_STATION is None:
            return jsonify(success=False, error=f"Base station not connected: {msg}")
        try:
            node = mscl.WirelessNode(node_id, state.BASE_STATION)
            features = node.features()
            flags = [
                ("supportsInputRange", "supportsInputRange"),
                ("supportsLowPassFilter", "supportsLowPassFilter"),
                ("supportsCommunicationProtocol", "supportsCommunicationProtocol"),
                ("supportsTempSensorOptions", "supportsTempSensorOptions"),
            ]
            out = []
            for label, fn in flags:
                try:
                    out.append({"name": label, "value": bool(getattr(features, fn)())})
                except Exception:
                    out.append({"name": label, "value": False})
            try:
                raw_modes = []
                try:
                    raw_modes = features.dataModes()
                except Exception:
                    raw_modes = []
                mode_parts = []
                for m in raw_modes:
                    mi = int(m)
                    mode_parts.append(f"{mi} ({DATA_MODE_LABELS.get(mi, f'Value {mi}')})")
                out.append({
                    "name": "supportedDataModes",
                    "value": ", ".join(mode_parts) if mode_parts else "N/A",
                })
            except Exception:
                out.append({"name": "supportedDataModes", "value": "N/A"})
            return jsonify(success=True, flags=out)
        except Exception as e:
            return jsonify(success=False, error=str(e))

@app.route('/api/logs')
def api_logs():
    return jsonify(logs=state.LOG_BUFFER[-state.LOG_MAX:])

@app.route('/api/metrics')
def api_metrics():
    metrics = metric_snapshot()
    metrics["node_cache_size"] = len(state.NODE_READ_CACHE)
    metrics["sampling_runs_count"] = len(state.SAMPLE_RUNS)
    metrics["idle_in_progress_count"] = len(state.IDLE_IN_PROGRESS)
    metrics["base_connected"] = bool(state.BASE_STATION is not None)
    metrics["base_port"] = state.CURRENT_PORT
    return jsonify(metrics=metrics)

@app.route('/api/read/<int:node_id>')
def api_read(node_id):
    read_tag = "READ"
    max_attempts = 5
    last_err = None
    log(f"[mscl-web] [{read_tag}] request node_id={node_id}")
    with state.OP_LOCK:
        cached = state.NODE_READ_CACHE.get(node_id, {})
        refresh_eeprom = True
        for attempt in range(1, max_attempts + 1):
            if attempt > 1:
                log(f"[mscl-web] [{read_tag}] retry {attempt}/{max_attempts} node_id={node_id}")
            ok, msg = internal_connect()
            if not ok or state.BASE_STATION is None:
                last_err = f"Base station not connected: {msg}"
                log(f"[mscl-web] [{read_tag}] failed: {last_err}")
                time.sleep(0.5)
                continue
            try:
                ensure_beacon_on()
                node = mscl.WirelessNode(node_id, state.BASE_STATION)
                node.readWriteRetries(15)
                # 2. Critical values (use cache-first for EEPROM-heavy fields)
                current_rate = cached.get("current_rate")
                if refresh_eeprom or current_rate is None:
                    try:
                        current_rate = int(node.getSampleRate())
                    except Exception as e:
                        last_err = str(e)
                        log(f"[mscl-web] [{read_tag}] warn node_id={node_id}: getSampleRate failed (1st): {last_err}")
                        time.sleep(1.0)
                        try:
                            current_rate = int(node.getSampleRate())
                        except Exception as e2:
                            last_err = str(e2)
                            log(f"[mscl-web] [{read_tag}] error node_id={node_id}: getSampleRate failed (2nd): {last_err}")
                            if "EEPROM" in last_err:
                                metric_inc("eeprom_retries_read")
                            if "EEPROM" not in last_err:
                                mark_base_disconnected()
                                time.sleep(0.5)
                                continue
                try:
                    active_mask = node.getActiveChannels()
                except Exception:
                    active_mask = None
        
                # 3. Remaining fields are best-effort (do not fail read on EEPROM errors)
                model = cached.get("model", "TC-Link-200")
                if refresh_eeprom or "model" not in cached:
                    try:
                        model = node.model()
                    except Exception as e:
                        log(f"[mscl-web] [{read_tag}] warn node_id={node_id}: model read failed: {e}")
        
                sn = cached.get("sn", "N/A")
                try:
                    sn = str(node.nodeAddress())
                except Exception as e:
                    log(f"[mscl-web] [{read_tag}] warn node_id={node_id}: serial read failed: {e}")
        
                fw = cached.get("fw", "N/A")
                if refresh_eeprom or "fw" not in cached:
                    try:
                        fw = str(node.firmwareVersion())
                    except Exception as e:
                        log(f"[mscl-web] [{read_tag}] warn node_id={node_id}: firmware read failed: {e}")
        
                current_power = cached.get("current_power", 16)
                current_power_enum = cached.get("current_power_enum")
                if refresh_eeprom or "current_power" not in cached:
                    try: 
                        p_raw = int(node.getTransmitPower())
                        current_power_enum = p_raw
                        current_power = TX_POWER_ENUM_TO_DBM.get(p_raw, 16)
                    except Exception as e:
                        log(f"[mscl-web] [{read_tag}] warn node_id={node_id}: transmit power read failed: {e}")
                tx_power_options = _tx_power_options_for_model(model, current_power)
                comm_protocol = cached.get("comm_protocol")
                comm_protocol_text = cached.get("comm_protocol_text")
                if refresh_eeprom or "comm_protocol" not in cached:
                    try:
                        cp = int(node.communicationProtocol())
                        comm_protocol = cp
                        comm_protocol_text = COMM_PROTOCOL_MAP.get(cp, f"Value {cp}")
                    except Exception as e:
                        log(f"[mscl-web] [{read_tag}] warn node_id={node_id}: communicationProtocol read failed: {e}")
            
                # Optional status fields (best-effort)
                try:
                    region = str(node.regionCode())
                except Exception:
                    region = None
                try:
                    last_comm = str(node.lastCommunicationTime()).split(".")[0]
                except Exception:
                    last_comm = None
                node_state, state_text, _ = _node_state_info(node)
                try:
                    node_address = int(node.nodeAddress())
                except Exception:
                    node_address = None
                try:
                    freq_raw = node.frequency()
                    try:
                        freq_ch = int(freq_raw)
                        frequency = f"{freq_ch} ({2404 + 2 * freq_ch} MHz)"
                    except Exception:
                        frequency = str(freq_raw)
                except Exception:
                    frequency = None
                storage_capacity_raw = cached.get("storage_capacity_raw")
                if refresh_eeprom or "storage_capacity_raw" not in cached:
                    try:
                        storage_capacity_raw = int(node.dataStorageSize())
                    except Exception:
                        pass
                try:
                    storage_pct = round(float(node.percentFull()), 2)
                except Exception:
                    storage_pct = None
                sampling_mode = cached.get("sampling_mode")
                sampling_mode_raw = cached.get("sampling_mode_raw")
                if refresh_eeprom or "sampling_mode" not in cached:
                    try:
                        sampling_mode_val = node.getSamplingMode()
                        try:
                            sampling_mode_raw = int(sampling_mode_val)
                        except Exception:
                            sampling_mode_raw = None
                        sampling_mode = "sync" if sampling_mode_val == mscl.WirelessTypes.samplingMode_sync else "non_sync"
                    except Exception:
                        pass
                current_data_mode = cached.get("current_data_mode")
                data_mode_options = cached.get("data_mode_options", [])
                if refresh_eeprom or "current_data_mode" not in cached:
                    try:
                        current_data_mode = int(node.getDataMode())
                    except Exception:
                        pass
                if refresh_eeprom or not data_mode_options:
                    try:
                        features = node.features()
                        modes = []
                        try:
                            modes = features.dataModes()
                        except Exception:
                            modes = []
                        opts = []
                        for m in modes:
                            mi = int(m)
                            opts.append({"value": mi, "label": DATA_MODE_LABELS.get(mi, f"Value {mi}")})
                        data_mode_options = opts
                    except Exception:
                        if not data_mode_options:
                            data_mode_options = []
                if current_data_mode is not None:
                    if all(x.get("value") != int(current_data_mode) for x in data_mode_options):
                        data_mode_options.insert(
                            0,
                            {
                                "value": int(current_data_mode),
                                "label": DATA_MODE_LABELS.get(int(current_data_mode), f"Value {int(current_data_mode)}"),
                            },
                        )
                if not data_mode_options:
                    data_mode_options = [
                        {"value": 1, "label": DATA_MODE_LABELS[1]},
                        {"value": 2, "label": DATA_MODE_LABELS[2]},
                        {"value": 3, "label": DATA_MODE_LABELS[3]},
                    ]
                data_mode_text = (
                    DATA_MODE_LABELS.get(int(current_data_mode), f"Value {int(current_data_mode)}")
                    if current_data_mode is not None
                    else None
                )
                current_input_range = cached.get("current_input_range")
                supported_input_ranges = cached.get("supported_input_ranges", [])
                if refresh_eeprom or "current_input_range" not in cached:
                    try:
                        current_input_range = int(node.getInputRange(ch1_mask()))
                    except Exception as e:
                        log(f"[mscl-web] [{read_tag}] warn node_id={node_id}: getInputRange(ch1) failed: {e}")
                if refresh_eeprom or not supported_input_ranges:
                    try:
                        features = node.features()
                        ir_values = []
                        try:
                            ir_values = features.inputRanges()
                        except Exception:
                            ir_values = []
                        supported_input_ranges = []
                        for ir in ir_values:
                            ir_int = int(ir)
                            supported_input_ranges.append({
                                "value": ir_int,
                                "label": INPUT_RANGE_LABELS.get(ir_int, f"Value {ir_int}"),
                                "primary": ir_int in PRIMARY_INPUT_RANGES,
                            })
                        # Stable order: primary (SensorConnect top set) first, then others by value.
                        supported_input_ranges.sort(
                            key=lambda x: (0 if x.get("primary") else 1, int(x.get("value", 999999)))
                        )
                        if len(supported_input_ranges) <= 1:
                            existing = {int(x.get("value")) for x in supported_input_ranges if x.get("value") is not None}
                            for v in (99, 100, 101, 102, 103):
                                if v not in existing:
                                    supported_input_ranges.append({
                                        "value": v,
                                        "label": INPUT_RANGE_LABELS[v],
                                        "primary": True,
                                    })
                            supported_input_ranges.sort(
                                key=lambda x: (0 if x.get("primary") else 1, int(x.get("value", 999999)))
                            )
                        if current_input_range is not None and all(x.get("value") != int(current_input_range) for x in supported_input_ranges):
                            supported_input_ranges.insert(0, {
                                "value": int(current_input_range),
                                "label": INPUT_RANGE_LABELS.get(int(current_input_range), f"Value {int(current_input_range)}"),
                                "primary": int(current_input_range) in PRIMARY_INPUT_RANGES,
                            })
                    except Exception as e:
                        log(f"[mscl-web] [{read_tag}] warn node_id={node_id}: features/inputRanges failed: {e}")
                # Fallback: keep SensorConnect-like core list visible even when feature read fails.
                if not supported_input_ranges:
                    supported_input_ranges = [
                        {"value": 99, "label": INPUT_RANGE_LABELS[99], "primary": True},
                        {"value": 100, "label": INPUT_RANGE_LABELS[100], "primary": True},
                        {"value": 101, "label": INPUT_RANGE_LABELS[101], "primary": True},
                        {"value": 102, "label": INPUT_RANGE_LABELS[102], "primary": True},
                        {"value": 103, "label": INPUT_RANGE_LABELS[103], "primary": True},
                    ]
                    if current_input_range is not None and all(x.get("value") != int(current_input_range) for x in supported_input_ranges):
                        supported_input_ranges.insert(0, {
                            "value": int(current_input_range),
                            "label": INPUT_RANGE_LABELS.get(int(current_input_range), f"Value {int(current_input_range)}"),
                            "primary": int(current_input_range) in PRIMARY_INPUT_RANGES,
                        })

                current_low_pass = cached.get("current_low_pass")
                low_pass_options = cached.get("low_pass_options", [])
                try:
                    lp_raw = int(node.getLowPassFilter(ch1_mask()))
                    current_low_pass = lp_raw
                except Exception as e:
                    log(f"[mscl-web] [{read_tag}] warn node_id={node_id}: getLowPassFilter(ch1) failed: {e}")
                try:
                    features = node.features()
                    lpf = []
                    try:
                        lpf = features.lowPassFilters()
                    except Exception:
                        lpf = []
                    opts = []
                    for v in lpf:
                        vi = int(v)
                        opts.append({"value": vi, "label": LOW_PASS_LABELS.get(vi, f"Value {vi}")})
                    if not opts:
                        opts = [{"value": 294, "label": LOW_PASS_LABELS.get(294, "294 Hz")}]
                    low_pass_options = opts
                except Exception as e:
                    log(f"[mscl-web] [{read_tag}] warn node_id={node_id}: features/lowPassFilters failed: {e}")
                    if not low_pass_options:
                        low_pass_options = [{"value": 294, "label": LOW_PASS_LABELS.get(294, "294 Hz")}]
                if current_low_pass is not None and all(x.get("value") != int(current_low_pass) for x in low_pass_options):
                    low_pass_options.insert(0, {"value": int(current_low_pass), "label": LOW_PASS_LABELS.get(int(current_low_pass), f"Value {int(current_low_pass)}")})
                if current_low_pass is None and low_pass_options:
                    current_low_pass = int(low_pass_options[0]["value"])

                current_unit = cached.get("current_unit")
                unit_options = cached.get("unit_options", [])
                if refresh_eeprom or "current_unit" not in cached:
                    try:
                        current_unit = int(node.getUnit(ch1_mask()))
                    except Exception as e:
                        log(f"[mscl-web] [{read_tag}] warn node_id={node_id}: getUnit(ch1) failed: {e}")
                        try:
                            current_unit = int(node.getUnit())
                        except Exception:
                            pass
                if refresh_eeprom or not unit_options:
                    try:
                        features = node.features()
                        values = []
                        for getter in (lambda: features.units(ch1_mask()), lambda: features.units()):
                            try:
                                values = getter()
                                if values:
                                    break
                            except Exception:
                                continue
                        unit_options = []
                        for v in values:
                            vi = int(v)
                            unit_options.append({"value": vi, "label": UNIT_LABELS.get(vi, f"Value {vi}")})
                    except Exception as e:
                        log(f"[mscl-web] [{read_tag}] warn node_id={node_id}: features/units failed: {e}")
                # SensorConnect-like behavior: keep core engineering units visible.
                if len(unit_options) <= 1:
                    existing = {int(x.get("value")) for x in unit_options if x.get("value") is not None}
                    for target_label in PRIMARY_UNIT_ORDER:
                        for unit_val, unit_label in UNIT_LABELS.items():
                            if _unit_family(unit_label) == target_label and int(unit_val) not in existing:
                                unit_options.append({"value": int(unit_val), "label": unit_label})
                                existing.add(int(unit_val))
                                break
                if unit_options:
                    unit_options.sort(
                        key=lambda x: (
                            PRIMARY_UNIT_ORDER.index(_unit_family(x.get("label"))) if _unit_family(x.get("label")) in PRIMARY_UNIT_ORDER else 99,
                            str(x.get("label")),
                            int(x.get("value", 999999)),
                        )
                    )
                if current_unit is not None and all(x.get("value") != int(current_unit) for x in unit_options):
                    unit_options.insert(0, {"value": int(current_unit), "label": UNIT_LABELS.get(int(current_unit), f"Value {int(current_unit)}")})
                if not unit_options and current_unit is not None:
                    unit_options = [{"value": int(current_unit), "label": UNIT_LABELS.get(int(current_unit), f"Value {int(current_unit)}")}]

                current_cjc_unit = cached.get("current_cjc_unit")
                cjc_unit_options = cached.get("cjc_unit_options", [])
                if refresh_eeprom or "current_cjc_unit" not in cached:
                    try:
                        current_cjc_unit = int(node.getUnit(ch2_mask()))
                    except Exception as e:
                        log(f"[mscl-web] [{read_tag}] warn node_id={node_id}: getUnit(ch2) failed: {e}")
                if refresh_eeprom or not cjc_unit_options:
                    try:
                        features = node.features()
                        values = []
                        for getter in (lambda: features.units(ch2_mask()), lambda: features.units()):
                            try:
                                values = getter()
                                if values:
                                    break
                            except Exception:
                                continue
                        cjc_unit_options = []
                        for v in values:
                            vi = int(v)
                            lbl = UNIT_LABELS.get(vi, f"Value {vi}")
                            if _is_temp_unit(lbl):
                                cjc_unit_options.append({"value": vi, "label": lbl})
                    except Exception as e:
                        log(f"[mscl-web] [{read_tag}] warn node_id={node_id}: features/units(ch2) failed: {e}")
                if len(cjc_unit_options) <= 1:
                    existing = {int(x.get("value")) for x in cjc_unit_options if x.get("value") is not None}
                    for target_label in TEMP_UNIT_ORDER:
                        for unit_val, unit_label in UNIT_LABELS.items():
                            if (target_label.lower() in str(unit_label).lower()) and int(unit_val) not in existing:
                                cjc_unit_options.append({"value": int(unit_val), "label": unit_label})
                                existing.add(int(unit_val))
                                break
                if cjc_unit_options:
                    cjc_unit_options.sort(
                        key=lambda x: (
                            TEMP_UNIT_ORDER.index(next((t for t in TEMP_UNIT_ORDER if t.lower() in str(x.get("label", "")).lower()), TEMP_UNIT_ORDER[0]))
                            if any(t.lower() in str(x.get("label", "")).lower() for t in TEMP_UNIT_ORDER) else 99,
                            str(x.get("label")),
                            int(x.get("value", 999999)),
                        )
                    )
                if current_cjc_unit is not None and all(x.get("value") != int(current_cjc_unit) for x in cjc_unit_options):
                    lbl = UNIT_LABELS.get(int(current_cjc_unit), f"Value {int(current_cjc_unit)}")
                    if _is_temp_unit(lbl):
                        cjc_unit_options.insert(0, {"value": int(current_cjc_unit), "label": lbl})
                if not cjc_unit_options and current_cjc_unit is not None:
                    lbl = UNIT_LABELS.get(int(current_cjc_unit), f"Value {int(current_cjc_unit)}")
                    if _is_temp_unit(lbl):
                        cjc_unit_options = [{"value": int(current_cjc_unit), "label": lbl}]

                current_storage_limit_mode = cached.get("current_storage_limit_mode")
                storage_limit_options = cached.get("storage_limit_options", [])
                try:
                    current_storage_limit_mode = int(node.getStorageLimitMode())
                except Exception as e:
                    log(f"[mscl-web] [{read_tag}] warn node_id={node_id}: getStorageLimitMode failed: {e}")
                try:
                    features = node.features()
                    modes = []
                    try:
                        modes = features.storageLimitModes()
                    except Exception:
                        modes = []
                    opts = []
                    for v in modes:
                        vi = int(v)
                        opts.append({"value": vi, "label": STORAGE_LIMIT_LABELS.get(vi, f"Value {vi}")})
                    if not opts:
                        opts = [{"value": 0, "label": "Overwrite"}, {"value": 1, "label": "Stop"}]
                    storage_limit_options = opts
                except Exception as e:
                    log(f"[mscl-web] [{read_tag}] warn node_id={node_id}: features/storageLimitModes failed: {e}")
                    if not storage_limit_options:
                        storage_limit_options = [{"value": 0, "label": "Overwrite"}, {"value": 1, "label": "Stop"}]
                if current_storage_limit_mode is not None and all(x.get("value") != int(current_storage_limit_mode) for x in storage_limit_options):
                    storage_limit_options.insert(0, {"value": int(current_storage_limit_mode), "label": STORAGE_LIMIT_LABELS.get(int(current_storage_limit_mode), f"Value {int(current_storage_limit_mode)}")})
                if current_storage_limit_mode is None and storage_limit_options:
                    current_storage_limit_mode = int(storage_limit_options[0]["value"])

                current_lost_beacon_timeout = cached.get("current_lost_beacon_timeout")
                try:
                    current_lost_beacon_timeout = int(node.getLostBeaconTimeout())
                except Exception as e:
                    log(f"[mscl-web] [{read_tag}] warn node_id={node_id}: getLostBeaconTimeout failed: {e}")
                if current_lost_beacon_timeout is None:
                    current_lost_beacon_timeout = 2
                current_lost_beacon_enabled = bool(int(current_lost_beacon_timeout) > 0)

                current_diagnostic_interval = cached.get("current_diagnostic_interval")
                try:
                    current_diagnostic_interval = int(node.getDiagnosticInterval())
                except Exception as e:
                    log(f"[mscl-web] [{read_tag}] warn node_id={node_id}: getDiagnosticInterval failed: {e}")
                if current_diagnostic_interval is None:
                    current_diagnostic_interval = 60
                current_diagnostic_enabled = bool(int(current_diagnostic_interval) > 0)

                supports_transducer_type = cached.get("supports_transducer_type")
                supports_temp_sensor_options = cached.get("supports_temp_sensor_options")
                current_transducer_type = cached.get("current_transducer_type")
                current_sensor_type = cached.get("current_sensor_type")
                current_wire_type = cached.get("current_wire_type")
                transducer_options = cached.get("transducer_options", [])
                rtd_sensor_options = cached.get("rtd_sensor_options", [])
                thermistor_sensor_options = cached.get("thermistor_sensor_options", [])
                thermocouple_sensor_options = cached.get("thermocouple_sensor_options", [])
                rtd_wire_options = cached.get("rtd_wire_options", [])

                supports_default_mode = cached.get("supports_default_mode")
                supports_inactivity_timeout = cached.get("supports_inactivity_timeout")
                supports_check_radio_interval = cached.get("supports_check_radio_interval")
                current_default_mode = cached.get("current_default_mode")
                current_inactivity_timeout = cached.get("current_inactivity_timeout")
                current_check_radio_interval = cached.get("current_check_radio_interval")
                default_mode_options = cached.get("default_mode_options", [])

                try:
                    features = node.features()
                except Exception:
                    features = None

                if features is not None:
                    supports_default_mode = _feature_supported(features, "supportsDefaultMode")
                    supports_inactivity_timeout = _feature_supported(features, "supportsInactivityTimeout")
                    supports_check_radio_interval = _feature_supported(features, "supportsCheckRadioInterval")
                    supports_transducer_type = _feature_supported(features, "supportsTransducerType")
                    supports_temp_sensor_options = _feature_supported(features, "supportsTempSensorOptions")

                    # SensorConnect-style behavior: try reads even when supports* says NO.
                    try:
                        current_default_mode = int(node.getDefaultMode())
                        supports_default_mode = True
                    except Exception as e:
                        log(f"[mscl-web] [{read_tag}] warn node_id={node_id}: getDefaultMode failed: {e}")
                    try:
                        modes = []
                        try:
                            modes = features.defaultModes()
                        except Exception:
                            modes = []
                        default_mode_options = []
                        for m in modes:
                            mi = int(m)
                            default_mode_options.append({
                                "value": mi,
                                "label": DEFAULT_MODE_LABELS.get(mi, f"Value {mi}")
                            })
                        default_mode_options = _filter_default_modes(default_mode_options)
                        if default_mode_options:
                            supports_default_mode = True
                        if not default_mode_options:
                            default_mode_options = [
                                {"value": 0, "label": "Idle"},
                                {"value": 5, "label": "Sleep"},
                                {"value": 6, "label": "Sample"},
                            ]
                            default_mode_options = _filter_default_modes(default_mode_options)
                    except Exception as e:
                        log(f"[mscl-web] [{read_tag}] warn node_id={node_id}: features/defaultModes failed: {e}")
                        if not default_mode_options:
                            default_mode_options = [
                                {"value": 0, "label": "Idle"},
                                {"value": 5, "label": "Sleep"},
                                {"value": 6, "label": "Sample"},
                            ]
                        default_mode_options = _filter_default_modes(default_mode_options)

                    try:
                        current_inactivity_timeout = int(node.getInactivityTimeout())
                        supports_inactivity_timeout = True
                    except Exception as e:
                        log(f"[mscl-web] [{read_tag}] warn node_id={node_id}: getInactivityTimeout failed: {e}")
                    try:
                        current_check_radio_interval = int(node.getCheckRadioInterval())
                        supports_check_radio_interval = True
                    except Exception as e:
                        log(f"[mscl-web] [{read_tag}] warn node_id={node_id}: getCheckRadioInterval failed: {e}")

                    try:
                        tr_types = []
                        try:
                            tr_types = features.transducerTypes()
                        except Exception:
                            tr_types = []
                        transducer_options = []
                        for v in tr_types:
                            vi = int(v)
                            transducer_options.append({"value": vi, "label": TRANSDUCER_LABELS.get(vi, f"Value {vi}")})
                        if not transducer_options:
                            transducer_options = [{"value": int(k), "label": v} for k, v in TRANSDUCER_LABELS.items()]
                    except Exception as e:
                        log(f"[mscl-web] [{read_tag}] warn node_id={node_id}: features/transducerTypes failed: {e}")
                        if not transducer_options:
                            transducer_options = [{"value": int(k), "label": v} for k, v in TRANSDUCER_LABELS.items()]
                    try:
                        tc_types = []
                        try:
                            tc_types = features.thermocoupleTypes()
                        except Exception:
                            tc_types = []
                        thermocouple_sensor_options = []
                        for v in tc_types:
                            vi = int(v)
                            thermocouple_sensor_options.append({"value": vi, "label": THERMOCOUPLE_SENSOR_LABELS.get(vi, f"Value {vi}")})
                    except Exception as e:
                        log(f"[mscl-web] [{read_tag}] warn node_id={node_id}: features/thermocoupleTypes failed: {e}")

                # SensorConnect-style behavior: read current temp sensor options even if supports* says NO.
                tso, tso_err = _get_temp_sensor_options(node)
                if tso is not None:
                    try:
                        current_transducer_type = int(tso.transducerType())
                        supports_transducer_type = True
                    except Exception:
                        pass
                    try:
                        if current_transducer_type == _wt("transducer_rtd", 1):
                            current_sensor_type = int(tso.rtdType())
                        elif current_transducer_type == _wt("transducer_thermistor", 2):
                            current_sensor_type = int(tso.thermistorType())
                        elif current_transducer_type == _wt("transducer_thermocouple", 0):
                            current_sensor_type = int(tso.thermocoupleType())
                    except Exception:
                        pass
                    try:
                        current_wire_type = int(tso.rtdWireType())
                    except Exception:
                        pass
                    supports_temp_sensor_options = True
                elif tso_err:
                    log(f"[mscl-web] [{read_tag}] warn node_id={node_id}: getTempSensorOptions failed: {tso_err}")

                if not transducer_options:
                    transducer_options = [{"value": int(k), "label": v} for k, v in TRANSDUCER_LABELS.items()]
                if current_transducer_type is not None and all(x.get("value") != int(current_transducer_type) for x in transducer_options):
                    transducer_options.insert(0, {"value": int(current_transducer_type), "label": TRANSDUCER_LABELS.get(int(current_transducer_type), f"Value {int(current_transducer_type)}")})
                if current_transducer_type is None and transducer_options:
                    current_transducer_type = int(transducer_options[0]["value"])

                rtd_sensor_options = [{"value": int(k), "label": v} for k, v in RTD_SENSOR_LABELS.items()]
                thermistor_sensor_options = [{"value": int(k), "label": v} for k, v in THERMISTOR_SENSOR_LABELS.items()]
                if not thermocouple_sensor_options:
                    thermocouple_sensor_options = [{"value": int(k), "label": v} for k, v in THERMOCOUPLE_SENSOR_LABELS.items()]
                if current_transducer_type == _wt("transducer_thermocouple", 0) and current_sensor_type is not None:
                    if all(x.get("value") != int(current_sensor_type) for x in thermocouple_sensor_options):
                        thermocouple_sensor_options.insert(0, {"value": int(current_sensor_type), "label": THERMOCOUPLE_SENSOR_LABELS.get(int(current_sensor_type), f"Value {int(current_sensor_type)}")})
                rtd_wire_options = [{"value": int(k), "label": v} for k, v in RTD_WIRE_LABELS.items()]
                default_mode_options = _filter_default_modes_for_model(model, default_mode_options, current_default_mode)
                tx_power_options = _tx_power_options_for_model(model, current_power)

                # Rates (when available)
                supported_rates = cached.get("supported_rates", [])
                if (refresh_eeprom or not supported_rates) and current_rate is not None:
                    supported_rates = [{"enum_val": int(current_rate), "str_val": _sample_rate_label(current_rate)}]
                    try:
                        features = node.features()
                        rates = features.sampleRates(mscl.WirelessTypes.samplingMode_sync, 1, 0)
                        supported_rates = []
                        for r in rates:
                            rid = int(r)
                            supported_rates.append({"enum_val": rid, "str_val": _sample_rate_label(rid, r)})
                    except Exception as e:
                        log(f"[mscl-web] [{read_tag}] warn node_id={node_id}: features/sampleRates failed: {e}")
                supported_rates = _filter_sample_rates_for_model(model, supported_rates, current_rate)
            
                channels = []
                if active_mask is not None:
                    for i in range(1, 3):
                        channels.append({"id": i, "enabled": active_mask.enabled(i)})
                elif isinstance(cached.get("channels"), list) and cached.get("channels"):
                    channels = cached.get("channels")
                else:
                    channels = [{"id": 1, "enabled": True}, {"id": 2, "enabled": False}]
                current_inactivity_enabled = bool((current_inactivity_timeout is not None) and (int(current_inactivity_timeout) > 0))
        
                payload = dict(
                    success=True, model=model, sn=sn, fw=fw,
                    region=region, last_comm=last_comm, state=node_state, state_text=state_text,
                    node_address=node_address, frequency=frequency,
                    storage_pct=storage_pct, storage_capacity_raw=storage_capacity_raw, sampling_mode=sampling_mode, sampling_mode_raw=sampling_mode_raw,
                    current_data_mode=current_data_mode, data_mode_text=data_mode_text, data_mode_options=data_mode_options,
                    current_input_range=current_input_range, supported_input_ranges=supported_input_ranges,
                    current_unit=current_unit, unit_options=unit_options,
                    current_cjc_unit=current_cjc_unit, cjc_unit_options=cjc_unit_options,
                    current_rate=current_rate, current_power=current_power, current_power_enum=current_power_enum,
                    tx_power_options=tx_power_options,
                    comm_protocol=comm_protocol, comm_protocol_text=comm_protocol_text,
                    supported_rates=supported_rates, channels=channels,
                    current_low_pass=current_low_pass, low_pass_options=low_pass_options,
                    current_storage_limit_mode=current_storage_limit_mode, storage_limit_options=storage_limit_options,
                    current_lost_beacon_timeout=current_lost_beacon_timeout,
                    current_lost_beacon_enabled=current_lost_beacon_enabled,
                    current_diagnostic_interval=current_diagnostic_interval,
                    current_diagnostic_enabled=current_diagnostic_enabled,
                    supports_default_mode=bool(supports_default_mode),
                    supports_inactivity_timeout=bool(supports_inactivity_timeout),
                    supports_check_radio_interval=bool(supports_check_radio_interval),
                    supports_transducer_type=bool(supports_transducer_type),
                    supports_temp_sensor_options=bool(supports_temp_sensor_options),
                    current_default_mode=current_default_mode,
                    current_inactivity_timeout=current_inactivity_timeout,
                    current_inactivity_enabled=current_inactivity_enabled,
                    current_check_radio_interval=current_check_radio_interval,
                    default_mode_options=default_mode_options,
                    current_transducer_type=current_transducer_type,
                    current_sensor_type=current_sensor_type,
                    current_wire_type=current_wire_type,
                    transducer_options=transducer_options,
                    rtd_sensor_options=rtd_sensor_options,
                    thermistor_sensor_options=thermistor_sensor_options,
                    thermocouple_sensor_options=thermocouple_sensor_options,
                    rtd_wire_options=rtd_wire_options,
                )
                state.NODE_READ_CACHE[node_id] = dict(payload, ts=time.time())
                log(f"[mscl-web] [{read_tag}] success node_id={node_id} sample_rate={payload.get('current_rate')} fw={payload.get('fw')}")
                return jsonify(**payload)
            except Exception as e:
                last_err = str(e)
                log(f"[mscl-web] [{read_tag}] error node_id={node_id}: {e}")
                if "EEPROM" in last_err:
                    metric_inc("eeprom_retries_read")
                    backoff = min(4.0, 0.5 * (2 ** (attempt - 1)))
                    time.sleep(backoff)
                    continue
                mark_base_disconnected()
                time.sleep(0.5)
                continue
    if last_err:
        log(f"[mscl-web] [{read_tag}] failed node_id={node_id}: {last_err}")
    else:
        log(f"[mscl-web] [{read_tag}] failed node_id={node_id}: Read failed")
    return jsonify(success=False, error=last_err or "Read failed")

@app.route('/api/probe/<int:node_id>')
def api_probe(node_id):
    log(f"[mscl-web] Probe request node_id={node_id}")
    with state.OP_LOCK:
        ok, msg = internal_connect()
        if not ok or state.BASE_STATION is None:
            err = f"Base station not connected: {msg}"
            log(f"[mscl-web] Probe failed: {err}")
            return jsonify(success=False, error=err)
        try:
            if _is_sampling_active(node_id):
                return jsonify(success=False, error="Sampling active. Stop sampling before probe.")
            ensure_beacon_on()
            node = mscl.WirelessNode(node_id, state.BASE_STATION)
            node.readWriteRetries(10)
            # Try to nudge node without touching EEPROM
            try:
                node.setToIdle()
                time.sleep(1.5)
            except Exception as e:
                log(f"[mscl-web] Probe setToIdle failed node_id={node_id}: {e}")
            try:
                node.ping()
            except Exception as e:
                log(f"[mscl-web] Probe ping failed node_id={node_id}: {e}")
            # Skip resendStartSyncSampling to avoid EEPROM reads on stuck nodes
            # Final idle
            try:
                node.setToIdle()
                time.sleep(1.0)
            except Exception:
                pass
            return jsonify(success=True, message="Probe commands sent")
        except Exception as e:
            log(f"[mscl-web] Probe error node_id={node_id}: {e}")
            return jsonify(success=False, error=str(e))

@app.route('/api/node_idle/<int:node_id>', methods=['POST'])
def api_node_idle(node_id):
    with state.OP_LOCK:
        if node_id in state.IDLE_IN_PROGRESS:
            return jsonify(success=False, error="Set to Idle already in progress")
        if _is_sampling_active(node_id):
            return jsonify(success=False, error="Sampling active. Stop sampling before Set to Idle.")
        state.IDLE_IN_PROGRESS.add(node_id)
        ok, msg = internal_connect()
        if not ok or state.BASE_STATION is None:
            state.IDLE_IN_PROGRESS.discard(node_id)
            return jsonify(success=False, error=f"Base station not connected: {msg}")
        try:
            ensure_beacon_on()
            node = mscl.WirelessNode(node_id, state.BASE_STATION)
            node.readWriteRetries(10)
            idle_status = send_idle_sensorconnect_style(node, node_id, "manual-idle")
            sent = bool(idle_status.get("command_sent"))
            confirmed = bool(idle_status.get("state_confirmed"))
            reason = idle_status.get("reason") or "unknown"
            idle_result = idle_status.get("idle_result") or ("success" if confirmed else "pending")
            if not sent:
                log(f"[mscl-web] [PREP-IDLE] failed node_id={node_id} reason={reason}")
                return jsonify(success=False, error=reason, idle_confirmed=False, idle_result=idle_result, idle_status=idle_status)
            if confirmed:
                log(f"[mscl-web] [PREP-IDLE] success node_id={node_id}")
                return jsonify(success=True, message="Node set to Idle", idle_confirmed=True, idle_result=idle_result, reason=reason, idle_status=idle_status)
            log(f"[mscl-web] [PREP-IDLE] pending node_id={node_id} reason={reason}")
            return jsonify(success=True, message="Idle command sent", idle_confirmed=False, idle_result=idle_result, reason=reason, idle_status=idle_status)
        except Exception as e:
            log(f"[mscl-web] [PREP-IDLE] failed node_id={node_id}: {e}")
            return jsonify(success=False, error=str(e))
        finally:
            state.IDLE_IN_PROGRESS.discard(node_id)

@app.route('/api/node_cycle_power/<int:node_id>', methods=['POST'])
def api_node_cycle_power(node_id):
    with state.OP_LOCK:
        ok, msg = internal_connect()
        if not ok or state.BASE_STATION is None:
            return jsonify(success=False, error=f"Base station not connected: {msg}")
        try:
            ensure_beacon_on()
            node = mscl.WirelessNode(node_id, state.BASE_STATION)
            node.readWriteRetries(10)
            node.cyclePower()
            log(f"[mscl-web] [PREP-CYCLE] success node_id={node_id}")
            return jsonify(success=True, message="Power cycle command sent")
        except Exception as e:
            log(f"[mscl-web] [PREP-CYCLE] failed node_id={node_id}: {e}")
            return jsonify(success=False, error=str(e))

@app.route('/api/node_sampling/<int:node_id>', methods=['POST'])
def api_node_sampling(node_id):
    with state.OP_LOCK:
        body = request.json or {}
        # Backward compatibility path (old UI sent only duration_sec).
        if "duration_sec" in body:
            body = {
                "duration_value": int(body.get("duration_sec", 0) or 0),
                "duration_units": "seconds",
                "continuous": int(body.get("duration_sec", 0) or 0) <= 0,
                "log_transmit_mode": "transmit",
                "data_type": "float",
                "sample_rate": None,
            }
        res = _start_sampling_run(node_id, body)
        if not res.get("success"):
            return jsonify(success=False, error=res.get("error", "Sampling start failed"))
        run = res["run"]
        return jsonify(success=True, message=f"Sampling started ({run['start_method']})", run=run)


@app.route('/api/sampling/start/<int:node_id>', methods=['POST'])
def api_sampling_start(node_id):
    with state.OP_LOCK:
        body = request.json or {}
        res = _start_sampling_run(node_id, body)
        if not res.get("success"):
            return jsonify(success=False, error=res.get("error", "Sampling start failed"))
        return jsonify(success=True, run=res["run"])


@app.route('/api/sampling/stop/<int:node_id>', methods=['POST'])
def api_sampling_stop(node_id):
    with state.OP_LOCK:
        ok, msg = internal_connect()
        if not ok or state.BASE_STATION is None:
            return jsonify(success=False, error=f"Base station not connected: {msg}")
        try:
            ensure_beacon_on()
            node = mscl.WirelessNode(node_id, state.BASE_STATION)
            node.readWriteRetries(10)
            idle_status = send_idle_sensorconnect_style(node, node_id, "stop-sampling")
            state.SAMPLE_STOP_TOKENS[node_id] = time.time()
            run = state.SAMPLE_RUNS.get(node_id, {})
            run.update({
                "state": "stopped",
                "stopped_at": int(time.time()),
                "idle_result": idle_status.get("idle_result"),
            })
            state.SAMPLE_RUNS[node_id] = run
            if idle_status.get("state_confirmed"):
                log(f"[mscl-web] [S-RUN] stop ok node_id={node_id}")
                return jsonify(success=True, message="Sampling stopped", idle_status=idle_status, run=run)
            reason = idle_status.get("reason", "pending")
            log(f"[mscl-web] [S-RUN] stop pending node_id={node_id}: {reason}")
            return jsonify(success=True, message=f"Stop sent ({reason})", idle_status=idle_status, run=run)
        except Exception as e:
            log(f"[mscl-web] [S-RUN] stop failed node_id={node_id}: {e}")
            return jsonify(success=False, error=str(e))


@app.route('/api/sampling/status/<int:node_id>')
def api_sampling_status(node_id):
    with state.OP_LOCK:
        state_num = None
        state_text = "Unknown"
        freshness_reason = None
        link_state = "offline"
        ok, msg = internal_connect(force_ping=False)
        if ok and state.BASE_STATION is not None:
            try:
                node = mscl.WirelessNode(node_id, state.BASE_STATION)
                node.readWriteRetries(5)
                state_num, state_text, freshness_reason = _node_state_info(node)
                link_state = "ok"
            except Exception as e:
                link_state = f"degraded: {e}"
        else:
            link_state = f"offline: {msg}"

        run = state.SAMPLE_RUNS.get(node_id, {})
        now = int(time.time())
        duration_sec = int(run.get("duration_sec") or 0)
        started_at = int(run.get("started_at") or 0)
        time_left = None
        if duration_sec > 0 and started_at > 0:
            time_left = max(0, duration_sec - max(0, now - started_at))
        return jsonify(
            success=True,
            node_id=node_id,
            node_state=state_text,
            node_state_num=state_num,
            freshness_reason=freshness_reason,
            link_state=link_state,
            run=run,
            time_left_sec=time_left,
        )

@app.route('/api/node_sleep/<int:node_id>', methods=['POST'])
def api_node_sleep(node_id):
    with state.OP_LOCK:
        ok, msg = internal_connect()
        if not ok or state.BASE_STATION is None:
            return jsonify(success=False, error=f"Base station not connected: {msg}")
        try:
            ensure_beacon_on()
            node = mscl.WirelessNode(node_id, state.BASE_STATION)
            node.readWriteRetries(10)
            node.sleep()
            log(f"[mscl-web] [SLEEP] sleep command sent node_id={node_id}")
            return jsonify(success=True, message="Sleep command sent")
        except Exception as e:
            log(f"[mscl-web] [SLEEP] failed node_id={node_id}: {e}")
            return jsonify(success=False, error=str(e))

@app.route('/api/clear_storage/<int:node_id>', methods=['POST'])
def api_clear_storage(node_id):
    with state.OP_LOCK:
        ok, msg = internal_connect()
        if not ok or state.BASE_STATION is None:
            return jsonify(success=False, error=f"Base station not connected: {msg}")
        try:
            ensure_beacon_on()
            node = mscl.WirelessNode(node_id, state.BASE_STATION)
            node.readWriteRetries(15)
            set_idle_with_retry(node, node_id, "before-clear-storage", attempts=2, delay_sec=0.8, required=False)
            node.erase()
            set_idle_with_retry(node, node_id, "after-clear-storage", attempts=2, delay_sec=0.8, required=False)
            cached = state.NODE_READ_CACHE.get(node_id, {})
            cached["storage_pct"] = 0.0
            cached["ts"] = time.time()
            state.NODE_READ_CACHE[node_id] = cached
            log(f"[mscl-web] [CLEAR-STORAGE] success node_id={node_id}")
            return jsonify(success=True, message="Storage cleared")
        except Exception as e:
            log(f"[mscl-web] [CLEAR-STORAGE] failed node_id={node_id}: {e}")
            return jsonify(success=False, error=str(e))


@app.route('/api/export_storage/<int:node_id>')
def api_export_storage(node_id):
    export_format = str(request.args.get("format", "csv") or "csv").strip().lower()
    if export_format not in ("csv", "json", "none"):
        return jsonify(success=False, error="Unsupported format. Use 'csv', 'json', or 'none'."), 400
    ingest_influx = _query_bool("ingest_influx", True)
    align_clock_raw = str(request.args.get("align_clock", "host") or "host").strip().lower()
    align_clock = align_clock_raw not in ("none", "off", "false", "0", "no")
    ui_from_raw = request.args.get("ui_from")
    ui_to_raw = request.args.get("ui_to")
    ui_window_from_ns = None
    ui_window_to_ns = None
    if ui_from_raw is not None or ui_to_raw is not None:
        if not ui_from_raw or not ui_to_raw:
            return jsonify(success=False, error="Both ui_from and ui_to are required"), 400
        try:
            ui_window_from_ns = _parse_iso_utc_to_ns(ui_from_raw, "ui_from")
            ui_window_to_ns = _parse_iso_utc_to_ns(ui_to_raw, "ui_to")
        except ValueError as ve:
            return jsonify(success=False, error=str(ve)), 400
        if int(ui_window_to_ns) <= int(ui_window_from_ns):
            return jsonify(success=False, error="ui_to must be greater than ui_from"), 400
    host_hours_raw = request.args.get("host_hours")
    host_hours = None
    if host_hours_raw is not None and str(host_hours_raw).strip() != "":
        try:
            host_hours = float(host_hours_raw)
        except Exception:
            return jsonify(success=False, error="Invalid host_hours. Use a positive number."), 400
        if host_hours <= 0:
            return jsonify(success=False, error="host_hours must be > 0"), 400

    with state.OP_LOCK:
        ok, msg = internal_connect()
        if not ok or state.BASE_STATION is None:
            return jsonify(success=False, error=f"Base station not connected: {msg}"), 503
        try:
            _pause_stream_reader(4.0, f"export-storage node={node_id}")
            ensure_beacon_on()
            base_station = state.BASE_STATION
            if base_station is None:
                return jsonify(success=False, error="Base station not connected"), 503

            old_base_timeout = None
            old_base_retries = None
            try:
                old_base_timeout = int(base_station.timeout())
            except Exception:
                old_base_timeout = None
            try:
                old_base_retries = int(base_station.readWriteRetries())
            except Exception:
                old_base_retries = None

            # Datalog download needs much higher command timeout than default.
            try:
                base_station.timeout(max(int(old_base_timeout or 0), 4000))
            except Exception:
                pass
            try:
                base_station.readWriteRetries(max(int(old_base_retries or 0), 25))
            except Exception:
                pass

            rows = []
            sweep_count = 0
            session_count = 0
            last_download_err = None

            try:
                # Retry around DatalogDownloader create/session info to tolerate startup noise.
                for attempt in range(1, 6):
                    _pause_stream_reader(6.0, f"export-storage attempt={attempt} node={node_id}")
                    node = mscl.WirelessNode(node_id, base_station)
                    node.readWriteRetries(25)

                    idle_status = send_idle_sensorconnect_style(node, node_id, f"before-export-storage#{attempt}")
                    idle_confirmed = bool(idle_status.get("state_confirmed"))
                    if not idle_confirmed:
                        log(
                            f"[mscl-web] [EXPORT-STORAGE] node_id={node_id}: idle not confirmed "
                            f"before attempt {attempt}; reason={idle_status.get('reason')}"
                        )

                    settle_sec = 1.0 + (attempt * 0.5)
                    time.sleep(settle_sec)

                    try:
                        node.ping()
                    except Exception:
                        pass

                    session_count = None
                    session_err = None
                    for s_try in range(1, 4):
                        try:
                            session_count = int(node.getNumDatalogSessions())
                            break
                        except Exception as se:
                            session_err = str(se)
                            if "EEPROM" in session_err:
                                metric_inc("eeprom_retries_read")
                            log(
                                f"[mscl-web] [EXPORT-STORAGE] getNumDatalogSessions failed "
                                f"node_id={node_id} attempt {attempt}/5 subtry {s_try}/3: {session_err}"
                            )
                            time.sleep(0.5 * s_try)

                    if session_count is None:
                        last_download_err = RuntimeError(
                            f"Failed to read datalog session count: {session_err or 'unknown error'}"
                        )
                        if attempt < 5:
                            continue
                        raise last_download_err

                    if session_count <= 0:
                        return jsonify(success=False, error="No datalog sessions on node storage"), 404

                    try:
                        downloader = mscl.DatalogDownloader(node)
                    except Exception as create_exc:
                        last_download_err = create_exc
                        err_txt = str(create_exc)
                        log(f"[mscl-web] [EXPORT-STORAGE] attempt {attempt}/5 create failed node_id={node_id}: {err_txt}")
                        retriable = (
                            ("Failed to get the Datalog Session Info" in err_txt)
                            or ("Failed to get the Datalogging Session Info" in err_txt)
                            or ("EEPROM" in err_txt)
                        )
                        if attempt < 5 and retriable:
                            continue
                        raise

                    rows = []
                    sweep_count = 0
                    safety_loops = 0
                    transient_errors = 0
                    consecutive_errors = 0
                    while not downloader.complete():
                        safety_loops += 1
                        if safety_loops > 20_000_000:
                            raise RuntimeError("Download aborted: safety loop limit reached")

                        try:
                            batch = downloader.getNextData()
                            consecutive_errors = 0
                        except Exception as dl_step_exc:
                            err_txt = str(dl_step_exc)
                            retriable = (
                                ("Failed to download data from the Node" in err_txt)
                                or ("Failed to get the Datalog Session Info" in err_txt)
                                or ("Failed to get the Datalogging Session Info" in err_txt)
                                or ("EEPROM" in err_txt)
                            )
                            if not retriable:
                                raise
                            transient_errors += 1
                            consecutive_errors += 1
                            if transient_errors % 10 == 0:
                                log(
                                    f"[mscl-web] [EXPORT-STORAGE] transient errors node_id={node_id}: "
                                    f"{transient_errors}, pct={downloader.percentComplete():.3f}, last={err_txt}"
                                )
                            if consecutive_errors >= 20 or transient_errors >= 400:
                                raise RuntimeError(
                                    f"Too many transient download errors ({transient_errors}); last={err_txt}"
                                )
                            time.sleep(min(1.0, 0.08 * consecutive_errors))
                            continue

                        sweeps = _coerce_logged_sweeps(batch)
                        if not sweeps:
                            continue

                        for sweep in sweeps:
                            sweep_count += 1
                            try:
                                session_index = int(downloader.sessionIndex())
                            except Exception:
                                session_index = None
                            try:
                                sample_rate_text = str(downloader.sampleRate())
                            except Exception:
                                sample_rate_text = ""
                            rows.extend(_logged_sweep_rows(node_id, session_index, sample_rate_text, sweep))

                        if sweep_count % 500 == 0:
                            try:
                                pct = float(downloader.percentComplete())
                            except Exception:
                                pct = -1.0
                            log(
                                f"[mscl-web] [EXPORT-STORAGE] progress node_id={node_id} "
                                f"sweeps={sweep_count} points={len(rows)} pct={pct:.3f}"
                            )

                    if rows:
                        break
                    last_download_err = RuntimeError("No datapoints found in node datalog sessions")

                if last_download_err is not None and not rows:
                    raise last_download_err
            finally:
                try:
                    if old_base_timeout is not None:
                        base_station.timeout(int(old_base_timeout))
                except Exception:
                    pass
                try:
                    if old_base_retries is not None:
                        base_station.readWriteRetries(int(old_base_retries))
                except Exception:
                    pass


            if not rows:
                return jsonify(success=False, error="No datapoints found in node datalog sessions"), 404

            time_window_applied = False
            time_window_from_ns = None
            time_window_to_ns = None
            time_window_offset_ns = 0
            time_window_origin = None
            if export_format in ("csv", "json"):
                if ui_window_from_ns is not None and ui_window_to_ns is not None:
                    time_window_from_ns = int(ui_window_from_ns)
                    time_window_to_ns = int(ui_window_to_ns)
                    time_window_origin = "ui"
                elif host_hours is not None:
                    time_window_to_ns = int(time.time_ns())
                    time_window_from_ns = time_window_to_ns - int(host_hours * 3600.0 * 1_000_000_000)
                    time_window_origin = "host_hours"

            if time_window_from_ns is not None and time_window_to_ns is not None and export_format in ("csv", "json"):
                time_window_offset_ns, _ = _compute_export_clock_offset_ns(
                    rows, node_id=node_id, min_skew_sec=MSCL_EXPORT_ALIGN_MIN_SKEW_SEC
                )
                filtered_rows = []
                for row in rows:
                    try:
                        host_ts_ns = int(row.get("timestamp_ns")) + int(time_window_offset_ns)
                    except Exception:
                        continue
                    if time_window_from_ns <= host_ts_ns <= time_window_to_ns:
                        filtered_rows.append(row)
                rows = filtered_rows
                time_window_applied = True
                if not rows:
                    return jsonify(
                        success=False,
                        error="No datapoints in selected time window",
                        window_origin=time_window_origin,
                        ui_from=ui_from_raw,
                        ui_to=ui_to_raw,
                        host_hours=host_hours,
                    ), 404

            backfill_written = 0
            backfill_skipped_existing = 0
            backfill_error = None
            clock_offset_ns = 0
            clock_skew_ns = 0
            if ingest_influx:
                try:
                    if align_clock:
                        clock_offset_ns, clock_skew_ns = _compute_export_clock_offset_ns(
                            rows, node_id=node_id, min_skew_sec=MSCL_EXPORT_ALIGN_MIN_SKEW_SEC
                        )
                    bf_stats = _backfill_rows_to_influx_stream(
                        node_id=node_id,
                        rows=rows,
                        time_offset_ns=clock_offset_ns,
                        source_tag=MSCL_SOURCE_NODE_EXPORT,
                    )
                    backfill_written = int(bf_stats.get("written", 0))
                    backfill_skipped_existing = int(bf_stats.get("skipped_existing", 0))
                    metric_inc("stream_write_calls")
                    metric_inc("stream_points_written", backfill_written)
                    log(
                        f"[mscl-web] [EXPORT-STORAGE] backfill node_id={node_id} "
                        f"written={backfill_written} skipped_existing={backfill_skipped_existing} "
                        f"offset_ns={clock_offset_ns} skew_ns={clock_skew_ns}"
                    )
                except Exception as bf_exc:
                    backfill_error = str(bf_exc)
                    log(
                        f"[mscl-web] [EXPORT-STORAGE] backfill failed node_id={node_id}: {backfill_error}"
                    )

            exported_at = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            base_name = f"node_{node_id}_datalog_{exported_at}"
            log(
                f"[mscl-web] [EXPORT-STORAGE] success node_id={node_id} "
                f"sessions={session_count} sweeps={sweep_count} points={len(rows)} "
                f"format={export_format} ingest_influx={ingest_influx} "
                f"backfill_written={backfill_written} backfill_skipped_existing={backfill_skipped_existing} "
                f"time_window_applied={time_window_applied} time_window_origin={time_window_origin} "
                f"time_window_offset_ns={int(time_window_offset_ns)} "
                f"host_hours={host_hours} ui_from={ui_from_raw} ui_to={ui_to_raw}"
            )

            def _attach_export_headers(resp):
                resp.headers["X-Influx-Backfill-Written"] = str(int(backfill_written))
                resp.headers["X-Influx-Backfill-Skipped-Existing"] = str(int(backfill_skipped_existing))
                resp.headers["X-Influx-Clock-Offset-Ns"] = str(int(clock_offset_ns))
                resp.headers["X-Influx-Clock-Skew-Ns"] = str(int(clock_skew_ns))
                if time_window_applied:
                    if time_window_origin:
                        resp.headers["X-Time-Window-Origin"] = str(time_window_origin)
                    if time_window_from_ns is not None:
                        resp.headers["X-Time-Window-From-Ns"] = str(int(time_window_from_ns))
                    if time_window_to_ns is not None:
                        resp.headers["X-Time-Window-To-Ns"] = str(int(time_window_to_ns))
                    if ui_from_raw:
                        resp.headers["X-UI-Window-From"] = str(ui_from_raw)
                    if ui_to_raw:
                        resp.headers["X-UI-Window-To"] = str(ui_to_raw)
                    if host_hours is not None:
                        resp.headers["X-Host-Window-Hours"] = str(host_hours)
                    resp.headers["X-Time-Window-Offset-Ns"] = str(int(time_window_offset_ns))
                if backfill_error:
                    resp.headers["X-Influx-Backfill-Error"] = str(backfill_error)[:180]
                return resp

            if export_format == "json":
                payload = {
                    "node_id": int(node_id),
                    "exported_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ"),
                    "session_count": int(session_count),
                    "sweep_count": int(sweep_count),
                    "point_count": int(len(rows)),
                    "ingest_influx": bool(ingest_influx),
                    "backfill_written": int(backfill_written),
                    "backfill_skipped_existing": int(backfill_skipped_existing),
                    "clock_offset_ns": int(clock_offset_ns),
                    "clock_skew_ns": int(clock_skew_ns),
                    "backfill_error": backfill_error,
                    "time_window_applied": bool(time_window_applied),
                    "time_window_origin": time_window_origin,
                    "ui_from": ui_from_raw,
                    "ui_to": ui_to_raw,
                    "host_hours": host_hours,
                    "time_window_offset_ns": int(time_window_offset_ns),
                    "rows": rows,
                }
                body = json.dumps(payload, ensure_ascii=False)
                resp = Response(
                    body,
                    mimetype="application/json",
                    headers={"Content-Disposition": f"attachment; filename={base_name}.json"},
                )
                return _attach_export_headers(resp)

            if export_format == "none":
                if ingest_influx and backfill_error:
                    return jsonify(
                        success=False,
                        error=f"Export to Influx failed: {backfill_error}",
                        node_id=int(node_id),
                        session_count=int(session_count),
                        sweep_count=int(sweep_count),
                        point_count=int(len(rows)),
                        backfill_written=int(backfill_written),
                        backfill_skipped_existing=int(backfill_skipped_existing),
                        clock_offset_ns=int(clock_offset_ns),
                        clock_skew_ns=int(clock_skew_ns),
                    ), 502
                return jsonify(
                    success=True,
                    node_id=int(node_id),
                    session_count=int(session_count),
                    sweep_count=int(sweep_count),
                    point_count=int(len(rows)),
                    ingest_influx=bool(ingest_influx),
                    backfill_written=int(backfill_written),
                    backfill_skipped_existing=int(backfill_skipped_existing),
                    clock_offset_ns=int(clock_offset_ns),
                    clock_skew_ns=int(clock_skew_ns),
                    backfill_error=backfill_error,
                    time_window_applied=bool(time_window_applied),
                    time_window_origin=time_window_origin,
                    ui_from=ui_from_raw,
                    ui_to=ui_to_raw,
                    host_hours=host_hours,
                    time_window_offset_ns=int(time_window_offset_ns),
                )

            csv_columns = [
                "timestamp_utc",
                "timestamp_ns",
                "node_id",
                "session_index",
                "sample_rate",
                "channel",
                "channel_id",
                "value",
                "tick",
                "cal_applied",
            ]
            csv_buf = io.StringIO()
            writer = csv.DictWriter(csv_buf, fieldnames=csv_columns)
            writer.writeheader()
            writer.writerows(rows)
            csv_bytes = csv_buf.getvalue().encode("utf-8")
            resp = send_file(
                io.BytesIO(csv_bytes),
                mimetype="text/csv; charset=utf-8",
                as_attachment=True,
                download_name=f"{base_name}.csv",
            )
            return _attach_export_headers(resp)
        except Exception as e:
            err = str(e)
            if (
                "Failed to download data from the Node" in err
                or "Failed to get the Datalog Session Info" in err
                or "Failed to get the Datalogging Session Info" in err
                or "EEPROM" in err
            ):
                friendly = (
                    "Node datalog session info is unstable (MSCL read error). "
                    "Try: stop sampling -> set Idle -> wait 5-10s -> retry export."
                )
                log(f"[mscl-web] [EXPORT-STORAGE] failed node_id={node_id}: {err} | hint={friendly}")
                return jsonify(success=False, error=friendly), 409
            log(f"[mscl-web] [EXPORT-STORAGE] failed node_id={node_id}: {err}")
            return jsonify(success=False, error=err), 500


@app.route('/api/write', methods=['POST'])
def api_write():
    data = request.json
    last_err = None
    last_was_eeprom = False
    log(f"[mscl-web] Write request node_id={data.get('node_id')}")
    with state.OP_LOCK:
        for attempt in range(1, 6):
            if attempt > 1:
                log(f"[mscl-web] Write retry {attempt}/5 node_id={data.get('node_id')}")
            # If last attempt failed with EEPROM read error, pause before retry.
            if last_was_eeprom:
                backoff = min(4.0, 0.5 * (2 ** (attempt - 1)))
                time.sleep(backoff)
            ok, msg = internal_connect()
            if not ok or state.BASE_STATION is None:
                last_err = f"Base station not connected: {msg}"
                log(f"[mscl-web] Write failed: {last_err}")
                time.sleep(0.5)
                continue
            try:
                ensure_beacon_on()
                node = mscl.WirelessNode(int(data['node_id']), state.BASE_STATION)
                node.readWriteRetries(15)
                def _to_opt_int(v):
                    if v is None:
                        return None
                    if isinstance(v, str):
                        vv = v.strip()
                        if vv == "":
                            return None
                        v = vv
                    try:
                        return int(v)
                    except Exception:
                        return None
                def _to_opt_bool(v):
                    if isinstance(v, bool):
                        return v
                    if isinstance(v, str):
                        s = v.strip().lower()
                        if s in ("1", "true", "yes", "on"):
                            return True
                        if s in ("0", "false", "no", "off", ""):
                            return False
                    if isinstance(v, (int, float)):
                        return bool(v)
                    return False

                has_channels = 'channels' in data
                has_input_range = 'input_range' in data
                has_unit = 'unit' in data
                has_cjc_unit = 'cjc_unit' in data
                has_low_pass_filter = 'low_pass_filter' in data
                has_storage_limit_mode = 'storage_limit_mode' in data
                has_lost_beacon_timeout = 'lost_beacon_timeout' in data
                has_diagnostic_interval = 'diagnostic_interval' in data
                has_lost_beacon_enabled = 'lost_beacon_enabled' in data
                has_diagnostic_enabled = 'diagnostic_enabled' in data
                has_default_mode = 'default_mode' in data
                has_inactivity_timeout = 'inactivity_timeout' in data
                has_inactivity_enabled = 'inactivity_enabled' in data
                has_check_radio_interval = 'check_radio_interval' in data
                has_data_mode = 'data_mode' in data
                has_transducer_type = 'transducer_type' in data
                has_sensor_type = 'sensor_type' in data
                has_wire_type = 'wire_type' in data

                sample_rate = _to_opt_int(data.get('sample_rate'))
                tx_power = _to_opt_int(data.get('tx_power'))
                channels = data.get('channels')
                input_range = _to_opt_int(data.get('input_range')) if has_input_range else None
                unit = _to_opt_int(data.get('unit')) if has_unit else None
                cjc_unit = _to_opt_int(data.get('cjc_unit')) if has_cjc_unit else None
                low_pass_filter = _to_opt_int(data.get('low_pass_filter')) if has_low_pass_filter else None
                storage_limit_mode = _to_opt_int(data.get('storage_limit_mode')) if has_storage_limit_mode else None
                lost_beacon_timeout = _to_opt_int(data.get('lost_beacon_timeout')) if has_lost_beacon_timeout else None
                diagnostic_interval = _to_opt_int(data.get('diagnostic_interval')) if has_diagnostic_interval else None
                lost_beacon_enabled = _to_opt_bool(data.get('lost_beacon_enabled')) if has_lost_beacon_enabled else False
                diagnostic_enabled = _to_opt_bool(data.get('diagnostic_enabled')) if has_diagnostic_enabled else False
                default_mode = _to_opt_int(data.get('default_mode')) if has_default_mode else None
                inactivity_timeout = _to_opt_int(data.get('inactivity_timeout')) if has_inactivity_timeout else None
                inactivity_enabled = _to_opt_bool(data.get('inactivity_enabled')) if has_inactivity_enabled else False
                check_radio_interval = _to_opt_int(data.get('check_radio_interval')) if has_check_radio_interval else None
                data_mode = _to_opt_int(data.get('data_mode')) if has_data_mode else None
                transducer_type = _to_opt_int(data.get('transducer_type')) if has_transducer_type else None
                sensor_type = _to_opt_int(data.get('sensor_type')) if has_sensor_type else None
                wire_type = _to_opt_int(data.get('wire_type')) if has_wire_type else None
                cached = state.NODE_READ_CACHE.get(int(data['node_id']), {})
                if sample_rate is None or tx_power is None:
                    if sample_rate is None:
                        sample_rate = cached.get('current_rate')
                    if tx_power is None:
                        tx_power = cached.get('current_power')
                    if has_input_range and input_range is None:
                        input_range = cached.get('current_input_range')
                    if has_unit and unit is None:
                        unit = cached.get('current_unit')
                    if has_cjc_unit and cjc_unit is None:
                        cjc_unit = cached.get('current_cjc_unit')
                    if has_low_pass_filter and low_pass_filter is None:
                        low_pass_filter = cached.get('current_low_pass')
                if has_storage_limit_mode and storage_limit_mode is None:
                    storage_limit_mode = cached.get('current_storage_limit_mode')
                if has_lost_beacon_timeout and lost_beacon_timeout is None:
                    lost_beacon_timeout = cached.get('current_lost_beacon_timeout')
                if has_diagnostic_interval and diagnostic_interval is None:
                    diagnostic_interval = cached.get('current_diagnostic_interval')
                if has_default_mode and default_mode is None:
                    default_mode = cached.get('current_default_mode')
                if has_inactivity_timeout and inactivity_timeout is None:
                    inactivity_timeout = cached.get('current_inactivity_timeout')
                if has_check_radio_interval and check_radio_interval is None:
                    check_radio_interval = cached.get('current_check_radio_interval')
                if has_transducer_type and transducer_type is None:
                    transducer_type = cached.get('current_transducer_type')
                if has_sensor_type and sensor_type is None:
                    sensor_type = cached.get('current_sensor_type')
                if has_wire_type and wire_type is None:
                    wire_type = cached.get('current_wire_type')
                if has_data_mode and data_mode is None:
                    data_mode = cached.get('current_data_mode')
                if not has_lost_beacon_enabled:
                    lost_beacon_enabled = bool((lost_beacon_timeout is not None) and (int(lost_beacon_timeout) > 0))
                if not has_diagnostic_enabled:
                    diagnostic_enabled = bool((diagnostic_interval is not None) and (int(diagnostic_interval) > 0))
                if not has_inactivity_enabled:
                    inactivity_enabled = bool((inactivity_timeout is not None) and (int(inactivity_timeout) > 0))
                if sample_rate is None:
                    return jsonify(success=False, error="Sample Rate is unknown. Run FULL READ once or set node in SensorConnect.")
                if tx_power is None:
                    tx_power = 16
                model_hint = cached.get("model")
                if _is_tc_link_200_oem_model(model_hint):
                    allowed_tx = [10, 5, 0]
                else:
                    allowed_tx = [16, 10, 5, 0]
                try:
                    tx_int = int(tx_power)
                except Exception:
                    tx_int = allowed_tx[0]
                if tx_int not in allowed_tx:
                    fallback_tx = next((p for p in allowed_tx if tx_int >= p), allowed_tx[-1])
                    log(
                        f"[mscl-web] Write warn node_id={data.get('node_id')}: "
                        f"tx_power={tx_int} unsupported for model={model_hint}; using {fallback_tx} dBm"
                    )
                    tx_int = fallback_tx
                tx_power = tx_int
                if not has_channels or not isinstance(channels, list):
                    channels = [1]
                channels = [int(ch) for ch in channels if _to_opt_int(ch) in (1, 2)]
                if len(channels) == 0:
                    channels = [1]
                p_map = {16: 1, 10: 2, 5: 3, 0: 4}
                tx_enum = p_map.get(int(tx_power), 1)
                full_mask = mscl.ChannelMask()
                for ch_id in channels:
                    full_mask.enable(ch_id)

                features = None
                try:
                    features = node.features()
                except Exception:
                    features = None
                supports_default_mode = _feature_supported(features, "supportsDefaultMode") if features is not None else False
                supports_inactivity_timeout = _feature_supported(features, "supportsInactivityTimeout") if features is not None else False
                supports_check_radio_interval = _feature_supported(features, "supportsCheckRadioInterval") if features is not None else False
                supports_transducer_type = _feature_supported(features, "supportsTransducerType") if features is not None else False
                supports_temp_sensor_options = _feature_supported(features, "supportsTempSensorOptions") if features is not None else False

                write_hw_effective = {"transducer_type": None, "sensor_type": None, "wire_type": None}

                def build_config(include_default_mode=True):
                    cfg = mscl.WirelessNodeConfig()
                    cfg.samplingMode(mscl.WirelessTypes.samplingMode_sync)
                    cfg.sampleRate(int(sample_rate))
                    cfg.transmitPower(tx_enum)
                    cfg.activeChannels(full_mask)
                    hw_effective = {"transducer_type": None, "sensor_type": None, "wire_type": None}

                    if input_range is not None:
                        ir_set = False
                        ir_errs = []
                        for setter in (
                            lambda: cfg.inputRange(ch1_mask(), int(input_range)),
                            lambda: cfg.inputRange(int(input_range)),
                        ):
                            try:
                                setter()
                                ir_set = True
                                break
                            except Exception as e:
                                ir_errs.append(str(e))
                        if not ir_set:
                            raise RuntimeError("Input Range not set: " + " | ".join(ir_errs))

                    if unit is not None:
                        unit_set = False
                        unit_errs = []
                        for setter in (
                            lambda: cfg.unit(ch1_mask(), int(unit)),
                            lambda: cfg.unit(int(unit)),
                        ):
                            try:
                                setter()
                                unit_set = True
                                break
                            except Exception as e:
                                unit_errs.append(str(e))
                        if not unit_set:
                            log(f"[mscl-web] Write warn node_id={data.get('node_id')}: unit not set: {' | '.join(unit_errs)}")
                        else:
                            unit_label = UNIT_LABELS.get(int(unit), f"Value {int(unit)}")
                            log(
                                f"[mscl-web] Write unit node_id={data.get('node_id')}: "
                                f"requested={unit_label} ({int(unit)})"
                            )
                    if cjc_unit is not None:
                        cjc_set = False
                        cjc_errs = []
                        for setter in (
                            lambda: cfg.unit(ch2_mask(), int(cjc_unit)),
                        ):
                            try:
                                setter()
                                cjc_set = True
                                break
                            except Exception as e:
                                cjc_errs.append(str(e))
                        if not cjc_set:
                            log(f"[mscl-web] Write warn node_id={data.get('node_id')}: cjc_unit not set: {' | '.join(cjc_errs)}")
                        else:
                            cjc_label = UNIT_LABELS.get(int(cjc_unit), f"Value {int(cjc_unit)}")
                            log(
                                f"[mscl-web] Write cjc-unit node_id={data.get('node_id')}: "
                                f"requested={cjc_label} ({int(cjc_unit)})"
                            )

                    if low_pass_filter is not None:
                        lp_set = False
                        lp_errs = []
                        for setter in (
                            lambda: cfg.lowPassFilter(ch1_mask(), int(low_pass_filter)),
                            lambda: cfg.lowPassFilter(full_mask, int(low_pass_filter)),
                            lambda: cfg.lowPassFilter(int(low_pass_filter)),
                        ):
                            try:
                                setter()
                                lp_set = True
                                break
                            except Exception as e:
                                lp_errs.append(str(e))
                        if not lp_set:
                            raise RuntimeError("Low Pass Filter not set: " + " | ".join(lp_errs))

                    if storage_limit_mode is not None:
                        cfg.storageLimitMode(int(storage_limit_mode))
                    if lost_beacon_timeout is not None:
                        try:
                            cfg.lostBeaconTimeout(0 if not lost_beacon_enabled else int(lost_beacon_timeout))
                        except Exception as e:
                            if lost_beacon_enabled:
                                raise
                            log(f"[mscl-web] Write warn node_id={data.get('node_id')}: lostBeaconTimeout off not set: {e}")
                    if diagnostic_interval is not None:
                        try:
                            cfg.diagnosticInterval(0 if not diagnostic_enabled else int(diagnostic_interval))
                        except Exception as e:
                            if diagnostic_enabled:
                                raise
                            log(f"[mscl-web] Write warn node_id={data.get('node_id')}: diagnosticInterval off not set: {e}")

                    # SensorConnect-style behavior: try setters even when supports* reports NO.
                    nonlocal supports_default_mode, supports_inactivity_timeout, supports_check_radio_interval
                    nonlocal supports_transducer_type, supports_temp_sensor_options
                    if include_default_mode and default_mode is not None:
                        try:
                            cfg.defaultMode(int(default_mode))
                            supports_default_mode = True
                        except Exception as e:
                            log(f"[mscl-web] Write warn node_id={data.get('node_id')}: defaultMode not set: {e}")
                    if inactivity_timeout is not None:
                        try:
                            cfg.inactivityTimeout(0 if not inactivity_enabled else int(inactivity_timeout))
                            supports_inactivity_timeout = True
                        except Exception as e:
                            if inactivity_enabled:
                                log(f"[mscl-web] Write warn node_id={data.get('node_id')}: inactivityTimeout not set: {e}")
                            else:
                                log(f"[mscl-web] Write warn node_id={data.get('node_id')}: inactivityTimeout off not set: {e}")
                    if check_radio_interval is not None:
                        try:
                            cfg.checkRadioInterval(int(check_radio_interval))
                            supports_check_radio_interval = True
                        except Exception as e:
                            log(f"[mscl-web] Write warn node_id={data.get('node_id')}: checkRadioInterval not set: {e}")
                    if data_mode is not None:
                        dm_set = False
                        dm_errs = []
                        for setter in (
                            lambda: cfg.dataMode(int(data_mode)),
                            lambda: cfg.dataCollectionMethod(int(data_mode)),
                        ):
                            try:
                                setter()
                                dm_set = True
                                break
                            except Exception as e:
                                dm_errs.append(str(e))
                        if not dm_set:
                            log(f"[mscl-web] Write warn node_id={data.get('node_id')}: data_mode not set: {' | '.join(dm_errs)}")
                        else:
                            log(
                                f"[mscl-web] Write data_mode node_id={data.get('node_id')}: "
                                f"requested={DATA_MODE_LABELS.get(int(data_mode), f'Value {int(data_mode)}')} ({int(data_mode)})"
                            )

                    # Hardware -> temp sensor options (SensorConnect-style best effort)
                    if transducer_type is not None or sensor_type is not None or wire_type is not None:
                        try:
                            tso, tso_err = _get_temp_sensor_options(node)
                            if tso is None:
                                raise RuntimeError(tso_err or "getTempSensorOptions failed")

                            try:
                                cur_transducer = int(tso.transducerType())
                            except Exception:
                                cur_transducer = None
                            try:
                                cur_rtd_type = int(tso.rtdType())
                            except Exception:
                                cur_rtd_type = None
                            try:
                                cur_thermistor_type = int(tso.thermistorType())
                            except Exception:
                                cur_thermistor_type = None
                            try:
                                cur_thermocouple_type = int(tso.thermocoupleType())
                            except Exception:
                                cur_thermocouple_type = None
                            try:
                                cur_rtd_wire = int(tso.rtdWireType())
                            except Exception:
                                cur_rtd_wire = None

                            eff_transducer = int(transducer_type) if transducer_type is not None else cur_transducer
                            new_tso = None
                            if eff_transducer == _wt("transducer_rtd", 1):
                                eff_sensor = int(sensor_type) if sensor_type is not None else cur_rtd_type
                                eff_wire = int(wire_type) if wire_type is not None else cur_rtd_wire
                                if eff_sensor is None:
                                    eff_sensor = _wt("rtd_uncompensated", 0)
                                if eff_wire is None:
                                    eff_wire = _wt("rtd_2wire", 0)
                                new_tso = mscl.TempSensorOptions.RTD(int(eff_wire), int(eff_sensor))
                                hw_effective["transducer_type"] = int(eff_transducer)
                                hw_effective["sensor_type"] = int(eff_sensor)
                                hw_effective["wire_type"] = int(eff_wire)
                            elif eff_transducer == _wt("transducer_thermistor", 2):
                                eff_sensor = int(sensor_type) if sensor_type is not None else cur_thermistor_type
                                if eff_sensor is None:
                                    eff_sensor = _wt("thermistor_uncompensated", 0)
                                new_tso = mscl.TempSensorOptions.Thermistor(int(eff_sensor))
                                hw_effective["transducer_type"] = int(eff_transducer)
                                hw_effective["sensor_type"] = int(eff_sensor)
                                hw_effective["wire_type"] = None
                            elif eff_transducer == _wt("transducer_thermocouple", 0):
                                eff_sensor = int(sensor_type) if sensor_type is not None else cur_thermocouple_type
                                if eff_sensor is None:
                                    eff_sensor = 0
                                new_tso = mscl.TempSensorOptions.Thermocouple(int(eff_sensor))
                                hw_effective["transducer_type"] = int(eff_transducer)
                                hw_effective["sensor_type"] = int(eff_sensor)
                                hw_effective["wire_type"] = None
                            else:
                                raise RuntimeError(f"Unsupported transducer type: {eff_transducer}")

                            ok_tso, err_tso = _set_temp_sensor_options(cfg, new_tso)
                            if not ok_tso:
                                log(f"[mscl-web] Write warn node_id={data.get('node_id')}: tempSensorOptions not set: {err_tso}")
                            else:
                                supports_transducer_type = True
                                supports_temp_sensor_options = True
                                write_hw_effective["transducer_type"] = hw_effective["transducer_type"]
                                write_hw_effective["sensor_type"] = hw_effective["sensor_type"]
                                write_hw_effective["wire_type"] = hw_effective["wire_type"]
                                requested_hw = {
                                    "transducer_type": (int(transducer_type) if transducer_type is not None else None),
                                    "sensor_type": (int(sensor_type) if sensor_type is not None else None),
                                    "wire_type": (int(wire_type) if wire_type is not None else None),
                                }
                                if (
                                    (requested_hw["transducer_type"] is not None and requested_hw["transducer_type"] != hw_effective["transducer_type"]) or
                                    (requested_hw["sensor_type"] is not None and requested_hw["sensor_type"] != hw_effective["sensor_type"]) or
                                    (requested_hw["wire_type"] is not None and requested_hw["wire_type"] != hw_effective["wire_type"])
                                ):
                                    log(
                                        f"[mscl-web] Write hardware node_id={data.get('node_id')}: "
                                        f"requested(transducer={requested_hw['transducer_type']}, sensor={requested_hw['sensor_type']}, wire={requested_hw['wire_type']}) "
                                        f"effective(transducer={hw_effective['transducer_type']}, "
                                        f"sensor={hw_effective['sensor_type']}, wire={hw_effective['wire_type']})"
                                    )
                        except Exception as e:
                            log(f"[mscl-web] Write warn node_id={data.get('node_id')}: tempSensorOptions flow failed: {e}")
                    return cfg

                config = build_config(include_default_mode=True)
                try:
                    node.applyConfig(config)
                except Exception as e:
                    emsg = str(e)
                    if default_mode is not None and "Default Mode is not supported" in emsg:
                        log(f"[mscl-web] Write warn node_id={data.get('node_id')}: retry without Default Mode")
                        supports_default_mode = False
                        config2 = build_config(include_default_mode=False)
                        node.applyConfig(config2)
                    else:
                        raise
                # Keep UI stable after write even if subsequent EEPROM reads fail.
                cached = state.NODE_READ_CACHE.get(int(data['node_id']), {})
                cached['current_rate'] = int(sample_rate)
                cached['current_power'] = int(tx_power)
                cached['current_power_enum'] = int(tx_enum)
                if input_range is not None:
                    cached['current_input_range'] = int(input_range)
                if unit is not None:
                    cached['current_unit'] = int(unit)
                if cjc_unit is not None:
                    cached['current_cjc_unit'] = int(cjc_unit)
                if low_pass_filter is not None:
                    cached['current_low_pass'] = int(low_pass_filter)
                if storage_limit_mode is not None:
                    cached['current_storage_limit_mode'] = int(storage_limit_mode)
                if lost_beacon_timeout is not None:
                    cached['current_lost_beacon_timeout'] = (0 if not lost_beacon_enabled else int(lost_beacon_timeout))
                if diagnostic_interval is not None:
                    cached['current_diagnostic_interval'] = (0 if not diagnostic_enabled else int(diagnostic_interval))
                if supports_default_mode and default_mode is not None:
                    cached['current_default_mode'] = int(default_mode)
                if supports_inactivity_timeout and inactivity_timeout is not None:
                    cached['current_inactivity_timeout'] = (0 if not inactivity_enabled else int(inactivity_timeout))
                if supports_check_radio_interval and check_radio_interval is not None:
                    cached['current_check_radio_interval'] = int(check_radio_interval)
                if supports_transducer_type:
                    if write_hw_effective["transducer_type"] is not None:
                        cached['current_transducer_type'] = int(write_hw_effective["transducer_type"])
                    elif transducer_type is not None:
                        cached['current_transducer_type'] = int(transducer_type)
                if supports_temp_sensor_options:
                    if write_hw_effective["sensor_type"] is not None:
                        cached['current_sensor_type'] = int(write_hw_effective["sensor_type"])
                    elif sensor_type is not None:
                        cached['current_sensor_type'] = int(sensor_type)
                    if write_hw_effective["wire_type"] is not None:
                        cached['current_wire_type'] = int(write_hw_effective["wire_type"])
                    elif wire_type is not None:
                        cached['current_wire_type'] = int(wire_type)
                enabled_ids = {int(ch_id) for ch_id in channels}
                cached['channels'] = [{"id": i, "enabled": (i in enabled_ids)} for i in (1, 2)]
                cached['ts'] = time.time()
                state.NODE_READ_CACHE[int(data['node_id'])] = cached
                log(f"[mscl-web] Write success node_id={data.get('node_id')}")
                return jsonify(success=True)
            except Exception as e:
                last_err = str(e)
                log(f"[mscl-web] Write error node_id={data.get('node_id')}: {e}")
                # If the node returned an EEPROM read error, keep the base connection and retry.
                last_was_eeprom = "EEPROM" in last_err
                if last_was_eeprom:
                    metric_inc("eeprom_retries_write")
                if not last_was_eeprom:
                    mark_base_disconnected()
                time.sleep(0.5)
                continue
    return jsonify(success=False, error=last_err or "Write failed")

def run_config_server():
    _start_streamer()
    app.run(host='0.0.0.0', port=5000)


if __name__ == "__main__":
    run_config_server()
