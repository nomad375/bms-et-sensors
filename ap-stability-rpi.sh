#!/usr/bin/env bash
set -euo pipefail

# Raspberry side helper for AP stability tests.
# Manages iperf3 server lifecycle on a configurable port.

PORT="${IPERF_PORT:-5201}"
ACTION="${1:-start}"

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "ERROR: required command not found: $1" >&2
    exit 1
  }
}

server_pids() {
  pgrep -f "iperf3.*--server|iperf3 -s" || true
}

port_busy() {
  ss -ltn | awk '{print $4}' | grep -Eq "(^|:)${PORT}$"
}

print_status() {
  echo "=== iperf3 status ==="
  if server_pids >/dev/null; then
    pgrep -af "iperf3.*--server|iperf3 -s" || true
  else
    echo "No iperf3 server process found."
  fi

  if port_busy; then
    echo "Port ${PORT} is listening."
    ss -ltnp | grep -E "(^|:)${PORT}[[:space:]]" || true
  else
    echo "Port ${PORT} is not listening."
  fi
}

start_server() {
  if port_busy; then
    echo "Port ${PORT} is already in use. Keep existing listener."
    print_status
    return 0
  fi

  echo "Starting iperf3 server on port ${PORT}..."
  nohup iperf3 -s -p "${PORT}" -i 1 >/tmp/iperf3-server.log 2>&1 &
  sleep 1
  print_status
}

stop_server() {
  local pids
  pids="$(server_pids)"
  if [[ -z "${pids}" ]]; then
    echo "No iperf3 server to stop."
    return 0
  fi

  echo "Stopping iperf3 server: ${pids}"
  kill ${pids} || true
  sleep 1
  print_status
}

restart_server() {
  stop_server
  start_server
}

main() {
  need_cmd iperf3
  need_cmd ss

  case "${ACTION}" in
    start) start_server ;;
    stop) stop_server ;;
    restart) restart_server ;;
    status) print_status ;;
    *)
      echo "Usage: $0 [start|stop|restart|status]" >&2
      echo "Env: IPERF_PORT=5201" >&2
      exit 2
      ;;
  esac
}

main "$@"
