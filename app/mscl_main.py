import os
import sys
import time
import glob
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS

mscl_install_path = '/usr/lib/python3.12/dist-packages'
if mscl_install_path not in sys.path:
    sys.path.append(mscl_install_path)

try:
    import MSCL as mscl
    print(f">>> MSCL Loaded Successfully. Version: {mscl.MSCL_VERSION}", flush=True)
except ImportError as e:
    print(f"!!! Critical: MSCL not found. Error: {e}", flush=True)
    sys.exit(1)

# InfluxDB Config
URL = os.getenv("INFLUX_URL", "http://influxdb:8086")
TOKEN = os.getenv("INFLUX_TOKEN")
ORG = os.getenv("INFLUX_ORG")
BUCKET = os.getenv("INFLUX_BUCKET")

TARGET_NODE = 16907
MAX_ADC = 16777215.0 

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
    """
    Агрессивный пинок сети.
    """
    print(f">>> [KICKSTART] Silence detected. Trying to wake Node {TARGET_NODE}...", flush=True)
    
    # 1. Пытаемся достучаться до ноды (3 попытки)
    node = None
    for i in range(3):
        try:
            # Попытка создать объект (читает EEPROM)
            node = mscl.WirelessNode(TARGET_NODE, base_station)
            print(f"    -> Success: Node {TARGET_NODE} contacted on attempt {i+1}.", flush=True)
            break
        except Exception as e:
            print(f"    -> Attempt {i+1} failed: Node sleeping? ({e})", flush=True)
            time.sleep(0.5)

    # 2. Создаем сеть
    try:
        network = mscl.SyncSamplingNetwork(base_station)
        
        if node:
            # Если удалось создать объект - добавляем официально
            network.addNode(node)
            print("    -> Node added to Network queue.", flush=True)
            try:
                node.setToIdle() # ПРИНУДИТЕЛЬНО БУДИМ
                print("    -> WakeUp command sent (setToIdle).", flush=True)
            except:
                print("    -> Failed to send WakeUp command.", flush=True)
        else:
            print("    -> WARNING: Adding node blindly (could not contact).", flush=True)
            # В MSCL нельзя добавить ноду без объекта. 
            # Если мы тут, значит нода спит слишком крепко.
            # Единственная надежда - Маяк.

        # 3. Запуск
        network.applyConfiguration()
        network.startSampling()
        print(">>> [KICKSTART] Network Started (Beacon Active). Waiting for node...", flush=True)

    except Exception as e:
        print(f"!!! Kickstart critical error: {e}", flush=True)

def main():
    print(">>> Starting MSCL Collector (Verbose Kicker)...", flush=True)
    
    db_client = InfluxDBClient(url=URL, token=TOKEN, org=ORG)
    write_api = db_client.write_api(write_options=SYNCHRONOUS)

    base_station = None
    last_data_time = time.time()
    
    while True:
        if base_station is None:
            base_station = find_base_station()
            if base_station is None:
                print(">>> Waiting for BaseStation...", flush=True)
                time.sleep(5)
                continue
            
            # Первый пинок при старте
            kickstart_network(base_station)
            last_data_time = time.time()

        try:
            packets = base_station.getData(100) # 100мс
            current_time = time.time()
            
            # --- ЛОГ СЕРДЦЕБИЕНИЯ (Чтобы не казалось, что завис) ---
            if not packets and (int(current_time) % 5 == 0):
                 # Выводим точку или сообщение, если давно не писали
                 print(f">>> Status: Listening... (No data for {int(current_time - last_data_time)}s)", flush=True)
                 time.sleep(0.2) # Чтобы не спамить в одну и ту же секунду

            # --- ПИНОК (Если тишина > 20 сек) ---
            if (current_time - last_data_time) > 20:
                kickstart_network(base_station)
                last_data_time = current_time # Сброс
            
            for packet in packets:
                last_data_time = current_time
                
                points_buffer = []
                node_id = packet.nodeAddress()
                pkt_timestamp = packet.timestamp().nanoseconds()
                
                log_pct = 0.0
                has_ch1 = False
                
                for data_point in packet.data():
                    try:
                        channel_name = data_point.channelName()
                        if data_point.stored_as() == mscl.WirelessTypes.stored_as_float:
                             raw_val = data_point.as_float()
                        else:
                             try: raw_val = data_point.as_float()
                             except: continue
                        
                        p = Point("mscl_sensors") \
                            .tag("node_id", str(node_id)) \
                            .tag("channel", channel_name) \
                            .field("value", float(raw_val)) \
                            .time(pkt_timestamp)
                        points_buffer.append(p)

                        if channel_name == "ch1":
                            log_pct = normalize_value(raw_val)
                            has_ch1 = True
                            p_pct = Point("mscl_sensors") \
                                .tag("node_id", str(node_id)) \
                                .tag("channel", "force_percent") \
                                .field("value", log_pct) \
                                .time(pkt_timestamp)
                            points_buffer.append(p_pct)
                    except: continue

                if points_buffer:
                    write_api.write(BUCKET, ORG, points_buffer)
                    if has_ch1:
                        print(f">>> Node {node_id}: Load={log_pct:.1f}%", flush=True)
                    else:
                        print(f">>> Node {node_id}: Data received", flush=True)

            if not packets:
                time.sleep(0.01)

        except Exception as e:
            print(f">>> Connection Error: {e}", flush=True)
            base_station = None
            time.sleep(2)

if __name__ == "__main__":
    main()