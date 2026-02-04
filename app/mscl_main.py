import os
import sys
import time
import glob
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS

# 1. Путь к библиотеке (уже проверено, что работает)
mscl_install_path = '/usr/lib/python3.12/dist-packages'
if mscl_install_path not in sys.path:
    sys.path.append(mscl_install_path)

try:
    import MSCL as mscl
    print(f">>> MSCL Loaded Successfully. Version: {mscl.MSCL_VERSION}")
except ImportError as e:
    print(f"!!! Critical: MSCL not found. Error: {e}")
    sys.exit(1)

# InfluxDB Config
URL = os.getenv("INFLUX_URL", "http://influxdb:8086")
TOKEN = os.getenv("INFLUX_TOKEN")
ORG = os.getenv("INFLUX_ORG")
BUCKET = os.getenv("INFLUX_BUCKET")

def find_base_station():
    """Поиск BaseStation на USB портах."""
    ports = glob.glob('/dev/ttyACM*') + glob.glob('/dev/ttyUSB*')
    for port in ports:
        try:
            connection = mscl.Connection.Serial(port)
            base_station = mscl.BaseStation(connection)
            print(f">>> Found BaseStation on {port}")
            return base_station
        except:
            continue
    return None

def main():
    print(">>> Starting MSCL Collector Loop...")
    
    # Инициализация InfluxDB
    db_client = InfluxDBClient(url=URL, token=TOKEN, org=ORG)
    write_api = db_client.write_api(write_options=SYNCHRONOUS)

    base_station = None
    
    # БЕСКОНЕЧНЫЙ ЦИКЛ поиска и работы
    while True:
        if base_station is None:
            base_station = find_base_station()
            if base_station is None:
                print(">>> Waiting for BaseStation USB device...")
                time.sleep(10) # Ждем 10 секунд перед следующим поиском
                continue

        try:
            # Пытаемся получить данные
            packets = base_station.getData(1000) # Таймаут 1 сек
            for packet in packets:
                print(f">>> Received packet from Node: {packet.nodeAddress()}") # ДОБАВИТЬ ЭТО
                points = [] 
                pass
            
            # Если данных нет, просто спим немного, чтобы не грузить CPU
            if not packets:
                time.sleep(0.1)

        except Exception as e:
            print(f">>> Connection issue or error: {e}")
            base_station = None # Сбрасываем станцию, чтобы начать поиск заново
            time.sleep(5)

if __name__ == "__main__":
    main()