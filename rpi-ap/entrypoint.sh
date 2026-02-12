#!/usr/bin/env bash
set -euo pipefail

iface="${RPI_AP_INTERFACE:-wlan0}"
upstream="${RPI_AP_UPSTREAM_IFACE:-eth0}"
ssid="${RPI_AP_SSID:-bms-ap}"
psk="${RPI_AP_PSK:-ChangeMe123}"
channel="${RPI_AP_CHANNEL:-6}"
cidr="${RPI_AP_CIDR:-10.10.10.1/24}"
dhcp_range="${RPI_AP_DHCP_RANGE:-10.10.10.10,10.10.10.50,12h}"
hw_mode="${RPI_AP_HW_MODE:-g}"
country="${RPI_AP_COUNTRY:-US}"
gateway_ip="${cidr%/*}"

arch="$(uname -m)"
if [[ "$arch" != arm* && "$arch" != aarch64* ]]; then
  echo "rpi-ap is Raspberry Pi only (arm/arm64). Detected $arch."
  exit 1
fi

# Optional extra guard: require Pi model string unless explicitly skipped
if [[ -f /proc/device-tree/model ]]; then
  if ! grep -qi 'raspberry pi' /proc/device-tree/model; then
    echo "Device-tree model is not Raspberry Pi; refusing to start."
    exit 1
  fi
fi

cleanup_rules() {
  iptables -t nat -D POSTROUTING -o "$upstream" -j MASQUERADE 2>/dev/null || true
  iptables -D FORWARD -i "$upstream" -o "$iface" -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || true
  iptables -D FORWARD -i "$iface" -o "$upstream" -j ACCEPT 2>/dev/null || true
}

trap cleanup_rules EXIT

ip link set "$iface" down || true
ip addr flush dev "$iface" || true
ip addr add "$cidr" dev "$iface"
ip link set "$iface" up

cat > /etc/hostapd/hostapd.conf <<EOF
country_code=${country}
interface=${iface}
ssid=${ssid}
hw_mode=${hw_mode}
channel=${channel}
wmm_enabled=1
auth_algs=1
wpa=2
wpa_key_mgmt=WPA-PSK
rsn_pairwise=CCMP
wpa_passphrase=${psk}
driver=nl80211
EOF

cat > /etc/dnsmasq.d/ap.conf <<EOF
interface=${iface}
dhcp-range=${dhcp_range}
dhcp-option=3,${gateway_ip}
dhcp-option=6,1.1.1.1,8.8.8.8
domain-needed
bogus-priv
no-resolv
server=1.1.1.1
server=8.8.8.8
EOF

sysctl -w net.ipv4.ip_forward=1
iptables -t nat -C POSTROUTING -o "$upstream" -j MASQUERADE 2>/dev/null || iptables -t nat -A POSTROUTING -o "$upstream" -j MASQUERADE
iptables -C FORWARD -i "$upstream" -o "$iface" -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || iptables -A FORWARD -i "$upstream" -o "$iface" -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
iptables -C FORWARD -i "$iface" -o "$upstream" -j ACCEPT 2>/dev/null || iptables -A FORWARD -i "$iface" -o "$upstream" -j ACCEPT

dnsmasq --no-daemon --conf-file=/etc/dnsmasq.d/ap.conf &
exec hostapd -d /etc/hostapd/hostapd.conf
