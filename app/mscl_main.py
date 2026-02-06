import os
import sys
import time
import glob
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS

# --- ИМПОРТ РЕЖИМА КОНФИГУРАЦИИ ---
# Переименовано в mscl_web_config.py
try:
    import mscl_web_config
except ImportError:
    pass 

# Настройки MSCL
mscl_install_path = '/usr/lib/python3.12/dist-packages'
if mscl_install_path not in sys.path:
    sys.path.append(mscl_install_path)

try:
    import MSCL as mscl
    print(f">>> MSCL Loaded Successfully. Version: {mscl.MSCL_VERSION}", flush=True)
except ImportError as e:
    print(f"!!! Critical: MSCL not found. Error: {e}", flush=True)
    sys.exit(1)

# InfluxDB
URL = os.getenv("INFLUX_URL", "http://influxdb:8086")
TOKEN = os.getenv("INFLUX_TOKEN")
ORG = os.getenv("INFLUX_ORG")
BUCKET = os.getenv("INFLUX_BUCKET")

# Настройки сбора
def parse_target_nodes():
    raw = os.getenv("MSCL_NODES", "").strip()
    if not raw:
        return []
    nodes = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            nodes.append(int(part))
        except ValueError:
            continue
    return nodes

TARGET_NODES = parse_target_nodes() or [16904]
MAX_ADC = 16777215.0 

# --- ФУНКЦИИ СБОРА (COLLECTOR) ---
def normalize_value(raw_val):
    try:
        if raw_val > MAX_ADC: return 0.0
        percent = ((MAX_ADC - raw_val) / MAX_ADC) * 100.0
        return max(0.0, min(100.0, percent))
    except:
        return 0.0

def find_base_station():
    ports = glob.glob('/dev/ttyACM*') + glob.glob('/dev/ttyUSB*')
    for port in ports:
        try:
            connection = mscl.Connection.Serial(port)
            base_station = mscl.BaseStation(connection)
            base_station.readWriteRetries(3)
            if base_station.ping():
                print(f">>> Found BaseStation on {port}", flush=True)
                return base_station
        except:
            pass
    return None

def kickstart_network(base_station):
    print(f">>> [KICKSTART] Configuring network for Nodes {TARGET_NODES}...", flush=True)
    try:
        print("    -> Enabling Beacon...", flush=True)
        base_station.enableBeacon()
        any_ok = False
        for node_id in TARGET_NODES:
            node = mscl.WirelessNode(node_id, base_station)
            node.readWriteRetries(15)
            started = False
            err_sync = None
            err_non = None
            err_resend_sync = None

            if hasattr(node, "startSyncSampling"):
                try:
                    print(f"    -> Sending StartSyncSampling to {node_id}...", flush=True)
                    node.startSyncSampling()
                    started = True
                except Exception as e:
                    err_sync = e

            if not started and hasattr(node, "startNonSyncSampling"):
                try:
                    print(f"    -> Sending StartNonSyncSampling to {node_id}...", flush=True)
                    node.startNonSyncSampling()
                    started = True
                except Exception as e:
                    err_non = e

            if not started and hasattr(node, "resendStartSyncSampling"):
                try:
                    print(f"    -> Resending StartSyncSampling to {node_id}...", flush=True)
                    node.resendStartSyncSampling()
                    started = True
                except Exception as e:
                    err_resend_sync = e

            if started:
                print(f"    -> SUCCESS: Node {node_id} is now sampling.", flush=True)
                any_ok = True
            else:
                print(f"    -> [!] Kickstart failed for {node_id}: sync_err={err_sync}; non_sync_err={err_non}; resend_sync_err={err_resend_sync}", flush=True)
        return any_ok
    except Exception as e:
        print(f"    -> [!] Kickstart failed: {e}", flush=True)
        return False

def run_collector_mode():
    print(">>> STARTING COLLECTOR MODE (InfluxDB) <<<", flush=True)
    db_client = InfluxDBClient(url=URL, token=TOKEN, org=ORG)
    write_api = db_client.write_api(write_options=SYNCHRONOUS)

    base_station = None
    last_data_time = time.time()
    
    while True:
        if base_station is None:
            base_station = find_base_station()
            if base_station is None:
                print(">>> Waiting for BaseStation USB...", flush=True)
                time.sleep(5)
                continue
            
            kickstart_network(base_station)
            last_data_time = time.time()

        try:
            packets = base_station.getData(500)
            current_time = time.time()
            
            if (current_time - last_data_time) > 20:
                print(">>> [WATCHDOG] No data for 20s, re-kickstarting...", flush=True)
                kickstart_network(base_station)
                last_data_time = current_time

            for packet in packets:
                last_data_time = current_time
                node_id = packet.nodeAddress()
                pkt_timestamp = packet.timestamp().nanoseconds()
                
                points_buffer = []
                
                for data_point in packet.data():
                    channel_name = data_point.channelName().lower()
                    raw_val = data_point.as_float()
                    
                    p = Point("mscl_sensors") \
                        .tag("node_id", str(node_id)) \
                        .tag("channel", channel_name) \
                        .field("raw_adc", float(raw_val)) \
                        .time(pkt_timestamp)
                    points_buffer.append(p)

                    if channel_name == "ch1":
                        load_pct = normalize_value(raw_val)
                        p_pct = Point("mscl_sensors") \
                            .tag("node_id", str(node_id)) \
                            .tag("channel", "load_percent") \
                            .field("value", load_pct) \
                            .time(pkt_timestamp)
                        points_buffer.append(p_pct)
                        
                        if int(current_time) % 2 == 0:
                            print(f">>> Node {node_id}: Raw={raw_val:.0f} | Load={load_pct:.1f}%", flush=True)

                if points_buffer:
                    write_api.write(BUCKET, ORG, points_buffer)

        except Exception as e:
            print(f">>> [!] Runtime Error: {e}", flush=True)
            base_station = None 
            time.sleep(2)

# --- ГЛАВНЫЙ ВХОД ---
if __name__ == "__main__":
    mode = os.getenv("MSCL_MODE", "COLLECTOR").upper()
    
    if mode == "CONFIG":
        # Используем новое имя модуля
        if 'mscl_web_config' in sys.modules:
            mscl_web_config.run_config_server()
        else:
            print("!!! Error: mscl_web_config.py not found.")
    else:
        run_collector_mode()
