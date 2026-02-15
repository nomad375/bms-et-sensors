# BMS ET Sensors Stack

Docker stack for sensor data acquisition and visualization:
- `mscl-app`: MicroStrain MSCL node configuration + stream ingestion + web API.
- `redlab-app`: MCC RedLab thermocouple collector.
- `influxdb`: time-series storage.
- `grafana`: dashboards.
- `dashboard`: lightweight start page based on `simple-dash`, served by `nginx`.
- `mosquitto`: MQTT broker.
- `mqtt-explorer`: browser UI for MQTT topics and payloads.
- `zigbee2mqtt`: Zigbee bridge for `ZBDongle-E`.
- `telegraf`: MQTT-to-InfluxDB bridge.

## Requirements

- Docker Engine with Docker Compose plugin.
- Linux host with access to sensor USB devices (`/dev` mount is used by containers).

## Quick start

1. Create local env file:

```bash
cp .env.example .env
```

2. Adjust secrets and network values in `.env`.

3. Start stack:

```bash
docker compose -f docker-compose.yml -f docker-compose.override.yml up -d
```

4. Open services:
- MSCL web UI/API: `http://<host>:5000`
- InfluxDB: `http://<host>:8086`
- Grafana: `http://<host>:3000`
- Dashboard: `http://<host>:80`
- System health page: `http://<host>/health.html`
- Zigbee2MQTT frontend: `http://<host>:8082`
- MQTT Explorer: `http://<host>:9001`

## Start commands

- Full local rebuild + restart (both app services):

```bash
./restart-local-amd64.sh
```

- Build/restart both app services:

```bash
./build-local-all.sh
```

- Build/restart only MSCL:

```bash
./build-local-mscl.sh
```

- Build/restart only RedLab:

```bash
./build-local-redlab.sh
```

- Fast MSCL restart in dev mode (bind-mounted `./app/mscl`):

```bash
./restart-mscl-dev.sh
```

- Build and push multi-arch images to Docker Hub:

```bash
./build-push-multiarch.sh
```

## Dev workflow (mscl-app)

- The MSCL container now bind-mounts code from host: `./app/mscl:/app` (see `docker-compose.override.yml`).
- `Flask reloader` is intentionally disabled, so runtime remains stable.
- If you change only MSCL code/assets (`*.py`, `*.js`, `*.html`) in `app/mscl`, rebuild is not required:

```bash
./restart-mscl-dev.sh
```

- Rebuild `mscl-app` only when dependencies/image inputs changed:
- `Dockerfile.mscl`
- `app/mscl/requirements.txt`
- system packages / base image assumptions

```bash
./build-local-mscl.sh
```

## Environment variables

Use `.env.example` as the baseline. Key variables:

### InfluxDB / Grafana
- `INFLUX_URL`
- `INFLUX_ORG`
- `INFLUX_BUCKET`
- `INFLUX_TOKEN`
- `INFLUX_ADMIN_PASSWORD`
- `GRAFANA_ADMIN_PASSWORD`
- `GRAFANA_ACCESS_ADDRESS`

### MQTT / Zigbee2MQTT / Telegraf
- `MQTT_PORT`
- `MQTT_EXPLORER_PORT`
- `Z2M_FRONTEND_PORT`
- `Z2M_BASE_TOPIC`
- `TELEGRAF_METRICS_PORT`
- `Z2M_SERIAL_PORT`
- `Z2M_SERIAL_ADAPTER`
- `TZ`

### RedLab collector
- `TEMP_MIN`
- `TEMP_MAX`

### MSCL app (optional advanced tuning)
The app also supports runtime tuning via env variables (batch sizes, queue limits, stream cadence, offsets). Defaults are defined in `app/mscl/mscl_settings.py`.
Additional stream options:
- `MSCL_RESAMPLED_ENABLED`: writes an extra evenly spaced stream for visualization.
- `MSCL_RESAMPLED_MEASUREMENT`: target measurement name for resampled points (default `mscl_sensors_resampled`).
- `MSCL_RESAMPLED_INCLUDE_RAW_TS`: include original raw timestamp as field `raw_ts_ns` in resampled points.

## Logs and diagnostics

- Follow all container logs:

```bash
./logs.sh
```

- Follow one service:

```bash
./logs.sh mscl-app
```

- MSCL API health:

```bash
curl -s http://localhost:5000/api/health
```

- MSCL API metrics:

```bash
curl -s http://localhost:5000/api/metrics
```

### AP diagnostics services (iperf3)

Use standard system commands:

- Start temporary server:
```bash
iperf3 -s
```

- Stop existing server:
```bash
pkill -f "iperf3 --server"
```

### MQTT + Zigbee2MQTT files

- Mosquitto config: `mosquitto/config/mosquitto.conf`
- Zigbee2MQTT config/data: `zigbee2mqtt/configuration.yaml`
- Telegraf config: `telegraf/telegraf.conf`

### Dashboard (simple-dash + nginx)

- Service: `dashboard` (`nginx:latest`)
- Files served from: `dashboard/simple-dash`
- Primary config file: `dashboard/simple-dash/config.json`
- Port mapping: `${DASHBOARD_PORT:-80}:80`
- `{{host}}` placeholder is supported in links and resolves to `window.location.hostname`

`simple-dash` upstream:
- https://github.com/wiesner-philipp/simple-dash

## Safe cleanup

Cleanup script is project-scoped and does not remove unrelated Docker resources.

- Default stack cleanup:

```bash
./clean-docker.sh
```

## Recovery procedures

### 1) Base station or node stopped responding
1. Check mscl logs: `./logs.sh mscl-app`
2. Trigger reconnect:
```bash
curl -X POST http://localhost:5000/api/reconnect
```
3. If needed, restart mscl container:
```bash
docker compose restart mscl-app
```

### 2) InfluxDB write problems
1. Verify Influx container is healthy:
```bash
docker compose ps
```
2. Check token/org/bucket values in `.env`.
3. Restart writer containers:
```bash
docker compose restart mscl-app redlab-app
```

## Testing

Project tests are `unittest`-based:

```bash
python -m unittest discover -s tests -q
```

## Sampling Validation Snapshot

Validation run on February 13, 2026:
- Run artifacts: `tests/runs/20260213-193644/`
- Node: `16904` (`TC-Link-200`)
- Observed LPF options from node read: `294 Hz` only

### Validated LPF × Sample Rate combinations

| Low Pass Filter | Sample Rate | Test | Result |
|---|---|---|---|
| 294 Hz | 1 Hz (`113`) | start/stop, 90s | PASS |
| 294 Hz | 2 Hz (`112`) | start/stop, 90s | PASS |
| 294 Hz | 4 Hz (`111`) | start/stop, 90s | PASS |
| 294 Hz | 8 Hz (`110`) | start/stop, 90s | PASS |
| 294 Hz | 16 Hz (`109`) | start/stop, 90s | PASS |
| 294 Hz | 64 Hz (`107`) | stability run, 300s | PASS |

Additional idle/read/health checks:
- Idle stability run: 150s
- API errors: none
- Read failures: none

Notes:
- Results above are node/firmware-specific and should be treated as a validated snapshot.
- If firmware exposes additional LPF values later, repeat the same validation procedure before using new combinations in production.
