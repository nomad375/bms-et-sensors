# RedLab-TC Temperature Monitoring System

A multi-platform (x86/ARM) monitoring solution for RedLab-TC (USB-TC) DAQ devices. This project automatically collects data from 8 thermocouple channels and visualizes them in a pre-configured Grafana dashboard.



## 🚀 Key Features
- **Plug & Play**: Automatic detection of RedLab-TC devices.
- **Multi-Platform**: Works on PC and Raspberry Pi (ARM).
- **Auto-Filtering**: Built-in Open Thermocouple Detection (OTD) to ignore disconnected sensors.
- **Pre-configured**: Automated InfluxDB setup and Grafana dashboards via provisioning.
- **Dockerized**: No need to install drivers or databases on your host OS.

## 🛠 Hardware Requirements
- RedLab-TC or MCC USB-TC Series device.
- K-type Thermocouples (supports up to 8 channels).
- Host machine: Raspberry Pi 4/5 or any PC with Linux/macOS/Windows.

## 📦 Quick Start

   git clone https://github.com/nomad375/redlab-daq-project.git
   cd redlab-daq-project
   cp .env.example .env # Edit .env to set your passwords/tokens if needed
   chmod +x setup.sh
   ./setup.sh