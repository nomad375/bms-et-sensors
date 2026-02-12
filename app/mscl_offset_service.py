import json
import time

def load_persisted_export_offset_ns(
    node_id,
    influx_url,
    influx_token,
    influx_org,
    influx_bucket,
    measurement,
    metric,
    log_func,
):
    if not all([influx_token, influx_org, influx_bucket]):
        return None
    node_tag = str(int(node_id))
    bucket_q = json.dumps(influx_bucket)
    measurement_q = json.dumps(measurement)
    node_q = json.dumps(node_tag)
    metric_q = json.dumps(metric)
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
        from influxdb_client import InfluxDBClient  # type: ignore

        with InfluxDBClient(url=influx_url, token=influx_token, org=influx_org) as db_client:
            for rec in db_client.query_api().query_stream(query=flux, org=influx_org):
                try:
                    return int(rec.get_value())
                except Exception:
                    continue
    except Exception as e:
        log_func(f"[mscl-web] [EXPORT-STORAGE] offset-load failed node_id={node_id}: {e}")
    return None


def persist_export_offset_ns(
    node_id,
    offset_ns,
    influx_url,
    influx_token,
    influx_org,
    influx_bucket,
    measurement,
    metric,
    log_func,
):
    if not all([influx_token, influx_org, influx_bucket]):
        return
    try:
        node_tag = str(int(node_id))
        off = int(offset_ns)
    except Exception:
        return
    try:
        from influxdb_client import InfluxDBClient, Point  # type: ignore
        from influxdb_client.client.write_api import SYNCHRONOUS  # type: ignore
        from influxdb_client.domain.write_precision import WritePrecision  # type: ignore

        point = (
            Point(measurement)
            .tag("node_id", node_tag)
            .tag("metric", metric)
            .field("value", off)
            .time(time.time_ns(), WritePrecision.NS)
        )
        with InfluxDBClient(url=influx_url, token=influx_token, org=influx_org) as db_client:
            db_client.write_api(write_options=SYNCHRONOUS).write(influx_bucket, influx_org, [point])
    except Exception as e:
        log_func(f"[mscl-web] [EXPORT-STORAGE] offset-persist failed node_id={node_id}: {e}")


def compute_export_clock_offset_ns(
    rows,
    node_id,
    min_skew_sec,
    recalc_threshold_sec,
    recalc_max_skew_sec,
    cache,
    load_persisted_fn,
    persist_fn,
    log_func,
    now_ns=None,
):
    """Estimate node->host clock offset using newest datalog timestamp."""
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

    if now_ns is None:
        now_ns = int(time.time_ns())
    skew_ns = int(now_ns) - int(max_node_ts)
    min_skew_ns = int(float(min_skew_sec) * 1_000_000_000)
    drift_threshold_ns = int(max(0.0, float(recalc_threshold_sec)) * 1_000_000_000)
    recalc_max_skew_ns = int(max(0.0, float(recalc_max_skew_sec)) * 1_000_000_000)

    chosen = 0 if abs(skew_ns) <= min_skew_ns else int(skew_ns)
    node_key = None
    try:
        if node_id is not None:
            node_key = int(node_id)
    except Exception:
        node_key = None

    def should_recalc(existing_offset_ns):
        if abs(skew_ns) > recalc_max_skew_ns:
            return False
        return abs(int(existing_offset_ns) - int(skew_ns)) > drift_threshold_ns

    if node_key is not None:
        cached = cache.get(node_key)
        if cached is not None:
            try:
                cached_i = int(cached)
                if should_recalc(cached_i):
                    cache[node_key] = int(chosen)
                    persist_fn(node_key, chosen)
                    log_func(
                        f"[mscl-web] [EXPORT-STORAGE] offset-recalc node_id={node_key} "
                        f"from={cached_i} to={chosen} skew_ns={skew_ns}"
                    )
                    return int(chosen), skew_ns
                return cached_i, skew_ns
            except Exception:
                pass

        persisted = load_persisted_fn(node_key)
        if persisted is not None:
            try:
                persisted_i = int(persisted)
                if should_recalc(persisted_i):
                    cache[node_key] = int(chosen)
                    persist_fn(node_key, chosen)
                    log_func(
                        f"[mscl-web] [EXPORT-STORAGE] offset-refresh node_id={node_key} "
                        f"from={persisted_i} to={chosen} skew_ns={skew_ns}"
                    )
                    return int(chosen), skew_ns
                cache[node_key] = persisted_i
                return persisted_i, skew_ns
            except Exception:
                pass

    if node_key is not None:
        cache[node_key] = int(chosen)
        persist_fn(node_key, chosen)
    return chosen, skew_ns


__all__ = [
    "compute_export_clock_offset_ns",
    "load_persisted_export_offset_ns",
    "persist_export_offset_ns",
]
