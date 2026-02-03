import os
import time
import sys
from uldaq import (get_daq_device_inventory, DaqDevice, InterfaceType,  # type: ignore
                   TcType, ULException, TempScale)
from influxdb_client import InfluxDBClient, Point # type: ignore
from influxdb_client.client.write_api import SYNCHRONOUS # type: ignore

# Configuration from environment variables
URL = os.getenv("INFLUX_URL", "http://influxdb:8086")
TOKEN = os.getenv("INFLUX_TOKEN")
ORG = os.getenv("INFLUX_ORG")
BUCKET = os.getenv("INFLUX_BUCKET")
TEMP_MIN = float(os.getenv("TEMP_MIN", "-70"))
TEMP_MAX = float(os.getenv("TEMP_MAX", "600"))

def get_device():
    """Locate and connect to the RedLab-TC device. Exit if not found."""
    try:
        devices = get_daq_device_inventory(InterfaceType.USB)
        if devices:
            print(f"--- Device found. Attempting to connect to {devices[0].product_name}...")
            dev = DaqDevice(devices[0])
            dev.connect()
            print(f">>> Successfully connected: {devices[0].product_name}")
            return dev
        else:
            print("--- Device not found in USB. Exiting for container restart...")
            sys.exit(1) # Exit to let Docker restart the container and refresh USB stack
    except Exception as e:
        print(f"!!! Connection error: {e}")
        sys.exit(1)

def main():
    # Initialize InfluxDB Client
    client = InfluxDBClient(url=URL, token=TOKEN, org=ORG)
    write_api = client.write_api(write_options=SYNCHRONOUS)
    
    daq_device = None
    try:
        # 1. Connect to device
        daq_device = get_device()
        ai_device = daq_device.get_ai_device()
        ai_config = ai_device.get_config()
        
        # 2. Configure all 8 channels as K-type thermocouples
        for ch in range(8):
            ai_config.set_chan_tc_type(ch, TcType.K)
        
        print(">>> Data acquisition loop started.")
        
        # 3. Main polling loop
        while True:
            points = []
            log_data = []

            for ch in range(8):
                try:
                    temp = ai_device.t_in(ch, TempScale.CELSIUS)
                    if TEMP_MIN < temp < TEMP_MAX:
                        points.append(
                            Point("temperature")
                            .tag("channel", f"ch{ch}")
                            .field("value", float(temp))
                        )
                        log_data.append(f"CH{ch}:{temp:.1f}")
                except ULException as e:
                    if "OPEN_CONNECTION" in str(e):
                        continue
                    # Any other hardware error (like disconnect) triggers restart
                    print(f"\n!!! Hardware error during reading: {e}")
                    sys.exit(1) 

            if points:
                try:
                    write_api.write(BUCKET, ORG, points)
                    if log_data:
                        print(f"Data logged: " + " | ".join(log_data))
                except Exception as e:
                    print(f"!!! InfluxDB Write Error: {e}")
            
            time.sleep(1)

    except Exception as e:
        print(f"\n!!! Critical error: {e}")
        sys.exit(1)
    finally:
        if daq_device:
            try:
                daq_device.disconnect()
                daq_device.release()
            except:
                pass

if __name__ == "__main__":
    main()