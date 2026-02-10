import glob
import fcntl
import os
import sys
import time
from influxdb_client import InfluxDBClient, Point  # type: ignore
from influxdb_client.client.write_api import SYNCHRONOUS  # type: ignore

# MSCL Python package path used in this project image.
for mscl_path in ("/usr/lib/python3.12/dist-packages", "/usr/share/python3-mscl"):
    if mscl_path not in sys.path:
        sys.path.append(mscl_path)

try:
    import MSCL as mscl  # type: ignore
except ImportError:
    print("!!! Critical: MSCL not found. Check Docker image installation.")
    sys.exit(1)


URL = os.getenv("INFLUX_URL", "http://influxdb:8086")
TOKEN = os.getenv("INFLUX_TOKEN")
ORG = os.getenv("INFLUX_ORG")
BUCKET = os.getenv("INFLUX_BUCKET")
MEASUREMENT = os.getenv("MSCL_MEASUREMENT", "mscl_sensors")
BAUDRATE = int(os.getenv("MSCL_BAUDRATE", "3000000"))
ONLY_CHANNEL_1 = os.getenv("MSCL_ONLY_CHANNEL_1", "false").lower() == "true"
LOCK_FILE = os.getenv("MSCL_LOCK_FILE", "/var/lock/mscl/base.lock")


class BaseAccessLock:
    def __init__(self, lock_file):
        self.lock_file = lock_file
        self.fh = None

    def __enter__(self):
        lock_dir = os.path.dirname(self.lock_file)
        if lock_dir:
            os.makedirs(lock_dir, exist_ok=True)
        self.fh = open(self.lock_file, "a+")
        fcntl.flock(self.fh.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.fh is not None:
            fcntl.flock(self.fh.fileno(), fcntl.LOCK_UN)
            self.fh.close()
            self.fh = None


def find_base_station():
    """Scan common serial ports and return first reachable base station."""
    ports = glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*")
    for port in ports:
        try:
            with BaseAccessLock(LOCK_FILE):
                connection = mscl.Connection.Serial(port, BAUDRATE)
                base_station = mscl.BaseStation(connection)
                base_station.readWriteRetries(10)
                if base_station.ping():
                    print(f">>> Found BaseStation on {port}")
                    return base_station, port
        except Exception:
            continue
    return None, None


def _close_base_station(base_station):
    if base_station is None:
        return
    try:
        base_station.disconnect()
    except Exception:
        pass
    try:
        base_station.release()
    except Exception:
        pass


def _point_channel(dp):
    try:
        name = dp.channelName()
        if name:
            return str(name)
    except Exception:
        pass
    try:
        return f"ch{int(dp.channelId())}"
    except Exception:
        return "channel"


def _point_value(dp):
    for getter in (
        lambda: dp.as_float(),
        lambda: dp.as_double(),
        lambda: dp.as_int32(),
        lambda: dp.as_uint32(),
        lambda: dp.as_int16(),
        lambda: dp.as_uint16(),
        lambda: dp.as_int8(),
        lambda: dp.as_uint8(),
        lambda: dp.value(),
    ):
        try:
            return float(getter())
        except Exception:
            continue
    return None


def main():
    if not all([TOKEN, ORG, BUCKET]):
        print("!!! Missing INFLUX_TOKEN / INFLUX_ORG / INFLUX_BUCKET")
        sys.exit(1)

    print(f">>> Starting MSCL read-only collector (MSCL {mscl.MSCL_VERSION})")
    print(f">>> Influx target: {URL} bucket={BUCKET} org={ORG} measurement={MEASUREMENT}")
    print(f">>> Filter only channel_1/ch1: {ONLY_CHANNEL_1}")
    print(f">>> Base access lock: {LOCK_FILE}")

    db_client = InfluxDBClient(url=URL, token=TOKEN, org=ORG)
    write_api = db_client.write_api(write_options=SYNCHRONOUS)

    base_station, port = find_base_station()
    if not base_station:
        print("!!! No BaseStation found. Ensure it is connected.")
        sys.exit(1)

    print(">>> Listening for node packets...")
    reconnect_backoff = 1.0
    reconnect_backoff_max = 10.0
    while True:
        try:
            with BaseAccessLock(LOCK_FILE):
                packets = base_station.getData(500)
            if not packets:
                time.sleep(0.1)
                continue

            points = []
            channel_counts = {}
            for packet in packets:
                node_address = str(packet.nodeAddress())
                for dp in packet.data():
                    channel = _point_channel(dp)
                    if ONLY_CHANNEL_1 and channel not in ("channel_1", "ch1"):
                        continue
                    value = _point_value(dp)
                    if value is None:
                        continue
                    point = (
                        Point(MEASUREMENT)
                        .tag("node_id", node_address)
                        .tag("channel", channel)
                        .tag("source", "mscl_main_ro")
                        .field("value", value)
                    )
                    points.append(point)
                    channel_counts[channel] = channel_counts.get(channel, 0) + 1

            if points:
                write_api.write(BUCKET, ORG, points)
                channels_txt = ", ".join(f"{k}:{v}" for k, v in sorted(channel_counts.items()))
                print(f">>> Logged {len(points)} points ({channels_txt})")

        except Exception as e:
            print(f"!!! Runtime error: {e}")
            _close_base_station(base_station)
            base_station = None
            while base_station is None:
                time.sleep(reconnect_backoff)
                base_station, port = find_base_station()
                if base_station:
                    print(f">>> Reconnected BaseStation on {port}")
                    reconnect_backoff = 1.0
                    break
                reconnect_backoff = min(reconnect_backoff_max, reconnect_backoff * 1.7)


if __name__ == "__main__":
    main()
