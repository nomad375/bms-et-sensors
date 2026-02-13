import os


def _env_bool(name, default=False):
    raw = os.getenv(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() not in ("0", "false", "no", "off")


def _env_int(name, default):
    raw = os.getenv(name)
    if raw is None:
        return int(default)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return int(default)


def _env_float(name, default):
    raw = os.getenv(name)
    if raw is None:
        return float(default)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return float(default)


INFLUX_URL = os.getenv("INFLUX_URL", "http://influxdb:8086")
INFLUX_TOKEN = os.getenv("INFLUX_TOKEN")
INFLUX_ORG = os.getenv("INFLUX_ORG")
INFLUX_BUCKET = os.getenv("INFLUX_BUCKET")

MSCL_MEASUREMENT = os.getenv("MSCL_MEASUREMENT", "mscl_sensors")
MSCL_ONLY_CHANNEL_1 = _env_bool("MSCL_ONLY_CHANNEL_1", False)
MSCL_STREAM_ENABLED = _env_bool("MSCL_STREAM_ENABLED", True)
MSCL_STREAM_READ_TIMEOUT_MS = _env_int("MSCL_STREAM_READ_TIMEOUT_MS", 20)
MSCL_STREAM_IDLE_SLEEP = _env_float("MSCL_STREAM_IDLE_SLEEP", 0.005)
MSCL_STREAM_BATCH_SIZE = _env_int("MSCL_STREAM_BATCH_SIZE", 5000)
MSCL_STREAM_FLUSH_INTERVAL_MS = _env_int("MSCL_STREAM_FLUSH_INTERVAL_MS", 500)
MSCL_STREAM_QUEUE_MAX = _env_int("MSCL_STREAM_QUEUE_MAX", 5000)
MSCL_STREAM_QUEUE_WAIT_MS = _env_int("MSCL_STREAM_QUEUE_WAIT_MS", 200)
MSCL_STREAM_DROP_WARN_SEC = _env_float("MSCL_STREAM_DROP_WARN_SEC", 30.0)
MSCL_STREAM_DROP_LOG_THROTTLE_SEC = _env_float("MSCL_STREAM_DROP_LOG_THROTTLE_SEC", 30.0)
MSCL_STREAM_LOG_INTERVAL_SEC = _env_float("MSCL_STREAM_LOG_INTERVAL_SEC", 5.0)
MSCL_RESAMPLED_ENABLED = _env_bool("MSCL_RESAMPLED_ENABLED", True)
MSCL_RESAMPLED_MEASUREMENT = os.getenv("MSCL_RESAMPLED_MEASUREMENT", "mscl_sensors_resampled")
MSCL_RESAMPLED_INCLUDE_RAW_TS = _env_bool("MSCL_RESAMPLED_INCLUDE_RAW_TS", True)

MSCL_EXPORT_ALIGN_MIN_SKEW_SEC = _env_float("MSCL_EXPORT_ALIGN_MIN_SKEW_SEC", 2.0)
MSCL_EXPORT_OFFSET_RECALC_THRESHOLD_SEC = _env_float("MSCL_EXPORT_OFFSET_RECALC_THRESHOLD_SEC", 3.0)
MSCL_EXPORT_OFFSET_RECALC_MAX_SKEW_SEC = _env_float("MSCL_EXPORT_OFFSET_RECALC_MAX_SKEW_SEC", 30.0)
MSCL_EXPORT_INFLUX_BATCH = _env_int("MSCL_EXPORT_INFLUX_BATCH", 5000)

MSCL_SOURCE_RADIO = os.getenv("MSCL_SOURCE_RADIO", "mscl_config_stream")
MSCL_SOURCE_NODE_EXPORT = os.getenv("MSCL_SOURCE_NODE_EXPORT", "mscl_node_export")
MSCL_META_MEASUREMENT = os.getenv("MSCL_META_MEASUREMENT", "mscl_meta")
MSCL_META_OFFSET_METRIC = "node_export_clock_offset_ns"
