# BMS ET Sensors Stack

Docker stack for sensor data acquisition and visualization:
- `mscl-app`: MicroStrain MSCL node configuration + stream ingestion + web API.
- `redlab-app`: MCC RedLab thermocouple collector.
- `influxdb`: time-series storage.
- `grafana`: dashboards.

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

## Start commands

- Local amd64 refresh:

```bash
./restart-local-amd64.sh
```

- Build local images for current arch:

```bash
./build-local-all.sh
```

- Build and push multi-arch images:

```bash
./build-push-multiarch.sh
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

### RedLab collector
- `TEMP_MIN`
- `TEMP_MAX`

### MSCL app (optional advanced tuning)
The app also supports runtime tuning via env variables (batch sizes, queue limits, stream cadence, offsets). Defaults are defined in `app/mscl_settings.py`.
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
