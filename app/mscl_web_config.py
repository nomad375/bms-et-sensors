from flask import Flask, render_template_string, request, jsonify # type: ignore
import logging
from pathlib import Path
import sys
import glob
import time
import threading

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
PING_COOLDOWN_SEC = 5
NEXT_PING_ALLOWED_TS = 0
CURRENT_PORT = None
LAST_CONNECT_ATTEMPT_TS = 0
CONNECT_MIN_INTERVAL_SEC = 2.0
NODE_READ_CACHE = {}
NODE_EEPROM_REFRESH_SEC = 45
BASE_BEACON_STATE = None

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

HTML_TEMPLATE = None

def find_port():
    ports = glob.glob('/dev/ttyACM*') + glob.glob('/dev/ttyUSB*')
    return ports[0] if ports else None

def internal_connect(force_ping=False):
    global BASE_STATION, LAST_PING_OK_TS, NEXT_PING_ALLOWED_TS, CURRENT_PORT, LAST_CONNECT_ATTEMPT_TS, BASE_BEACON_STATE
    now = time.time()
    with CONNECT_LOCK:
        if BASE_STATION and not force_ping:
            return True, "Connected"
        if BASE_STATION and not force_ping and now < NEXT_PING_ALLOWED_TS:
            return True, "Connected"
        if BASE_STATION and force_ping:
            try:
                if BASE_STATION.ping():
                    LAST_PING_OK_TS = now
                    LAST_BASE_STATUS.update({"connected": True, "message": "Connected", "ts": time.strftime("%H:%M:%S")})
                    return True, "Connected"
            except Exception:
                BASE_STATION = None
        if (now - LAST_CONNECT_ATTEMPT_TS) < CONNECT_MIN_INTERVAL_SEC:
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
        def _trim_ts(value):
            try:
                s = str(value)
                if "." in s:
                    return s.split(".")[0]
                return s
            except Exception:
                return value
        base_model = None
        base_fw = None
        base_serial = None
        base_region = None
        base_radio = None
        base_last_comm = None
        base_link = None
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
        return jsonify(
            connected=bool(ok),
            port=port,
            message=msg,
            beacon_state=BASE_BEACON_STATE,
            ts=LAST_BASE_STATUS.get("ts"),
            base_model=base_model,
            base_fw=base_fw,
            base_serial=base_serial,
            base_region=base_region,
            base_radio=base_radio,
            base_last_comm=base_last_comm,
            base_link=base_link
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
                ("supportsSamplingMode", "supportsSamplingMode"),
                ("supportsLostBeaconTimeout", "supportsLostBeaconTimeout"),
                ("supportsInactivityTimeout", "supportsInactivityTimeout"),
                ("supportsDiagnosticInfo", "supportsDiagnosticInfo"),
                ("supportsTransmitPower", "supportsTransmitPower"),
                ("supportsSampleRate", "supportsSampleRate"),
                ("supportsChannel", "supportsChannel"),
                ("supportsChannelSetting", "supportsChannelSetting"),
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
    read_mode = (request.args.get("mode") or "fast").strip().lower()
    if read_mode not in ("fast", "full"):
        read_mode = "fast"
    read_tag = "READ-FULL" if read_mode == "full" else "READ-FAST"
    max_attempts = 5 if read_mode == "full" else 2
    last_err = None
    log(f"[mscl-web] [{read_tag}] request node_id={node_id}")
    with OP_LOCK:
        cached = NODE_READ_CACHE.get(node_id, {})
        now_ts = time.time()
        cache_age = now_ts - float(cached.get("ts", 0))
        refresh_eeprom = False if read_mode == "fast" else True
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
        
                # Read path should be as non-intrusive as possible.
                # Do not force Idle before every read; this reduced stability on real hardware.

                # 2. Critical values (use cache-first for EEPROM-heavy fields)
                current_rate = cached.get("current_rate")
                if read_mode == "full" and (refresh_eeprom or current_rate is None):
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
                if read_mode == "full" and (refresh_eeprom or "model" not in cached):
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
                if read_mode == "full" and (refresh_eeprom or "fw" not in cached):
                    try:
                        fw = str(node.firmwareVersion())
                    except Exception as e:
                        log(f"[mscl-web] [{read_tag}] warn node_id={node_id}: firmware read failed: {e}")
        
                current_power = cached.get("current_power", 20)
                if read_mode == "full" and (refresh_eeprom or "current_power" not in cached):
                    try: 
                        p_raw = int(node.getTransmitPower())
                        p_map = {0: 20, 1: 16, 2: 10, 3: 5, 4: 0}
                        current_power = p_map.get(p_raw, 20)
                    except Exception as e:
                        log(f"[mscl-web] [{read_tag}] warn node_id={node_id}: transmit power read failed: {e}")
            
                # Optional status fields (best-effort)
                try:
                    region = str(node.regionCode())
                except Exception:
                    region = None
                try:
                    last_comm = str(node.lastCommunicationTime()).split(".")[0]
                except Exception:
                    last_comm = None
                state = None
                state_text = None
                try:
                    raw_state = node.lastDeviceState()
                    try:
                        state = int(raw_state)
                    except Exception:
                        state = str(raw_state)
                    state_map = {
                        0: "Idle",
                        1: "Sampling",
                        2: "Sampling",
                        255: "Unknown",
                    }
                    if isinstance(state, int):
                        state_text = state_map.get(state, f"State {state}")
                    else:
                        state_text = str(state)
                except Exception:
                    state = None
                    state_text = None
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
                if read_mode == "full" and (refresh_eeprom or "sampling_mode" not in cached):
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
                if read_mode == "full" and (refresh_eeprom or "data_mode" not in cached):
                    try:
                        data_mode = str(node.getDataMode())
                    except Exception:
                        pass

                # Частоты (если доступно)
                supported_rates = cached.get("supported_rates", [])
                if read_mode == "full" and (refresh_eeprom or not supported_rates) and current_rate is not None:
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
                    current_rate=current_rate, current_power=current_power,
                    supported_rates=supported_rates, channels=channels
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
        ok, msg = internal_connect()
        if not ok or BASE_STATION is None:
            return jsonify(success=False, error=f"Base station not connected: {msg}")
        try:
            ensure_beacon_on()
            node = mscl.WirelessNode(node_id, BASE_STATION)
            node.readWriteRetries(10)
            node.setToIdle()
            log(f"[mscl-web] [PREP-IDLE] success node_id={node_id}")
            return jsonify(success=True, message="Node set to Idle")
        except Exception as e:
            log(f"[mscl-web] [PREP-IDLE] failed node_id={node_id}: {e}")
            return jsonify(success=False, error=str(e))

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
                # Soft gate: only write when node is explicitly in Idle.
                try:
                    state_raw = node.lastDeviceState()
                    state_num = int(state_raw)
                    if state_num != 0:
                        err = f"Node state is {state_num}, write allowed only in Idle (0). Use 'Set to Idle' first."
                        log(f"[mscl-web] [WRITE] blocked node_id={data.get('node_id')}: {err}")
                        return jsonify(success=False, error=err)
                except Exception as e:
                    err = f"Cannot verify node state before write: {e}. Use 'Set to Idle' and retry."
                    log(f"[mscl-web] [WRITE] blocked node_id={data.get('node_id')}: {err}")
                    return jsonify(success=False, error=err)
                sample_rate = data.get('sample_rate')
                tx_power = data.get('tx_power')
                channels = data.get('channels')
                if sample_rate is None or tx_power is None:
                    cached = NODE_READ_CACHE.get(int(data['node_id']), {})
                    if sample_rate is None:
                        sample_rate = cached.get('current_rate')
                    if tx_power is None:
                        tx_power = cached.get('current_power')
                if sample_rate is None:
                    return jsonify(success=False, error="Sample Rate is unknown. Run FULL READ once or set node in SensorConnect.")
                if tx_power is None:
                    tx_power = 20
                if not isinstance(channels, list):
                    channels = [1]
                if len(channels) == 0:
                    channels = [1]
                config = mscl.WirelessNodeConfig()
                config.samplingMode(mscl.WirelessTypes.samplingMode_sync)
                config.sampleRate(int(sample_rate))
                p_map = {20: 0, 16: 1, 10: 2, 5: 3, 0: 4}
                config.transmitPower(p_map.get(int(tx_power), 0))
                full_mask = mscl.ChannelMask()
                for ch_id in channels:
                    full_mask.enable(ch_id)
                config.activeChannels(full_mask)
                node.applyConfig(config)
                # Avoid pinging immediately after write
                global NEXT_PING_ALLOWED_TS
                NEXT_PING_ALLOWED_TS = time.time() + PING_COOLDOWN_SEC
                # Keep UI stable after write even if subsequent EEPROM reads fail.
                cached = NODE_READ_CACHE.get(int(data['node_id']), {})
                cached['current_rate'] = int(sample_rate)
                cached['current_power'] = int(tx_power)
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
