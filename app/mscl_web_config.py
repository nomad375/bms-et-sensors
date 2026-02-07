from flask import Flask, render_template_string, request, jsonify # type: ignore
import logging
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
OP_LOCK = threading.Lock()

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
IDLE_IN_PROGRESS = set()

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

    try:
        status = node.setToIdle()
        command_sent = True
        log(f"[mscl-web] [PREP-IDLE] {stage_tag} node setToIdle started node_id={node_id}")
    except Exception as e:
        last_reason = f"setToIdle failed: {e}"
        log(f"[mscl-web] [PREP-IDLE] {stage_tag} node setToIdle failed node_id={node_id}: {e}")
        return {
            "command_sent": command_sent,
            "transport_alive": transport_alive,
            "state_confirmed": state_confirmed,
            "state_text": state_text,
            "reason": last_reason,
        }

    complete = False
    for poll in range(1, 41):  # about 12s max (40 * 300ms)
        try:
            if status.complete(300):
                complete = True
                transport_alive = True
                break
            if poll % 3 == 0:
                log(f"[mscl-web] [PREP-IDLE] {stage_tag} waiting node_id={node_id} poll {poll}/40")
        except Exception as e:
            last_reason = f"status.complete failed: {e}"
            log(f"[mscl-web] [PREP-IDLE] {stage_tag} waiting node_id={node_id} poll {poll}/40 ({last_reason})")
            break

    if not complete:
        return {
            "command_sent": command_sent,
            "transport_alive": transport_alive,
            "state_confirmed": False,
            "state_text": state_text,
            "reason": last_reason,
        }

    try:
        result = status.result()
        success_val = getattr(mscl.SetToIdleStatus, "setToIdleResult_success", None)
        canceled_val = getattr(mscl.SetToIdleStatus, "setToIdleResult_canceled", None)

        if result == success_val:
            state_confirmed = True
            state_text = "Idle"
            last_reason = "confirmed:status.result=success"
            log(f"[mscl-web] [PREP-IDLE] {stage_tag} confirmed node_id={node_id} by status.result")
        elif canceled_val is not None and result == canceled_val:
            last_reason = "status.result=canceled"
            log(f"[mscl-web] [PREP-IDLE] {stage_tag} canceled node_id={node_id}")
        else:
            last_reason = f"status.result={result}"
            log(f"[mscl-web] [PREP-IDLE] {stage_tag} not-confirmed node_id={node_id} ({last_reason})")
    except Exception as e:
        last_reason = f"status.result failed: {e}"
        log(f"[mscl-web] [PREP-IDLE] {stage_tag} result read failed node_id={node_id}: {e}")

    return {
        "command_sent": command_sent,
        "transport_alive": transport_alive,
        "state_confirmed": state_confirmed,
        "state_text": state_text,
        "reason": last_reason,
    }

def _start_sampling_best_effort(node, node_id):
    """Try sync-first sampling start, then fallback to non-sync start."""
    errors = []
    try:
        if callable(getattr(node, "resendStartSyncSampling", None)):
            node.resendStartSyncSampling()
            log(f"[mscl-web] [SAMPLE] resendStartSyncSampling sent node_id={node_id}")
            return "sync"
    except Exception as e:
        errors.append(f"sync={e}")
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
            ]
            out = []
            for label, fn in flags:
                try:
                    out.append({"name": label, "value": bool(getattr(features, fn)())})
                except Exception:
                    out.append({"name": label, "value": False})
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
            log(f"[mscl-web] [{read_tag}] attempt {attempt}/{max_attempts} node_id={node_id}")
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
                data_mode = cached.get("data_mode")
                if refresh_eeprom or "data_mode" not in cached:
                    try:
                        data_mode = str(node.getDataMode())
                    except Exception:
                        pass
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

                current_diagnostic_interval = cached.get("current_diagnostic_interval")
                try:
                    current_diagnostic_interval = int(node.getDiagnosticInterval())
                except Exception as e:
                    log(f"[mscl-web] [{read_tag}] warn node_id={node_id}: getDiagnosticInterval failed: {e}")
                if current_diagnostic_interval is None:
                    current_diagnostic_interval = 60

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
            
                channels = []
                if active_mask is not None:
                    for i in range(1, 3):
                        channels.append({"id": i, "enabled": active_mask.enabled(i)})
                elif isinstance(cached.get("channels"), list) and cached.get("channels"):
                    channels = cached.get("channels")
                else:
                    channels = [{"id": 1, "enabled": True}, {"id": 2, "enabled": False}]
        
                payload = dict(
                    success=True, model=model, sn=sn, fw=fw,
                    region=region, last_comm=last_comm, state=state, state_text=state_text,
                    node_address=node_address, frequency=frequency,
                    storage_pct=storage_pct, sampling_mode=sampling_mode, sampling_mode_raw=sampling_mode_raw, data_mode=data_mode,
                    current_input_range=current_input_range, supported_input_ranges=supported_input_ranges,
                    current_rate=current_rate, current_power=current_power, current_power_enum=current_power_enum,
                    comm_protocol=comm_protocol, comm_protocol_text=comm_protocol_text,
                    supported_rates=supported_rates, channels=channels,
                    current_low_pass=current_low_pass, low_pass_options=low_pass_options,
                    current_storage_limit_mode=current_storage_limit_mode, storage_limit_options=storage_limit_options,
                    current_lost_beacon_timeout=current_lost_beacon_timeout,
                    current_diagnostic_interval=current_diagnostic_interval,
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
            if not sent:
                log(f"[mscl-web] [PREP-IDLE] failed node_id={node_id} reason={reason}")
                return jsonify(success=False, error=reason, idle_confirmed=False, idle_status=idle_status)
            if confirmed:
                log(f"[mscl-web] [PREP-IDLE] success node_id={node_id}")
                return jsonify(success=True, message="Node set to Idle", idle_confirmed=True, reason=reason, idle_status=idle_status)
            log(f"[mscl-web] [PREP-IDLE] pending node_id={node_id} reason={reason}")
            return jsonify(success=True, message="Idle command sent", idle_confirmed=False, reason=reason, idle_status=idle_status)
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
    global BASE_STATION
    with OP_LOCK:
        ok, msg = internal_connect()
        if not ok or BASE_STATION is None:
            return jsonify(success=False, error=f"Base station not connected: {msg}")
        body = request.json or {}
        try:
            duration_sec = int(body.get("duration_sec", 0))
        except Exception:
            duration_sec = 0
        if duration_sec < 0:
            duration_sec = 0
        if duration_sec > 86400:
            duration_sec = 86400
        try:
            ensure_beacon_on()
            node = mscl.WirelessNode(node_id, BASE_STATION)
            node.readWriteRetries(10)
            mode = _start_sampling_best_effort(node, node_id)
            token = time.time()
            SAMPLE_STOP_TOKENS[node_id] = token
            if duration_sec > 0:
                t = threading.Thread(target=_schedule_idle_after, args=(node_id, duration_sec, token), daemon=True)
                t.start()
            return jsonify(success=True, message=f"Sampling started ({mode})", duration_sec=duration_sec)
        except Exception as e:
            log(f"[mscl-web] [SAMPLE] failed node_id={node_id}: {e}")
            return jsonify(success=False, error=str(e))

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
            log(f"[mscl-web] Write attempt {attempt}/5 node_id={data.get('node_id')}")
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

                sample_rate = _to_opt_int(data.get('sample_rate'))
                tx_power = _to_opt_int(data.get('tx_power'))
                channels = data.get('channels')
                input_range = _to_opt_int(data.get('input_range'))
                low_pass_filter = _to_opt_int(data.get('low_pass_filter'))
                storage_limit_mode = _to_opt_int(data.get('storage_limit_mode'))
                lost_beacon_timeout = _to_opt_int(data.get('lost_beacon_timeout'))
                diagnostic_interval = _to_opt_int(data.get('diagnostic_interval'))
                if sample_rate is None or tx_power is None:
                    cached = NODE_READ_CACHE.get(int(data['node_id']), {})
                    if sample_rate is None:
                        sample_rate = cached.get('current_rate')
                    if tx_power is None:
                        tx_power = cached.get('current_power')
                    if input_range is None:
                        input_range = cached.get('current_input_range')
                    if low_pass_filter is None:
                        low_pass_filter = cached.get('current_low_pass')
                    if storage_limit_mode is None:
                        storage_limit_mode = cached.get('current_storage_limit_mode')
                    if lost_beacon_timeout is None:
                        lost_beacon_timeout = cached.get('current_lost_beacon_timeout')
                    if diagnostic_interval is None:
                        diagnostic_interval = cached.get('current_diagnostic_interval')
                if sample_rate is None:
                    return jsonify(success=False, error="Sample Rate is unknown. Run FULL READ once or set node in SensorConnect.")
                if tx_power is None:
                    tx_power = 16
                if int(tx_power) > 16:
                    log(f"[mscl-web] Write warn node_id={data.get('node_id')}: tx_power={tx_power} exceeds node limit, clamped to 16 dBm")
                    tx_power = 16
                if not isinstance(channels, list):
                    channels = [1]
                channels = [int(ch) for ch in channels if _to_opt_int(ch) in (1, 2)]
                if len(channels) == 0:
                    channels = [1]
                config = mscl.WirelessNodeConfig()
                config.samplingMode(mscl.WirelessTypes.samplingMode_sync)
                config.sampleRate(int(sample_rate))
                p_map = {16: 1, 10: 2, 5: 3, 0: 4}
                tx_enum = p_map.get(int(tx_power), 1)
                config.transmitPower(tx_enum)
                full_mask = mscl.ChannelMask()
                for ch_id in channels:
                    full_mask.enable(ch_id)
                config.activeChannels(full_mask)
                if input_range is not None:
                    ir_set = False
                    ir_errs = []
                    for setter in (
                        lambda: config.inputRange(ch1_mask(), int(input_range)),
                        lambda: config.inputRange(int(input_range)),
                    ):
                        try:
                            setter()
                            ir_set = True
                            break
                        except Exception as e:
                            ir_errs.append(str(e))
                    if not ir_set:
                        raise RuntimeError("Input Range not set: " + " | ".join(ir_errs))
                if low_pass_filter is not None:
                    lp_set = False
                    lp_errs = []
                    for setter in (
                        lambda: config.lowPassFilter(ch1_mask(), int(low_pass_filter)),
                        lambda: config.lowPassFilter(full_mask, int(low_pass_filter)),
                        lambda: config.lowPassFilter(int(low_pass_filter)),
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
                    config.storageLimitMode(int(storage_limit_mode))
                if lost_beacon_timeout is not None:
                    config.lostBeaconTimeout(int(lost_beacon_timeout))
                if diagnostic_interval is not None:
                    config.diagnosticInterval(int(diagnostic_interval))
                node.applyConfig(config)
                # Keep UI stable after write even if subsequent EEPROM reads fail.
                cached = NODE_READ_CACHE.get(int(data['node_id']), {})
                cached['current_rate'] = int(sample_rate)
                cached['current_power'] = int(tx_power)
                cached['current_power_enum'] = int(tx_enum)
                if input_range is not None:
                    cached['current_input_range'] = int(input_range)
                if low_pass_filter is not None:
                    cached['current_low_pass'] = int(low_pass_filter)
                if storage_limit_mode is not None:
                    cached['current_storage_limit_mode'] = int(storage_limit_mode)
                if lost_beacon_timeout is not None:
                    cached['current_lost_beacon_timeout'] = int(lost_beacon_timeout)
                if diagnostic_interval is not None:
                    cached['current_diagnostic_interval'] = int(diagnostic_interval)
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
