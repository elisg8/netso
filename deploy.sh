#!/usr/bin/env bash
# Deploy di NetSO dal Mac al server.
# Uso:
#   ./deploy.sh                       # compila UI + copia codice + install (riavvia il servizio)
#   ./deploy.sh --con-lab             # come sopra + carica/aggiorna i lab CCIE
#   ./deploy.sh utente@ip             # server diverso da quello di default
#   ./deploy.sh utente@ip --con-lab
set -euo pipefail

SERVER="netso@192.168.12.46"
WITH_LAB=0
for a in "$@"; do
  case "$a" in
    --con-lab) WITH_LAB=1 ;;
    *@*)       SERVER="$a" ;;
  esac
done

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

echo "==> 1/3  Compilo il frontend"
( cd frontend && npm run build )

echo "==> 2/3  Copio il codice su $SERVER:~/NetSO/"
rsync -az --delete \
  --exclude '.git' \
  --exclude '.venv' \
  --exclude 'backend/.venv' \
  --exclude 'node_modules' \
  --exclude 'frontend/node_modules' \
  --exclude '__pycache__' \
  --exclude 'build' \
  --exclude '*.egg-info' \
  --exclude '.pytest_cache' \
  ./ "$SERVER:~/NetSO/"

echo "==> 3/3  Installo sul server (chiede la password per sudo)"
ssh -t "$SERVER" 'cd ~/NetSO && sudo ./install.sh'

if [ "$WITH_LAB" = 1 ]; then
  echo "==> Extra  Carico i lab CCIE in /var/lib/netso/scenario_labs"
  scp deploy/netso-ccie-labs.tar.gz "$SERVER:~/"
  ssh -t "$SERVER" 'sudo mkdir -p /var/lib/netso && sudo tar -xzf ~/netso-ccie-labs.tar.gz -C /var/lib/netso/ && rm -f ~/netso-ccie-labs.tar.gz'
fi

echo
echo "FATTO. Apri  http://${SERVER#*@}:8080"
