#!/usr/bin/env bash
# NetSO installer for Ubuntu 22.04/24.04/26.04 and Debian 12 (bare metal, run as root)
set -euo pipefail

PREFIX=/opt/netso
HERE="$(cd "$(dirname "$0")" && pwd)"

[ "$(id -u)" -eq 0 ] || { echo "Esegui come root: sudo ./install.sh"; exit 1; }

echo "==> Pacchetti di sistema"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q qemu-system-x86 qemu-utils iproute2 telnet \
  genisoimage dosfstools mtools tcpdump libarchive-tools dnsmasq-base iptables \
  rsync openssh-client python3 python3-venv python3-pip util-linux
if ! command -v docker >/dev/null; then
  apt-get install -y -q docker.io
fi
apt-get install -y -q docker-buildx 2>/dev/null || true   # BuildKit, evita il builder legacy
apt-get install -y -q dynamips 2>/dev/null || echo "    (dynamips non disponibile: i router classici 7200/3725 non partiranno)"
# Cisco IOL nativo: i binari i86bi (15.x) sono a 32 bit e cercano libcrypto.so.4
dpkg --add-architecture i386 && apt-get update -q
apt-get install -y -q libc6:i386 libstdc++6:i386 2>/dev/null || true
apt-get install -y -q libssl3t64:i386 2>/dev/null || apt-get install -y -q libssl3:i386 2>/dev/null || true
for d in /usr/lib/i386-linux-gnu /usr/lib/x86_64-linux-gnu; do
  [ -e "$d/libcrypto.so.3" ] && [ ! -e "$d/libcrypto.so.4" ] && ln -s libcrypto.so.3 "$d/libcrypto.so.4" || true
done
# IOL prova a contattare xml.cisco.com all'avvio: risolto in locale non resta in attesa
grep -q "xml.cisco.com" /etc/hosts || echo "127.0.0.127 xml.cisco.com" >> /etc/hosts
systemctl enable --now docker

echo "==> Immagine Wireshark integrato (netso/wireshark), qualche minuto..."
docker build -t netso/wireshark "$HERE/deploy/wireshark" \
  && echo "    pronta" || echo "    ATTENZIONE: build fallita, Wireshark nel browser non sarà disponibile"

echo "==> Consolle RDP nel browser (guacamole/guacd)"
docker pull -q guacamole/guacd:1.5.5 >/dev/null && echo "    pronta" || echo "    ATTENZIONE: guacd non scaricato, verrà riprovato alla prima consolle RDP"

echo "==> Controllo virtualizzazione"
if [ -e /dev/kvm ]; then
  echo "    KVM disponibile"
else
  echo "    ATTENZIONE: /dev/kvm assente. Abilita VT-x/AMD-V nel BIOS (le VM andranno lentissime senza)."
fi

echo "==> Installazione in $PREFIX"
mkdir -p "$PREFIX"/{images,templates,web} /var/lib/netso/labs
python3 -m venv "$PREFIX/venv"
"$PREFIX/venv/bin/pip" install -q --upgrade pip
"$PREFIX/venv/bin/pip" install -q "$HERE/backend"
"$PREFIX/venv/bin/pip" install -q --force-reinstall --no-deps "$HERE/backend"   # stessa versione: reinstalla comunque il codice nuovo
rm -rf "$PREFIX/web" && cp -r "$HERE/frontend/dist" "$PREFIX/web"
cp -n "$HERE"/examples/*.yml "$PREFIX/" 2>/dev/null || true
ln -sf "$PREFIX/venv/bin/netso" /usr/local/bin/netso

if [ ! -f /etc/netso.env ]; then
  TOKEN="$(head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 32)"
  cat > /etc/netso.env <<ENV
# Configurazione NetSO
NETSO_TOKEN=$TOKEN
NETSO_HOME=/var/lib/netso
NETSO_IMAGES=$PREFIX/images
NETSO_TEMPLATES=$PREFIX/templates
NETSO_WEB=$PREFIX/web
ENV
  chmod 600 /etc/netso.env
  echo "    Token API per script generato in /etc/netso.env"
fi

cp "$HERE/deploy/netso.service" /etc/systemd/system/netso.service
systemctl daemon-reload
systemctl enable --now netso
systemctl restart netso

IP="$(hostname -I | awk '{print $1}')"
echo
echo "NetSO installato. Apri http://$IP:8080"
echo "Accesso: utente admin, password Netso (cambiala dal menu utente o con: sudo netso passwd admin)"
echo "Immagini QEMU: $PREFIX/images/<nome>/disco.qcow2   (es. $PREFIX/images/cisco-iosv/)"
echo "Template personalizzati: $PREFIX/templates/*.yml"
