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
    global BASE_STATION, LAST_PING_OK_TS, NEXT_PING_ALLOWED_TS, CURRENT_PORT, LAST_CONNECT_ATTEMPT_TS
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
    global BASE_STATION, LAST_PING_OK_TS
    with OP_LOCK:
        BASE_STATION = None
        LAST_PING_OK_TS = 0
        ok, msg = internal_connect(force_ping=True)
        return jsonify(success=bool(ok), message=msg)

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
    global BASE_STATION, RATE_MAP
    last_err = None
    log(f"[mscl-web] Read request node_id={node_id}")
    with OP_LOCK:
        for attempt in range(1, 6):
            log(f"[mscl-web] Read attempt {attempt}/5 node_id={node_id}")
            ok, msg = internal_connect()
            if not ok or BASE_STATION is None:
                last_err = f"Base station not connected: {msg}"
                log(f"[mscl-web] Read failed: {last_err}")
                time.sleep(0.5)
                continue
            try:
                node = mscl.WirelessNode(node_id, BASE_STATION)
                node.readWriteRetries(15)
        
                # 1. Принудительная остановка (Auto-Idle before Read)
                idle_ok = False
                for idle_attempt in range(1, 3):
                    try:
                        log(f"[mscl-web] Read setToIdle attempt {idle_attempt}/2 node_id={node_id}")
                        node.setToIdle()
                        time.sleep(2.0)
                        idle_ok = True
                        break
                    except Exception as e:
                        log(f"[mscl-web] Read setToIdle failed node_id={node_id}: {e}")
                        time.sleep(1.0)
                if not idle_ok:
                    last_err = "Failed to set node to Idle before Read"
                    continue

                # 2. ЧИТАЕМ ТОЛЬКО КРИТИЧЕСКИЕ ДАННЫЕ
                current_rate = None
                try:
                    current_rate = int(node.getSampleRate())
                except Exception as e:
                    last_err = str(e)
                    log(f"[mscl-web] Read warn node_id={node_id}: getSampleRate failed (1st): {last_err}")
                    time.sleep(1.0)
                    try:
                        current_rate = int(node.getSampleRate())
                    except Exception as e2:
                        last_err = str(e2)
                        log(f"[mscl-web] Read error node_id={node_id}: getSampleRate failed (2nd): {last_err}")
                        if "EEPROM" in last_err:
                            # Continue with partial data
                            current_rate = None
                        else:
                            BASE_STATION = None
                            LAST_PING_OK_TS = 0
                            time.sleep(0.5)
                            continue
                try:
                    active_mask = node.getActiveChannels()
                except Exception:
                    active_mask = None
        
                # 3. Остальные поля — best-effort (не ломаем чтение при EEPROM ошибках)
                try:
                    model = node.model()
                except Exception as e:
                    model = "TC-Link-200"
                    log(f"[mscl-web] Read warn node_id={node_id}: model read failed: {e}")
        
                try:
                    sn = str(node.nodeAddress())
                except Exception as e:
                    sn = "N/A"
                    log(f"[mscl-web] Read warn node_id={node_id}: serial read failed: {e}")
        
                try:
                    fw = str(node.firmwareVersion())
                except Exception as e:
                    fw = "N/A"
                    log(f"[mscl-web] Read warn node_id={node_id}: firmware read failed: {e}")
        
                try: 
                    p_raw = int(node.getTransmitPower())
                    p_map = {0: 20, 1: 16, 2: 10, 3: 5, 4: 0}
                    current_power = p_map.get(p_raw, 20)
                except Exception as e: 
                    current_power = 20 # Дефолт при ошибке 94
                    log(f"[mscl-web] Read warn node_id={node_id}: transmit power read failed: {e}")
            
                # Optional status fields (best-effort)
                try:
                    region = str(node.regionCode())
                except Exception:
                    region = None
                try:
                    last_comm = str(node.lastCommunicationTime()).split(".")[0]
                except Exception:
                    last_comm = None
                try:
                    state = str(node.lastDeviceState())
                except Exception:
                    state = None
                try:
                    storage_pct = round(float(node.percentFull()), 2)
                except Exception:
                    storage_pct = None
                try:
                    sampling_mode_val = node.getSamplingMode()
                    sampling_mode = "sync" if sampling_mode_val == mscl.WirelessTypes.samplingMode_sync else "non_sync"
                except Exception:
                    sampling_mode = None
                try:
                    data_mode = str(node.getDataMode())
                except Exception:
                    data_mode = None

                # Частоты (если доступно)
                supported_rates = []
                if current_rate is not None:
                    supported_rates.append({"enum_val": current_rate, "str_val": RATE_MAP.get(current_rate, str(current_rate) + " Hz")})
                try:
                    features = node.features()
                    rates = features.sampleRates(mscl.WirelessTypes.samplingMode_sync, 1, 0)
                    supported_rates = []
                    for r in rates:
                        rid = int(r)
                        supported_rates.append({"enum_val": rid, "str_val": RATE_MAP.get(rid, str(rid) + " Hz")})
                except Exception as e:
                    log(f"[mscl-web] Read warn node_id={node_id}: features/sampleRates failed: {e}")
            
                channels = []
                if active_mask is not None:
                    for i in range(1, 3):
                        channels.append({"id": i, "enabled": active_mask.enabled(i)})
        
                return jsonify(
                    success=True, model=model, sn=sn, fw=fw,
                    region=region, last_comm=last_comm, state=state,
                    storage_pct=storage_pct, sampling_mode=sampling_mode, data_mode=data_mode,
                    current_rate=current_rate, current_power=current_power,
                    supported_rates=supported_rates, channels=channels
                )
            except Exception as e:
                last_err = str(e)
                log(f"[mscl-web] Read error node_id={node_id}: {e}")
                if "EEPROM" in last_err:
                    backoff = min(4.0, 0.5 * (2 ** (attempt - 1)))
                    time.sleep(backoff)
                    continue
                BASE_STATION = None
                LAST_PING_OK_TS = 0
                time.sleep(0.5)
                continue
    if last_err:
        log(f"[mscl-web] Read failed node_id={node_id}: {last_err}")
    else:
        log(f"[mscl-web] Read failed node_id={node_id}: Read failed")
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

@app.route('/api/write', methods=['POST'])
def api_write():
    global BASE_STATION
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
                node = mscl.WirelessNode(int(data['node_id']), BASE_STATION)
                node.readWriteRetries(15)
                # Auto-Idle before Write
                idle_ok = False
                for idle_attempt in range(1, 3):
                    try:
                        log(f"[mscl-web] Write setToIdle attempt {idle_attempt}/2 node_id={data.get('node_id')}")
                        node.setToIdle()
                        time.sleep(2.5)
                        idle_ok = True
                        break
                    except Exception as e:
                        log(f"[mscl-web] Write setToIdle failed node_id={data.get('node_id')}: {e}")
                        time.sleep(1.0)
                if not idle_ok:
                    last_err = "Failed to set node to Idle before Write"
                    continue
                config = mscl.WirelessNodeConfig()
                config.samplingMode(mscl.WirelessTypes.samplingMode_sync)
                config.sampleRate(int(data['sample_rate']))
                p_map = {20: 0, 16: 1, 10: 2, 5: 3, 0: 4}
                config.transmitPower(p_map.get(data['tx_power'], 0))
                full_mask = mscl.ChannelMask()
                for ch_id in data['channels']:
                    full_mask.enable(ch_id)
                config.activeChannels(full_mask)
                node.applyConfig(config)
                # Avoid pinging immediately after write
                global NEXT_PING_ALLOWED_TS
                NEXT_PING_ALLOWED_TS = time.time() + PING_COOLDOWN_SEC
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
