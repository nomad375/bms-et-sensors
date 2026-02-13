#!/usr/bin/env bash
set -euo pipefail

# Raspberry Pi only:
# - built-in Wi-Fi -> Access Point (NetworkManager)
# - additional Wi-Fi adapters -> normal client Wi-Fi
# - Ethernet config stays as-is, autoconnect is enabled

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${ENV_FILE:-${SCRIPT_DIR}/.env}"

load_env() {
  if [[ -f "${ENV_FILE}" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "${ENV_FILE}"
    set +a
  fi
}

load_env

AP_NAME="${AP_NAME:-rpi-ap}"
AP_SSID="${AP_SSID:-RPI-DAQ-AP}"
AP_PASSWORD="${AP_PASSWORD:-ChangeMe12345}"
AP_BAND="${AP_BAND:-bg}"   # bg or a
AP_CHANNEL="${AP_CHANNEL:-6}"

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "ERROR: required command not found: $1" >&2
    exit 1
  }
}

run_nmcli() {
  if [[ "${EUID}" -eq 0 ]]; then
    nmcli "$@"
  else
    sudo nmcli "$@"
  fi
}

is_usb_wifi() {
  local ifname="$1"
  local dev_path=""
  dev_path="$(readlink -f "/sys/class/net/${ifname}/device" 2>/dev/null || true)"
  [[ "${dev_path}" == *"/usb"* ]]
}

is_builtin_wifi() {
  local ifname="$1"
  # On Raspberry Pi, built-in radio is not on USB bus.
  ! is_usb_wifi "${ifname}"
}

pick_builtin_wifi() {
  local wifi_ifaces=("$@")
  local ifn=""
  for ifn in "${wifi_ifaces[@]}"; do
    if is_builtin_wifi "${ifn}"; then
      echo "${ifn}"
      return 0
    fi
  done
  return 1
}

list_wifi_ifaces() {
  nmcli -t -f DEVICE,TYPE device status | awk -F: '$2=="wifi"{print $1}'
}

list_eth_ifaces() {
  nmcli -t -f DEVICE,TYPE device status | awk -F: '$2=="ethernet"{print $1}'
}

ensure_nm_running() {
  local nm_state
  nm_state="$(nmcli -t -f RUNNING general status | head -n1 || true)"
  if [[ "${nm_state}" != "running" ]]; then
    echo "ERROR: NetworkManager is not running." >&2
    exit 1
  fi
}

validate_ap_password() {
  local len=${#AP_PASSWORD}
  if (( len < 8 || len > 63 )); then
    echo "ERROR: AP_PASSWORD length must be 8..63 for WPA-PSK." >&2
    exit 1
  fi
}

configure_ap_on_builtin_wifi() {
  local ap_if="$1"

  echo ">>> Configure AP '${AP_NAME}' on built-in Wi-Fi: ${ap_if}"

  # Reuse existing AP profile if present, otherwise create.
  if nmcli -t -f NAME connection show | grep -Fxq "${AP_NAME}"; then
    run_nmcli connection modify "${AP_NAME}" \
      connection.type wifi \
      connection.interface-name "${ap_if}" \
      connection.autoconnect yes \
      802-11-wireless.ssid "${AP_SSID}" \
      802-11-wireless.mode ap \
      802-11-wireless.band "${AP_BAND}" \
      802-11-wireless.channel "${AP_CHANNEL}" \
      wifi-sec.key-mgmt wpa-psk \
      wifi-sec.psk "${AP_PASSWORD}" \
      ipv4.method shared \
      ipv6.method ignore
  else
    run_nmcli connection add type wifi ifname "${ap_if}" con-name "${AP_NAME}" ssid "${AP_SSID}"
    run_nmcli connection modify "${AP_NAME}" \
      connection.interface-name "${ap_if}" \
      connection.autoconnect yes \
      802-11-wireless.mode ap \
      802-11-wireless.band "${AP_BAND}" \
      802-11-wireless.channel "${AP_CHANNEL}" \
      wifi-sec.key-mgmt wpa-psk \
      wifi-sec.psk "${AP_PASSWORD}" \
      ipv4.method shared \
      ipv6.method ignore
  fi

  run_nmcli connection up "${AP_NAME}" || {
    echo "ERROR: failed to bring up AP connection '${AP_NAME}'." >&2
    exit 1
  }
}

configure_extra_wifi_as_clients() {
  local ap_if="$1"
  local ifn=""

  while IFS= read -r ifn; do
    [[ -z "${ifn}" ]] && continue
    [[ "${ifn}" == "${ap_if}" ]] && continue

    echo ">>> Keep extra Wi-Fi '${ifn}' in client mode"
    run_nmcli device set "${ifn}" managed yes || true

    # If this iface is currently on an AP-mode connection, disconnect it.
    local active_conn mode
    active_conn="$(nmcli -t -f GENERAL.CONNECTION device show "${ifn}" 2>/dev/null | head -n1 | cut -d: -f2- || true)"
    if [[ -n "${active_conn}" && "${active_conn}" != "--" ]]; then
      mode="$(nmcli -g 802-11-wireless.mode connection show "${active_conn}" 2>/dev/null || true)"
      if [[ "${mode}" == "ap" ]]; then
        run_nmcli connection down "${active_conn}" || true
      fi
    fi
  done < <(list_wifi_ifaces)
}

ensure_ethernet_autoconnect() {
  local ifn=""
  while IFS= read -r ifn; do
    [[ -z "${ifn}" ]] && continue
    echo ">>> Ensure Ethernet autoconnect on '${ifn}' (no IP mode rewrite)"

    local existing=""
    existing="$(nmcli -t -f NAME,TYPE,DEVICE connection show | awk -F: -v dev="${ifn}" '$2=="802-3-ethernet" && $3==dev {print $1; exit}')"

    if [[ -n "${existing}" ]]; then
      run_nmcli connection modify "${existing}" connection.autoconnect yes || true
      run_nmcli connection up "${existing}" ifname "${ifn}" >/dev/null 2>&1 || true
    else
      local cname="wired-auto-${ifn}"
      run_nmcli connection add type ethernet ifname "${ifn}" con-name "${cname}" autoconnect yes ipv4.method auto ipv6.method auto
      run_nmcli connection up "${cname}" ifname "${ifn}" >/dev/null 2>&1 || true
    fi
  done < <(list_eth_ifaces)
}

print_summary() {
  echo
  echo "=== NetworkManager summary ==="
  nmcli -f DEVICE,TYPE,STATE,CONNECTION device status
  echo
  echo "AP profile: ${AP_NAME}"
  nmcli -f connection.id,connection.interface-name,connection.autoconnect,802-11-wireless.ssid,802-11-wireless.mode,ipv4.method connection show "${AP_NAME}" || true
}

main() {
  need_cmd nmcli
  ensure_nm_running
  validate_ap_password

  mapfile -t wifi_ifaces < <(list_wifi_ifaces)
  if [[ "${#wifi_ifaces[@]}" -eq 0 ]]; then
    echo "ERROR: no Wi-Fi interfaces found." >&2
    exit 1
  fi

  local ap_if
  ap_if="$(pick_builtin_wifi "${wifi_ifaces[@]}")" || {
    echo "ERROR: could not detect built-in Wi-Fi (non-USB) interface." >&2
    exit 1
  }

  configure_ap_on_builtin_wifi "${ap_if}"
  configure_extra_wifi_as_clients "${ap_if}"
  ensure_ethernet_autoconnect
  print_summary
}

main "$@"
