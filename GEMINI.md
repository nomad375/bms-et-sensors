# GEMINI.md — BMS ET Sensors Stack Assistant Guide

## Project Identity & Scope

This repository contains the **BMS ET Sensors Stack**, a modular Docker-based sensor data acquisition, storage, and visualization system targeted for Linux hosts (primarily Raspberry Pi 4/5 in field/standalone environments, as well as x86_64/AMD64 servers) and ESP32 microcontroller firmware nodes.

## Communication & Language Rules

- **User Responses**: Always answer the user in **Russian** (unless explicitly requested otherwise).
- **Code & Repository Artifacts**: All code, comments, docstrings, commit messages, and configuration entries must be written in **English**.

## Architecture & Services Overview

### Core Custom Collectors (`app/`)
- `mscl-collector` (`app/mscl`): MicroStrain MSCL integration for LORD Sensing wireless base stations (WSDA-Base-200) and nodes. Manages node configuration, RF power, sampling, and streaming to InfluxDB.
- `redlab-collector` (`app/redlab`): Measurement Computing (MCC) RedLab USB-TC thermocouple collector. Discovered by USB Vendor/Product ID; includes bus re-enumeration recovery.
- `almemo-collector` (`app/almemo`): Ahlborn ALMEMO serial integration (RS-232/USB). Uses flow-control safeguards (`XON/XOFF`), command batching via `/api/command-sequence`, and fast overview sequences (`G00`, `Mxx`, `f2 P00`, `P32`).
- `pyrometer-collector` (`app/pyrometers`): Unified serial collector for Micro-Epsilon thermoMETER CT and Optris CT pyrometers.
- `matter-collector` (`app/matter`): WebSocket bridge connecting to `matter-server` to ingest Matter attribute reports and events into InfluxDB.
- `messkluppe-collector` (`app/messkluppe`): Collector for the Messkluppe caliper device using nRF24L01+ 2.4 GHz radio over SPI on Raspberry Pi (supports fake/mock mode).

### Infrastructure & UI Services
- `service-controller` (`app/svcctl`): Central system supervisor with Docker socket access (`/var/run/docker.sock`). Implements hardware guards for MSCL, RedLab, and ALMEMO to prevent container crash loops when physical USB hardware is detached.
- `ap-control` (`app/ap`): Raspberry Pi Wi-Fi Access Point manager (interfaces with NetworkManager via D-Bus) for client monitoring and telemetry on `wlan1`.
- `graf-lite` (`app/graf`): Ultra-lightweight Flask/HTML dashboard designed for low-power hosts (RPi), supporting quick sensor graphs and Excel-compatible CSV export (UTF-8 BOM).
- `dashboard` (`dashboard/`): Nginx reverse proxy on port 80 serving `simple-dash` landing page, health probes (`/probe/*`), and routing to backend services.
- `influxdb`: InfluxDB v2.7 time-series database.
- `grafana`: Grafana OSS with automated provisioning for datasources and dashboards.

### Wireless Mesh & Matter Infrastructure (Profile-gated)
- `openthread-border-router` (Profile `thread`): OpenThread Border Router container connected to an RCP dongle (Sonoff Dongle-E / Silicon Labs EFR32MG21) via Spinel HDLC UART. Provides IPv6 routing between the Thread mesh and host network.
- `matter-server` (Profile `matter`): Official Python Matter Server for device commissioning and fabric management.

### Microcontroller Firmware Nodes (`nodes/`)
- ESP-IDF v5.4.1 based firmware under `nodes/`:
  - **Matter over Thread (ESP32-C6)**: `esp32c6zero`, `esp32c6DevKitC`, `esp32c6Pico`, `seeedXiaoEsp32C6`.
  - **Matter over Wi-Fi**: `esp32c3SuperMini`, `m5stickcPlus2`, `esp32sCam`.
- Shared component: `nodes/components/bms_node_core` provides MAC-derived deterministic serial numbering, NVS persistence, and `DeviceInfoProvider`.
- Dual-slot OTA partition table with Matter OTA Requestor; NVS is pinned at `0x9000` to preserve fabric pairing credentials across OTA updates.

## Critical Invariants & Rules

1. **Install-time vs Runtime Isolation**:
   - Tracked templates in Git: `.env.example`, service default configs in `config/`.
   - Local runtime files (NEVER commit): `.env`, `runtime/` (including InfluxDB storage, OpenThread active datasets, Matter credentials, logs).
   - Any new runtime path must be added to `.gitignore`.

2. **Matter Commissioning BLE Policy**:
   - Commissioning requires the dedicated external USB Realtek RTL8761BU dongle (`0bda:8771`, Bluetooth address `8C:88:2B:24:32:8F`).
   - Do NOT use internal Raspberry Pi Cypress/Broadcom BLE (fails with HCI `0x3e`) or MediaTek combo BLE adapters.
   - Start Matter services via `./scripts/restart-matter-server.sh` to properly resolve dynamic `hciN` indexes.

3. **Serial Channel Stability (ALMEMO & Pyrometers)**:
   - Serial communication stability is top priority. Never bypass `XON/XOFF` handling or remove batching in favor of unthrottled concurrent requests.

4. **Hardware-Guarded Lifecycle**:
   - `service-controller` manages startup of hardware-dependent containers. Do not remove or weaken device guards in compose or service scripts.

5. **Raspberry Pi Performance Preservation**:
   - Avoid heavy background tasks, polling loops without sleep, or high-overhead dependencies on the host. Prefer `graf-lite` over full Grafana for simple status checks.

## Common Developer Workflows

### Testing & Python Environment
- Local virtual environment:
  ```bash
  ./scripts/setup-local-python.sh
  ./scripts/test-local.sh
  ```
- Direct unittest execution with venv:
  ```bash
  .venv/bin/python -m unittest discover -s tests -q
  ```

### Lifecycle & Build Scripts
- Full stack rebuild and restart:
  ```bash
  ./scripts/restart-local.sh
  ```
- Build all local container images:
  ```bash
  ./scripts/build-local-all.sh
  ```
- Service-specific builds:
  - `./scripts/build-local-mscl.sh`
  - `./scripts/build-local-redlab.sh`
  - `./scripts/build-local-almemo.sh`
  - `./scripts/build-local-pyrometers.sh`
  - `./scripts/build-local-graf-app.sh`
  - `./scripts/build-local-matter-app.sh`
  - `./scripts/build-local-messkluppe.sh`
  - `./scripts/build-local-svcctl.sh`
  - `./scripts/build-local-ap-ui.sh`
- Thread & Matter services:
  ```bash
  ./scripts/restart-openthread.sh
  ./scripts/restart-matter-server.sh
  ```
- Viewing logs:
  ```bash
  ./scripts/logs.sh
  ./scripts/logs.sh <service-name>
  ```

