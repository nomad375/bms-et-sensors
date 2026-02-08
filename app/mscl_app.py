from flask import Flask, render_template_string, request, jsonify # type: ignore
import fcntl
import logging
import os
from pathlib import Path
import sys
import glob
import time
import threading
from datetime import datetime, timezone

TEMPLATE_PATH = Path(__file__).parent / 'templates' / 'mscl_web_config.html'

# Подключаем MSCL
mscl_path = '/usr/lib/python3.12/dist-packages'
if mscl_path not in sys.path: 
    sys.path.append(mscl_path)

import MSCL as mscl # type: ignore

app = Flask(__name__)

# Suppress Flask request logs (GET/POST lines)
logging.getLogger("werkzeug").setLevel(logging.WARNING)

CONNECT_LOCK = threading.Lock()
INTERPROCESS_LOCK_PATH = os.getenv("MSCL_LOCK_FILE", "/var/lock/mscl/base.lock")


class SharedOpLock:
    """Thread + process lock to serialize BaseStation access across containers."""

    def __init__(self, lock_path):
        self._lock_path = lock_path
        self._thread_lock = threading.RLock()
        self._tls = threading.local()

    def __enter__(self):
        self._thread_lock.acquire()
        depth = getattr(self._tls, "depth", 0)
        if depth == 0:
            lock_dir = os.path.dirname(self._lock_path)
            if lock_dir:
                os.makedirs(lock_dir, exist_ok=True)
            fh = open(self._lock_path, "a+")
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            self._tls.fh = fh
        self._tls.depth = depth + 1
        return self

    def __exit__(self, exc_type, exc, tb):
        depth = getattr(self._tls, "depth", 1) - 1
        self._tls.depth = depth
        if depth == 0:
            fh = getattr(self._tls, "fh", None)
            if fh is not None:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                fh.close()
                self._tls.fh = None
        self._thread_lock.release()


OP_LOCK = SharedOpLock(INTERPROCESS_LOCK_PATH)

# Глобальные переменные
BASE_STATION = None
BAUDRATE = 3000000 
LOG_BUFFER = []
LOG_MAX = 200
LAST_BASE_STATUS = {"connected": False, "port": "N/A", "message": "Not connected", "ts": None}
LAST_PING_OK_TS = 0
PING_TTL_SEC = 10
CONNECT_BACKOFF_SEC = 1.5
CURRENT_PORT = None
LAST_CONNECT_ATTEMPT_TS = 0
CONNECT_MIN_INTERVAL_SEC = 2.0
NODE_READ_CACHE = {}
BASE_BEACON_STATE = None
NODE_FRESH_COMM_SEC = 45
NODE_ACTIVE_STATE_FRESH_SEC = 8
SAMPLE_STOP_TOKENS = {}
SAMPLE_RUNS = {}
IDLE_IN_PROGRESS = set()
SAMPLING_MODE_MAP = {
    "log": 2,
    "transmit": 1,
    "log_and_transmit": 3,
}
SAMPLING_MODE_LABELS = {
    "log": "Log",
    "transmit": "Transmit",
    "log_and_transmit": "Log and Transmit",
}

def log(msg):
    print(msg, flush=True)
    LOG_BUFFER.append(f"{time.strftime('%H:%M:%S')} {msg}")
    if len(LOG_BUFFER) > LOG_MAX:
        del LOG_BUFFER[0:len(LOG_BUFFER) - LOG_MAX]

RATE_MAP = {
    106: "1 Hz", 107: "2 Hz", 108: "4 Hz", 109: "8 Hz",
    110: "16 Hz", 111: "32 Hz", 112: "64 Hz", 113: "128 Hz",
    114: "256 Hz", 115: "512 Hz", 116: "1 kHz", 117: "2 kHz",
    118: "4 kHz", 119: "8 kHz", 120: "16 kHz", 121: "32 kHz",
    122: "64 kHz", 123: "128 kHz"
}
TC_LINK_200_RATE_ENUMS = {106, 107, 108, 109, 110, 111, 112, 113}
COMM_PROTOCOL_MAP = {
    0: "LXRS",
    1: "LXRS+",
}
TX_POWER_ENUM_TO_DBM = {
    0: 20,
    1: 16,
    2: 10,
    3: 5,
    4: 0,
}
INPUT_RANGE_LABELS = {
    99: "+/-1.35 V or 0 to 1 mega-ohms (Gain: 1)",
    100: "+/-1.25 V or 0 to 10000 ohms (Gain: 2)",
    101: "+/-625 mV or 0 to 3333.3 ohms (Gain: 4)",
    102: "+/-312.5 mV or 0 to 1428.6 ohms (Gain: 8)",
    103: "+/-156.25 mV or 0 to 666.67 ohms (Gain: 16)",
    0: "+/-14.545 mV",
    1: "+/-10.236 mV",
    2: "+/-7.608 mV",
    3: "+/-4.046 mV",
    4: "+/-2.008 mV",
}
PRIMARY_INPUT_RANGES = {99, 100, 101, 102, 103}
LOW_PASS_LABELS = {
    294: "294 Hz",
    291: "12.66 Hz (92db 50/60 Hz rejection)",
    289: "2.6 Hz (120db 50/60 Hz rejection)",
    # Some MSCL builds return compact enum values for the same filters.
    12: "12.66 Hz (92db 50/60 Hz rejection)",
    2: "2.6 Hz (120db 50/60 Hz rejection)",
}
STORAGE_LIMIT_LABELS = {
    0: "Overwrite",
    1: "Stop",
}
DEFAULT_MODE_LABELS = {
    0: "Idle",
    1: "Low Duty Cycle",
    5: "Sleep",
    6: "Sample (Sync)",
}
DATA_MODE_LABELS = {
    1: "Live Radio",
    2: "Datalog Only",
    3: "Live Radio + Datalog",
}

def _wt(name, default=None):
    return getattr(mscl.WirelessTypes, name, default)

TRANSDUCER_LABELS = {
    _wt("transducer_thermocouple", 0): "Thermocouple",
    _wt("transducer_rtd", 1): "RTD",
    _wt("transducer_thermistor", 2): "Thermistor",
}

RTD_SENSOR_LABELS = {
    _wt("rtd_uncompensated", 0): "Uncompensated (Resistance)",
    _wt("rtd_pt10", 1): "PT10",
    _wt("rtd_pt50", 2): "PT50",
    _wt("rtd_pt100", 3): "PT100",
    _wt("rtd_pt200", 4): "PT200",
    _wt("rtd_pt500", 5): "PT500",
    _wt("rtd_pt1000", 6): "PT1000",
}

THERMISTOR_SENSOR_LABELS = {
    _wt("thermistor_uncompensated", 0): "Uncompensated",
    _wt("thermistor_44004_44033", 1): "44004 / 44033",
    _wt("thermistor_44005_44030", 2): "44005 / 44030",
    _wt("thermistor_44007_44034", 3): "44007 / 44034",
    _wt("thermistor_44006_44031", 4): "44006 / 44031",
    _wt("thermistor_44008_44032", 5): "44008 / 44032",
    _wt("thermistor_ysi_400", 6): "YSI 400",
}

THERMOCOUPLE_SENSOR_LABELS = {
    0: "Type J",
    1: "Type K",
    2: "Type T",
    3: "Type E",
    4: "Type R",
    5: "Type S",
    6: "Type B",
    7: "Type N",
}

RTD_WIRE_LABELS = {
    _wt("rtd_2wire", 0): "2 Wire",
    _wt("rtd_3wire", 1): "3 Wire",
    _wt("rtd_4wire", 2): "4 Wire",
}

def _build_unit_labels():
    labels = {}
    try:
        for name in dir(mscl.WirelessTypes):
            if not name.startswith("unit_"):
                continue
            try:
                val = int(getattr(mscl.WirelessTypes, name))
            except Exception:
                continue
            suffix = name.split("unit_", 1)[1].replace("_", " ").strip().lower()
            compact = suffix.replace(" ", "")
            if "milliohm" in compact:
                label = "Milliohm"
            elif "kiloohm" in compact:
                label = "Kiloohm"
            elif compact.endswith("ohm") or "resistance" in compact:
                label = "Ohm"
            else:
                label = suffix.title() if suffix else f"Value {val}"
            labels[val] = label
    except Exception:
        pass
    return labels

UNIT_LABELS = _build_unit_labels()
PRIMARY_UNIT_ORDER = ["Ohm", "Milliohm", "Kiloohm"]
TEMP_UNIT_ORDER = ["Celsius", "Fahrenheit", "Kelvin"]

def _unit_family(label):
    s = str(label or "").lower().replace(" ", "")
    if "milliohm" in s:
        return "Milliohm"
    if "kiloohm" in s:
        return "Kiloohm"
    if "ohm" in s:
        return "Ohm"
    return None

def _is_temp_unit(label):
    s = str(label or "").lower()
    return ("celsius" in s) or ("fahrenheit" in s) or ("kelvin" in s)

def _get_temp_sensor_options(node):
    errs = []
    for getter in (
        lambda: node.getTempSensorOptions(ch1_mask()),
        lambda: node.getTempSensorOptions(),
    ):
        try:
            return getter(), None
        except Exception as e:
            errs.append(str(e))
    return None, " | ".join(errs) if errs else "getTempSensorOptions failed"

def _set_temp_sensor_options(cfg, opts):
    errs = []
    for setter in (
        lambda: cfg.tempSensorOptions(ch1_mask(), opts),
        lambda: cfg.tempSensorOptions(opts),
    ):
        try:
            setter()
            return True, None
        except Exception as e:
            errs.append(str(e))
    return False, " | ".join(errs) if errs else "tempSensorOptions set failed"

def _filter_default_modes(opts):
    vals = {int(x.get("value")) for x in opts if x.get("value") is not None}
    if 4 in vals:
        opts = [x for x in opts if int(x.get("value")) != 4]
    return opts

def _feature_supported(features, method_name):
    try:
        fn = getattr(features, method_name, None)
        if callable(fn):
            return bool(fn())
    except Exception:
        pass
    return False

def find_port():
    ports = glob.glob('/dev/ttyACM*') + glob.glob('/dev/ttyUSB*')
    return ports[0] if ports else None

def internal_connect(force_ping=False):
    global BASE_STATION, LAST_PING_OK_TS, CURRENT_PORT, LAST_CONNECT_ATTEMPT_TS, BASE_BEACON_STATE
    now = time.time()
    with CONNECT_LOCK:
        # If we already have a station object, periodically verify transport health.
        if BASE_STATION and not force_ping:
            if (now - LAST_PING_OK_TS) <= PING_TTL_SEC:
                return True, "Connected"
            try:
                if BASE_STATION.ping():
                    LAST_PING_OK_TS = now
                    LAST_BASE_STATUS.update({"connected": True, "message": "Connected", "ts": time.strftime("%H:%M:%S")})
                    return True, "Connected"
            except Exception as e:
                LAST_BASE_STATUS.update({
                    "connected": False,
                    "port": CURRENT_PORT or "N/A",
                    "message": f"Runtime ping failed: {e}",
                    "ts": time.strftime("%H:%M:%S"),
                })
                BASE_STATION = None
        if BASE_STATION and force_ping:
            try:
                if BASE_STATION.ping():
                    LAST_PING_OK_TS = now
                    LAST_BASE_STATUS.update({"connected": True, "message": "Connected", "ts": time.strftime("%H:%M:%S")})
                    return True, "Connected"
            except Exception as e:
                LAST_BASE_STATUS.update({
                    "connected": False,
                    "port": CURRENT_PORT or "N/A",
                    "message": f"Forced ping failed: {e}",
                    "ts": time.strftime("%H:%M:%S"),
                })
                BASE_STATION = None
        if (not force_ping) and (now - LAST_CONNECT_ATTEMPT_TS) < CONNECT_MIN_INTERVAL_SEC:
            return False, "Connect throttled"
        LAST_CONNECT_ATTEMPT_TS = now
        port = CURRENT_PORT or find_port()
        if not port:
            LAST_BASE_STATUS.update({"connected": False, "port": "N/A", "message": "No Port", "ts": time.strftime("%H:%M:%S")})
            return False, "No Port"
        try:
            log(f"[mscl-web] Connecting BaseStation on {port}...")
            conn = mscl.Connection.Serial(port, BAUDRATE)
            station = mscl.BaseStation(conn)
            station.readWriteRetries(10)
            if station.ping():
                BASE_STATION = station
                BASE_STATION.enableBeacon()
                BASE_BEACON_STATE = True
                LAST_PING_OK_TS = now
                CURRENT_PORT = port
                LAST_BASE_STATUS.update({"connected": True, "port": port, "message": "OK", "ts": time.strftime("%H:%M:%S")})
                log(f"[mscl-web] BaseStation OK port={port}")
                return True, port
            LAST_BASE_STATUS.update({"connected": False, "port": port, "message": "Ping failed", "ts": time.strftime("%H:%M:%S")})
            log("[mscl-web] BaseStation ping failed")
            time.sleep(CONNECT_BACKOFF_SEC)
            return False, "Ping failed"
        except Exception as e:
            LAST_BASE_STATUS.update({"connected": False, "port": port, "message": str(e), "ts": time.strftime("%H:%M:%S")})
            log(f"[mscl-web] BaseStation connect error: {e}")
            time.sleep(CONNECT_BACKOFF_SEC)
            return False, str(e)

def ensure_beacon_on():
    global BASE_BEACON_STATE
    if BASE_STATION is None:
        return
    if BASE_BEACON_STATE is True:
        return
    try:
        BASE_STATION.enableBeacon()
        BASE_BEACON_STATE = True
        log("[mscl-web] [BEACON] auto-enabled for node communication")
    except Exception as e:
        log(f"[mscl-web] [BEACON] auto-enable failed: {e}")

def ch1_mask():
    mask = mscl.ChannelMask()
    mask.enable(1)
    return mask

def ch2_mask():
    mask = mscl.ChannelMask()
    mask.enable(2)
    return mask

def set_idle_with_retry(node, node_id, stage_tag, attempts=2, delay_sec=0.8, required=False):
    last_err = None
    for i in range(1, attempts + 1):
        try:
            node.setToIdle()
            log(f"[mscl-web] [PREP-IDLE] {stage_tag} success node_id={node_id} attempt {i}/{attempts}")
            return True
        except Exception as e:
            last_err = str(e)
            log(f"[mscl-web] [PREP-IDLE] {stage_tag} fail node_id={node_id} attempt {i}/{attempts}: {e}")
            if i < attempts:
                time.sleep(delay_sec)
    if required:
        raise RuntimeError(f"Set to Idle failed at {stage_tag}: {last_err}")
    return False

def _node_state_info(node):
    state_map = {
        0: "Idle",
        1: "Sampling",
        2: "Sampling",
        255: "Unknown",
    }
    try:
        raw_state = node.lastDeviceState()
        try:
            state_num = int(raw_state)
            state_text = state_map.get(state_num, f"State {state_num}")
        except Exception:
            state_num = None
            state_text = str(raw_state)
        # lastDeviceState() is "last known". Use communication freshness to avoid stale Sampling.
        try:
            raw_last_comm = str(node.lastCommunicationTime())
            ts_base = raw_last_comm.split(".")[0]
            dt = datetime.strptime(ts_base, "%Y-%m-%d %H:%M:%S")
            ts_local = dt.timestamp()
            ts_utc = dt.replace(tzinfo=timezone.utc).timestamp()
            now = time.time()
            age_sec = min(abs(now - ts_local), abs(now - ts_utc))
            if age_sec > NODE_FRESH_COMM_SEC:
                return None, f"Offline (stale {int(age_sec)}s)", f"stale_last_comm={int(age_sec)}s"
            # lastDeviceState() is last known value; Sampling can remain stale for a while.
            # Treat non-idle states as unreliable if comm is not very fresh.
            if state_num in (1, 2) and age_sec > NODE_ACTIVE_STATE_FRESH_SEC:
                return None, "Unknown", f"stale_active_state={int(age_sec)}s"
        except Exception:
            # If freshness cannot be computed, keep state fallback behavior.
            pass
        return state_num, state_text, None
    except Exception as e:
        return None, None, str(e)

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
        errors.append(f"startSync={e}")
    try:
        if callable(getattr(node, "resendStartSyncSampling", None)):
            node.resendStartSyncSampling()
            log(f"[mscl-web] [SAMPLE] resendStartSyncSampling sent node_id={node_id}")
            return "sync-resend"
    except Exception as e:
        errors.append(f"resendSync={e}")
    try:
        if callable(getattr(node, "startNonSyncSampling", None)):
            node.startNonSyncSampling()
            log(f"[mscl-web] [SAMPLE] startNonSyncSampling sent node_id={node_id}")
            return "non-sync"
    except Exception as e:
        errors.append(f"non-sync={e}")
    raise RuntimeError("; ".join(errors) if errors else "No sampling start method available")

def _schedule_idle_after(node_id, seconds, token):
    if seconds <= 0:
        return
    time.sleep(seconds)
    with OP_LOCK:
        if SAMPLE_STOP_TOKENS.get(node_id) != token:
            return
        ok, msg = internal_connect()
        if not ok or BASE_STATION is None:
            log(f"[mscl-web] [SAMPLE] auto-idle skipped node_id={node_id}: {msg}")
            return
        try:
            ensure_beacon_on()
            node = mscl.WirelessNode(node_id, BASE_STATION)
            node.readWriteRetries(10)
            node.setToIdle()
            log(f"[mscl-web] [SAMPLE] auto-idle sent node_id={node_id} after {seconds}s")
        except Exception as e:
            log(f"[mscl-web] [SAMPLE] auto-idle failed node_id={node_id}: {e}")


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


def _set_sampling_mode_on_node(cfg, mode_key):
    mode_key = str(mode_key or "transmit").lower()
    mode_value = SAMPLING_MODE_MAP.get(mode_key, SAMPLING_MODE_MAP["transmit"])
    errs = []
    for setter in (
        lambda: cfg.dataMode(int(mode_value)),
        lambda: cfg.dataCollectionMethod(int(mode_value)),
    ):
        try:
            setter()
            return mode_value, None
        except Exception as e:
            errs.append(str(e))
    return None, " | ".join(errs) if errs else "no setter available"


def _is_tc_link_200_model(model):
    s = str(model or "").strip().lower()
    return ("tc-link-200" in s) or s.startswith("63104100")


def _filter_sample_rates_for_model(model, supported_rates, current_rate):
    rates = list(supported_rates or [])
    if not _is_tc_link_200_model(model):
        return rates

    filtered = []
    for r in rates:
        try:
            rid = int(r.get("enum_val"))
        except Exception:
            continue
        if rid in TC_LINK_200_RATE_ENUMS:
            filtered.append(r)

    # Keep currently reported rate visible even if it is outside filtered list.
    if current_rate is not None:
        try:
            cur = int(current_rate)
            if all(int(x.get("enum_val")) != cur for x in filtered):
                filtered.insert(0, {"enum_val": cur, "str_val": RATE_MAP.get(cur, f"{cur} (current)")})
        except Exception:
            pass
    return filtered


def _start_sampling_run(node_id, body):
    global BASE_STATION
    ok, msg = internal_connect()
    if not ok or BASE_STATION is None:
        return {"success": False, "error": f"Base station not connected: {msg}"}

    mode_key = str(body.get("log_transmit_mode") or "transmit").lower()
    data_type = str(body.get("data_type") or "float").lower()
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
        node = mscl.WirelessNode(node_id, BASE_STATION)
        node.readWriteRetries(10)
        cfg = mscl.WirelessNodeConfig()
        # Keep runtime sampling config explicit and minimal.
        try:
            cfg.samplingMode(mscl.WirelessTypes.samplingMode_sync)
        except Exception:
            pass

        sample_rate_set = False
        if sample_rate is not None:
            try:
                cfg.sampleRate(int(sample_rate))
                sample_rate_set = True
            except Exception as e:
                log(f"[mscl-web] [S-RUN] sampleRate not set node_id={node_id}: {e}")

        mode_value, mode_err = _set_sampling_mode_on_node(cfg, mode_key)
        if mode_value is None:
            log(f"[mscl-web] [S-RUN] mode not set node_id={node_id}: {mode_err}")

        apply_ok = False
        try:
            node.applyConfig(cfg)
            apply_ok = True
        except Exception as e:
            # Keep behavior permissive: if apply fails, try start anyway.
            log(f"[mscl-web] [S-RUN] applyConfig warning node_id={node_id}: {e}")

        start_mode = _start_sampling_best_effort(node, node_id)
        token = time.time()
        SAMPLE_STOP_TOKENS[node_id] = token
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
        SAMPLE_RUNS[node_id] = run
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
def index(): return render_template_string(TEMPLATE_PATH.read_text())

@app.route('/api/connect', methods=['POST'])
def api_connect():
    with OP_LOCK:
        s, p = internal_connect()
        return jsonify(success=s, port=p)

@app.route('/api/status')
def api_status():
    with OP_LOCK:
        ok = BASE_STATION is not None
        msg = LAST_BASE_STATUS.get("message", "Not connected")
        port = LAST_BASE_STATUS.get("port", "N/A")
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
        if ok and BASE_STATION is not None:
            try:
                base_model = str(BASE_STATION.model())
            except Exception:
                base_model = None
            try:
                base_fw = str(BASE_STATION.firmwareVersion())
            except Exception:
                base_fw = None
            try:
                base_serial = str(BASE_STATION.serial())
            except Exception:
                try:
                    base_serial = str(BASE_STATION.serialNumber())
                except Exception:
                    base_serial = None
            try:
                base_region = str(BASE_STATION.regionCode())
            except Exception:
                base_region = None
            try:
                base_radio = str(BASE_STATION.frequency())
            except Exception:
                base_radio = None
            try:
                base_last_comm = _trim_ts(BASE_STATION.lastCommunicationTime())
            except Exception:
                base_last_comm = None
            try:
                base_link = str(BASE_STATION.lastDeviceState())
            except Exception:
                base_link = None
            if LAST_PING_OK_TS > 0:
                ping_age_sec = max(0.0, now - LAST_PING_OK_TS)
            comm_age_sec = _comm_age_sec(base_last_comm)
            if ping_age_sec is not None and ping_age_sec <= PING_TTL_SEC:
                link_health = "healthy"
                link_health_reason = f"Ping fresh ({ping_age_sec:.1f}s)"
            elif ping_age_sec is not None and ping_age_sec <= (PING_TTL_SEC * 3):
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
            beacon_state=BASE_BEACON_STATE,
            base_connection=f"Serial, {port}, {BAUDRATE}" if port and port != "N/A" else None,
            ts=LAST_BASE_STATUS.get("ts"),
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
    global BASE_STATION, LAST_PING_OK_TS, BASE_BEACON_STATE
    with OP_LOCK:
        BASE_STATION = None
        BASE_BEACON_STATE = None
        LAST_PING_OK_TS = 0
        ok, msg = internal_connect(force_ping=True)
        return jsonify(success=bool(ok), message=msg)

@app.route('/api/beacon', methods=['POST'])
def api_beacon():
    global BASE_STATION, BASE_BEACON_STATE
    with OP_LOCK:
        ok, msg = internal_connect()
        if not ok or BASE_STATION is None:
            return jsonify(success=False, error=f"Base station not connected: {msg}")
        body = request.json or {}
        requested = body.get("enabled", None)
        if requested is None:
            target = not bool(BASE_BEACON_STATE)
        else:
            target = bool(requested)
        try:
            if target:
                BASE_STATION.enableBeacon()
                BASE_BEACON_STATE = True
                log("[mscl-web] [BEACON] enabled")
                return jsonify(success=True, beacon_state=True, message="Beacon ON")
            disable_methods = ("disableBeacon", "setBeaconOff")
            disabled = False
            for m in disable_methods:
                fn = getattr(BASE_STATION, m, None)
                if callable(fn):
                    fn()
                    disabled = True
                    break
            if not disabled:
                return jsonify(success=False, error="Beacon OFF is not supported by this MSCL API build")
            BASE_BEACON_STATE = False
            log("[mscl-web] [BEACON] disabled")
            return jsonify(success=True, beacon_state=False, message="Beacon OFF")
        except Exception as e:
            return jsonify(success=False, error=str(e))

@app.route('/api/diagnostics/<int:node_id>')
def api_diagnostics(node_id):
    with OP_LOCK:
        ok, msg = internal_connect()
        if not ok or BASE_STATION is None:
            return jsonify(success=False, error=f"Base station not connected: {msg}")
        try:
            node = mscl.WirelessNode(node_id, BASE_STATION)
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
    return jsonify(logs=LOG_BUFFER[-LOG_MAX:])

@app.route('/api/read/<int:node_id>')
def api_read(node_id):
    global BASE_STATION, RATE_MAP, NODE_READ_CACHE
    read_tag = "READ"
    max_attempts = 5
    last_err = None
    log(f"[mscl-web] [{read_tag}] request node_id={node_id}")
    with OP_LOCK:
        cached = NODE_READ_CACHE.get(node_id, {})
        refresh_eeprom = True
        for attempt in range(1, max_attempts + 1):
            if attempt > 1:
                log(f"[mscl-web] [{read_tag}] retry {attempt}/{max_attempts} node_id={node_id}")
            ok, msg = internal_connect()
            if not ok or BASE_STATION is None:
                last_err = f"Base station not connected: {msg}"
                log(f"[mscl-web] [{read_tag}] failed: {last_err}")
                time.sleep(0.5)
                continue
            try:
                ensure_beacon_on()
                node = mscl.WirelessNode(node_id, BASE_STATION)
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
                            if "EEPROM" not in last_err:
                                BASE_STATION = None
                                LAST_PING_OK_TS = 0
                                time.sleep(0.5)
                                continue
                try:
                    active_mask = node.getActiveChannels()
                except Exception:
                    active_mask = None
        
                # 3. Остальные поля — best-effort (не ломаем чтение при EEPROM ошибках)
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
                state, state_text, _ = _node_state_info(node)
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
                                {"value": 6, "label": "Sample (Sync)"},
                            ]
                            default_mode_options = _filter_default_modes(default_mode_options)
                    except Exception as e:
                        log(f"[mscl-web] [{read_tag}] warn node_id={node_id}: features/defaultModes failed: {e}")
                        if not default_mode_options:
                            default_mode_options = [
                                {"value": 0, "label": "Idle"},
                                {"value": 5, "label": "Sleep"},
                                {"value": 6, "label": "Sample (Sync)"},
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

                # Частоты (если доступно)
                supported_rates = cached.get("supported_rates", [])
                if (refresh_eeprom or not supported_rates) and current_rate is not None:
                    supported_rates = [{"enum_val": current_rate, "str_val": RATE_MAP.get(current_rate, str(current_rate) + " Hz")}]
                    try:
                        features = node.features()
                        rates = features.sampleRates(mscl.WirelessTypes.samplingMode_sync, 1, 0)
                        supported_rates = []
                        for r in rates:
                            rid = int(r)
                            supported_rates.append({"enum_val": rid, "str_val": RATE_MAP.get(rid, str(rid) + " Hz")})
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
                    region=region, last_comm=last_comm, state=state, state_text=state_text,
                    node_address=node_address, frequency=frequency,
                    storage_pct=storage_pct, storage_capacity_raw=storage_capacity_raw, sampling_mode=sampling_mode, sampling_mode_raw=sampling_mode_raw,
                    current_data_mode=current_data_mode, data_mode_text=data_mode_text, data_mode_options=data_mode_options,
                    current_input_range=current_input_range, supported_input_ranges=supported_input_ranges,
                    current_unit=current_unit, unit_options=unit_options,
                    current_cjc_unit=current_cjc_unit, cjc_unit_options=cjc_unit_options,
                    current_rate=current_rate, current_power=current_power, current_power_enum=current_power_enum,
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
                NODE_READ_CACHE[node_id] = dict(payload, ts=time.time())
                log(f"[mscl-web] [{read_tag}] success node_id={node_id} sample_rate={payload.get('current_rate')} fw={payload.get('fw')}")
                return jsonify(**payload)
            except Exception as e:
                last_err = str(e)
                log(f"[mscl-web] [{read_tag}] error node_id={node_id}: {e}")
                if "EEPROM" in last_err:
                    backoff = min(4.0, 0.5 * (2 ** (attempt - 1)))
                    time.sleep(backoff)
                    continue
                BASE_STATION = None
                LAST_PING_OK_TS = 0
                time.sleep(0.5)
                continue
    if last_err:
        log(f"[mscl-web] [{read_tag}] failed node_id={node_id}: {last_err}")
    else:
        log(f"[mscl-web] [{read_tag}] failed node_id={node_id}: Read failed")
    return jsonify(success=False, error=last_err or "Read failed")

@app.route('/api/probe/<int:node_id>')
def api_probe(node_id):
    global BASE_STATION
    log(f"[mscl-web] Probe request node_id={node_id}")
    with OP_LOCK:
        ok, msg = internal_connect()
        if not ok or BASE_STATION is None:
            err = f"Base station not connected: {msg}"
            log(f"[mscl-web] Probe failed: {err}")
            return jsonify(success=False, error=err)
        try:
            ensure_beacon_on()
            node = mscl.WirelessNode(node_id, BASE_STATION)
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
    global BASE_STATION
    with OP_LOCK:
        if node_id in IDLE_IN_PROGRESS:
            return jsonify(success=False, error="Set to Idle already in progress")
        IDLE_IN_PROGRESS.add(node_id)
        ok, msg = internal_connect()
        if not ok or BASE_STATION is None:
            IDLE_IN_PROGRESS.discard(node_id)
            return jsonify(success=False, error=f"Base station not connected: {msg}")
        try:
            ensure_beacon_on()
            node = mscl.WirelessNode(node_id, BASE_STATION)
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
            IDLE_IN_PROGRESS.discard(node_id)

@app.route('/api/node_cycle_power/<int:node_id>', methods=['POST'])
def api_node_cycle_power(node_id):
    global BASE_STATION
    with OP_LOCK:
        ok, msg = internal_connect()
        if not ok or BASE_STATION is None:
            return jsonify(success=False, error=f"Base station not connected: {msg}")
        try:
            ensure_beacon_on()
            node = mscl.WirelessNode(node_id, BASE_STATION)
            node.readWriteRetries(10)
            node.cyclePower()
            log(f"[mscl-web] [PREP-CYCLE] success node_id={node_id}")
            return jsonify(success=True, message="Power cycle command sent")
        except Exception as e:
            log(f"[mscl-web] [PREP-CYCLE] failed node_id={node_id}: {e}")
            return jsonify(success=False, error=str(e))

@app.route('/api/node_sampling/<int:node_id>', methods=['POST'])
def api_node_sampling(node_id):
    with OP_LOCK:
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
    with OP_LOCK:
        body = request.json or {}
        res = _start_sampling_run(node_id, body)
        if not res.get("success"):
            return jsonify(success=False, error=res.get("error", "Sampling start failed"))
        return jsonify(success=True, run=res["run"])


@app.route('/api/sampling/stop/<int:node_id>', methods=['POST'])
def api_sampling_stop(node_id):
    global BASE_STATION
    with OP_LOCK:
        ok, msg = internal_connect()
        if not ok or BASE_STATION is None:
            return jsonify(success=False, error=f"Base station not connected: {msg}")
        try:
            ensure_beacon_on()
            node = mscl.WirelessNode(node_id, BASE_STATION)
            node.readWriteRetries(10)
            idle_status = send_idle_sensorconnect_style(node, node_id, "stop-sampling")
            SAMPLE_STOP_TOKENS[node_id] = time.time()
            run = SAMPLE_RUNS.get(node_id, {})
            run.update({
                "state": "stopped",
                "stopped_at": int(time.time()),
                "idle_result": idle_status.get("idle_result"),
            })
            SAMPLE_RUNS[node_id] = run
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
    global BASE_STATION
    with OP_LOCK:
        state_num = None
        state_text = "Unknown"
        freshness_reason = None
        link_state = "offline"
        ok, msg = internal_connect(force_ping=False)
        if ok and BASE_STATION is not None:
            try:
                node = mscl.WirelessNode(node_id, BASE_STATION)
                node.readWriteRetries(5)
                state_num, state_text, freshness_reason = _node_state_info(node)
                link_state = "ok"
            except Exception as e:
                link_state = f"degraded: {e}"
        else:
            link_state = f"offline: {msg}"

        run = SAMPLE_RUNS.get(node_id, {})
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
    global BASE_STATION
    with OP_LOCK:
        ok, msg = internal_connect()
        if not ok or BASE_STATION is None:
            return jsonify(success=False, error=f"Base station not connected: {msg}")
        try:
            ensure_beacon_on()
            node = mscl.WirelessNode(node_id, BASE_STATION)
            node.readWriteRetries(10)
            node.sleep()
            log(f"[mscl-web] [SLEEP] sleep command sent node_id={node_id}")
            return jsonify(success=True, message="Sleep command sent")
        except Exception as e:
            log(f"[mscl-web] [SLEEP] failed node_id={node_id}: {e}")
            return jsonify(success=False, error=str(e))

@app.route('/api/clear_storage/<int:node_id>', methods=['POST'])
def api_clear_storage(node_id):
    global BASE_STATION, NODE_READ_CACHE
    with OP_LOCK:
        ok, msg = internal_connect()
        if not ok or BASE_STATION is None:
            return jsonify(success=False, error=f"Base station not connected: {msg}")
        try:
            ensure_beacon_on()
            node = mscl.WirelessNode(node_id, BASE_STATION)
            node.readWriteRetries(15)
            set_idle_with_retry(node, node_id, "before-clear-storage", attempts=2, delay_sec=0.8, required=False)
            node.erase()
            set_idle_with_retry(node, node_id, "after-clear-storage", attempts=2, delay_sec=0.8, required=False)
            cached = NODE_READ_CACHE.get(node_id, {})
            cached["storage_pct"] = 0.0
            cached["ts"] = time.time()
            NODE_READ_CACHE[node_id] = cached
            log(f"[mscl-web] [CLEAR-STORAGE] success node_id={node_id}")
            return jsonify(success=True, message="Storage cleared")
        except Exception as e:
            log(f"[mscl-web] [CLEAR-STORAGE] failed node_id={node_id}: {e}")
            return jsonify(success=False, error=str(e))

@app.route('/api/write', methods=['POST'])
def api_write():
    global BASE_STATION, NODE_READ_CACHE
    data = request.json
    last_err = None
    last_was_eeprom = False
    log(f"[mscl-web] Write request node_id={data.get('node_id')}")
    with OP_LOCK:
        for attempt in range(1, 6):
            if attempt > 1:
                log(f"[mscl-web] Write retry {attempt}/5 node_id={data.get('node_id')}")
            # If last attempt failed with EEPROM read error, pause before retry.
            if last_was_eeprom:
                backoff = min(4.0, 0.5 * (2 ** (attempt - 1)))
                time.sleep(backoff)
            ok, msg = internal_connect()
            if not ok or BASE_STATION is None:
                last_err = f"Base station not connected: {msg}"
                log(f"[mscl-web] Write failed: {last_err}")
                time.sleep(0.5)
                continue
            try:
                ensure_beacon_on()
                node = mscl.WirelessNode(int(data['node_id']), BASE_STATION)
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

                has_sample_rate = 'sample_rate' in data
                has_tx_power = 'tx_power' in data
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
                cached = NODE_READ_CACHE.get(int(data['node_id']), {})
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
                if int(tx_power) > 16:
                    log(f"[mscl-web] Write warn node_id={data.get('node_id')}: tx_power={tx_power} exceeds node limit, clamped to 16 dBm")
                    tx_power = 16
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
                cached = NODE_READ_CACHE.get(int(data['node_id']), {})
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
                NODE_READ_CACHE[int(data['node_id'])] = cached
                log(f"[mscl-web] Write success node_id={data.get('node_id')}")
                return jsonify(success=True)
            except Exception as e:
                last_err = str(e)
                log(f"[mscl-web] Write error node_id={data.get('node_id')}: {e}")
                # If the node returned an EEPROM read error, keep the base connection and retry.
                last_was_eeprom = "EEPROM" in last_err
                if not last_was_eeprom:
                    BASE_STATION = None
                    LAST_PING_OK_TS = 0
                time.sleep(0.5)
                continue
    return jsonify(success=False, error=last_err or "Write failed")

def run_config_server():
    app.run(host='0.0.0.0', port=5000)


if __name__ == "__main__":
    run_config_server()
