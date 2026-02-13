#!/usr/bin/env bash
set -euo pipefail

# Ubuntu side AP stability test runner.
# Runs ping + iperf forward/reverse and stores logs for later analysis.

TARGET_HOST="${TARGET_HOST:-10.42.0.1}"
IPERF_PORT="${IPERF_PORT:-5201}"
TEST_SECONDS="${TEST_SECONDS:-300}"
PING_INTERVAL="${PING_INTERVAL:-0.2}"
WIFI_IFACE="${WIFI_IFACE:-}"
LOG_ROOT="${LOG_ROOT:-./ap-test-logs}"

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "ERROR: required command not found: $1" >&2
    exit 1
  }
}

detect_wifi_iface() {
  if [[ -n "${WIFI_IFACE}" ]]; then
    echo "${WIFI_IFACE}"
    return 0
  fi
  nmcli -t -f DEVICE,TYPE,STATE device status | awk -F: '$2=="wifi" && $3=="connected"{print $1; exit}'
}

route_check() {
  echo "=== Route check ==="
  ip route get "${TARGET_HOST}" || true
  echo
}

start_link_monitor() {
  local iface="$1"
  local out_file="$2"
  (
    while true; do
      printf "[%s]\n" "$(date --iso-8601=seconds)"
      iw dev "${iface}" link || true
      echo
      sleep 1
    done
  ) >>"${out_file}" 2>&1 &
  echo $!
}

run_ping() {
  local out_file="$1"
  timeout "$((TEST_SECONDS + 5))" \
    ping -i "${PING_INTERVAL}" -D "${TARGET_HOST}" >"${out_file}" 2>&1 || true
}

run_iperf() {
  local mode="$1"
  local out_file="$2"
  if [[ "${mode}" == "reverse" ]]; then
    iperf3 -c "${TARGET_HOST}" -p "${IPERF_PORT}" -t "${TEST_SECONDS}" -i 1 -R >"${out_file}" 2>&1 || true
  else
    iperf3 -c "${TARGET_HOST}" -p "${IPERF_PORT}" -t "${TEST_SECONDS}" -i 1 >"${out_file}" 2>&1 || true
  fi
}

summary() {
  local ping_file="$1"
  echo "=== Ping summary ==="
  grep -E "packets transmitted|rtt min/avg/max|round-trip min/avg/max" "${ping_file}" || true
  echo
}

main() {
  need_cmd nmcli
  need_cmd ip
  need_cmd ping
  need_cmd iperf3
  need_cmd iw
  need_cmd timeout

  local iface
  iface="$(detect_wifi_iface)"
  if [[ -z "${iface}" ]]; then
    echo "ERROR: no connected Wi-Fi interface detected. Set WIFI_IFACE explicitly." >&2
    exit 1
  fi

  local ts out_dir ping_log iperf_fwd_log iperf_rev_log link_log link_pid
  ts="$(date +%Y%m%d-%H%M%S)"
  out_dir="${LOG_ROOT}/${ts}"
  mkdir -p "${out_dir}"

  ping_log="${out_dir}/ping.log"
  iperf_fwd_log="${out_dir}/iperf-forward.log"
  iperf_rev_log="${out_dir}/iperf-reverse.log"
  link_log="${out_dir}/wifi-link.log"

  echo "Target: ${TARGET_HOST}:${IPERF_PORT}"
  echo "Wi-Fi iface: ${iface}"
  echo "Duration per iperf direction: ${TEST_SECONDS}s"
  echo "Logs: ${out_dir}"
  echo

  route_check | tee "${out_dir}/route.txt"

  link_pid="$(start_link_monitor "${iface}" "${link_log}")"
  trap 'kill "${link_pid}" >/dev/null 2>&1 || true' EXIT

  echo ">>> Running ping test..."
  run_ping "${ping_log}"

  echo ">>> Running iperf forward test..."
  run_iperf "forward" "${iperf_fwd_log}"

  echo ">>> Running iperf reverse test..."
  run_iperf "reverse" "${iperf_rev_log}"

  kill "${link_pid}" >/dev/null 2>&1 || true
  trap - EXIT

  summary "${ping_log}"
  echo "Done. Logs saved in: ${out_dir}"
}

main "$@"
