#!/usr/bin/env bash
# NetSO installer for Ubuntu 22.04/24.04/26.04 and Debian 12 (bare metal, run as root)
set -euo pipefail

PREFIX=/opt/netso
HERE="$(cd "$(dirname "$0")" && pwd)"

[ "$(id -u)" -eq 0 ] || { echo "Run as root: sudo ./install.sh"; exit 1; }

echo "==> System packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q qemu-system-x86 qemu-utils iproute2 telnet \
  genisoimage dosfstools mtools tcpdump libarchive-tools dnsmasq-base iptables \
  rsync openssh-client python3 python3-venv python3-pip util-linux
if ! command -v docker >/dev/null; then
  apt-get install -y -q docker.io
fi
apt-get install -y -q docker-buildx 2>/dev/null || true   # BuildKit, avoids the legacy builder
apt-get install -y -q dynamips 2>/dev/null || echo "    (dynamips not available: the classic 7200/3725 routers will not start)"
# Native Cisco IOL: the i86bi binaries (15.x) are 32-bit and look for libcrypto.so.4
dpkg --add-architecture i386 && apt-get update -q
apt-get install -y -q libc6:i386 libstdc++6:i386 2>/dev/null || true
apt-get install -y -q libssl3t64:i386 2>/dev/null || apt-get install -y -q libssl3:i386 2>/dev/null || true
for d in /usr/lib/i386-linux-gnu /usr/lib/x86_64-linux-gnu; do
  [ -e "$d/libcrypto.so.3" ] && [ ! -e "$d/libcrypto.so.4" ] && ln -s libcrypto.so.3 "$d/libcrypto.so.4" || true
done
# IOL tries to reach xml.cisco.com at startup: resolved locally so it does not hang waiting
grep -q "xml.cisco.com" /etc/hosts || echo "127.0.0.127 xml.cisco.com" >> /etc/hosts
systemctl enable --now docker

echo "==> Built-in Wireshark image (netso/wireshark), a few minutes..."
docker build -t netso/wireshark "$HERE/deploy/wireshark" \
  && echo "    ready" || echo "    WARNING: build failed, Wireshark in the browser will not be available"

echo "==> RDP console in the browser (guacamole/guacd)"
docker pull -q guacamole/guacd:1.5.5 >/dev/null && echo "    ready" || echo "    WARNING: guacd not downloaded, it will be retried on the first RDP console"

echo "==> Virtualization check"
if [ -e /dev/kvm ]; then
  echo "    KVM available"
else
  echo "    WARNING: /dev/kvm missing. Enable VT-x/AMD-V in the BIOS (VMs will be extremely slow without it)."
fi

echo "==> Installing into $PREFIX"
mkdir -p "$PREFIX"/{images,templates,web} /var/lib/netso/labs
python3 -m venv "$PREFIX/venv"
"$PREFIX/venv/bin/pip" install -q --upgrade pip
"$PREFIX/venv/bin/pip" install -q "$HERE/backend"
"$PREFIX/venv/bin/pip" install -q --force-reinstall --no-deps "$HERE/backend"   # same version: reinstall the new code anyway
rm -rf "$PREFIX/web" && cp -r "$HERE/frontend/dist" "$PREFIX/web"
cp -n "$HERE"/examples/*.yml "$PREFIX/" 2>/dev/null || true
ln -sf "$PREFIX/venv/bin/netso" /usr/local/bin/netso

if [ ! -f /etc/netso.env ]; then
  TOKEN="$(head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 32)"
  cat > /etc/netso.env <<ENV
# NetSO configuration
NETSO_TOKEN=$TOKEN
NETSO_HOME=/var/lib/netso
NETSO_IMAGES=$PREFIX/images
NETSO_TEMPLATES=$PREFIX/templates
NETSO_WEB=$PREFIX/web
ENV
  chmod 600 /etc/netso.env
  echo "    API token for scripts generated in /etc/netso.env"
fi

cp "$HERE/deploy/netso.service" /etc/systemd/system/netso.service
systemctl daemon-reload
systemctl enable --now netso
systemctl restart netso

IP="$(hostname -I | awk '{print $1}')"
echo
echo "NetSO installed. Open http://$IP:8080"
echo "Login: user admin, password Netso (change it from the user menu or with: sudo netso passwd admin)"
echo "QEMU images: $PREFIX/images/<name>/disk.qcow2   (e.g. $PREFIX/images/cisco-iosv/)"
echo "Custom templates: $PREFIX/templates/*.yml"
