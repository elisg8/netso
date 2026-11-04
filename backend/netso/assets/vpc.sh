#!/bin/sh
# NetSO virtual PC: a tiny VPCS-style shell on Alpine/busybox (written for NetSO).
#   vpc --boot   container entry point: waits for eth1, applies /etc/vpc.conf, then idles
#   vpc          interactive console (what NetSO opens for this node)
IF=eth1
CONF=/etc/vpc.conf
STATE=/run/vpc.state

wait_if() {
    i=0
    while [ ! -e /sys/class/net/$IF ] && [ $i -lt 120 ]; do sleep 0.5; i=$((i + 1)); done
    ip link set $IF up 2>/dev/null
}

mask2len() { # 255.255.255.0 -> 24
    n=0
    for o in $(echo "$1" | tr . ' '); do
        case $o in 255) n=$((n + 8)) ;; 254) n=$((n + 7)) ;; 252) n=$((n + 6)) ;; 248) n=$((n + 5)) ;;
            240) n=$((n + 4)) ;; 224) n=$((n + 3)) ;; 192) n=$((n + 2)) ;; 128) n=$((n + 1)) ;; esac
    done
    echo $n
}

set_ip() { # ip 10.0.0.2/24 10.0.0.1 [dns] | ip 10.0.0.2 255.255.255.0 10.0.0.1 [dns] | ip dhcp
    if [ "$1" = "dhcp" ]; then
        ip addr flush dev $IF
        echo "DHCP su $IF..."
        if udhcpc -i $IF -n -q -t 5 -T 2 >/dev/null 2>&1; then
            echo "ip dhcp" > $STATE; show_ip
        else
            echo "Nessuna risposta DHCP"
        fi
        return
    fi
    addr=$1; shift
    case $addr in
        */*) ;;
        *) if echo "$1" | grep -q '^255\.\|^0\.'; then addr="$addr/$(mask2len "$1")"; shift; else addr="$addr/24"; fi ;;
    esac
    gw=$1; dns=$2
    ip addr flush dev $IF
    ip addr add "$addr" dev $IF || return
    ip route del default 2>/dev/null
    [ -n "$gw" ] && ip route add default via "$gw" dev $IF
    [ -n "$dns" ] && echo "nameserver $dns" > /etc/resolv.conf
    echo "ip $addr${gw:+ $gw}${dns:+ $dns}" > $STATE
    echo "Indirizzo: $addr${gw:+  gateway: $gw}${dns:+  dns: $dns}"
}

show_ip() {
    a=$(ip -4 -o addr show dev $IF 2>/dev/null | awk '{print $4}' | head -1)
    g=$(ip route show default 2>/dev/null | awk '{print $3}' | head -1)
    m=$(cat /sys/class/net/$IF/address 2>/dev/null)
    d=$(awk '/^nameserver/{print $2; exit}' /etc/resolv.conf 2>/dev/null)
    printf 'NOME     : %s\nIP/MASK  : %s\nGATEWAY  : %s\nDNS      : %s\nMAC      : %s\nINTERFACCIA: %s\n' \
        "$(hostname)" "${a:-non configurato}" "${g:--}" "${d:--}" "${m:--}" "$IF"
}

help() {
    cat <<'EOF'
Comandi (stile VPCS):
  ip 10.0.0.2/24 10.0.0.1 [dns]   indirizzo, gateway e DNS (anche: ip 10.0.0.2 255.255.255.0 10.0.0.1)
  ip dhcp                         indirizzo dal DHCP (es. nuvola NAT)
  show ip | show                  configurazione attuale
  ping <host> [-c N]              ping (5 pacchetti di default)
  trace <host>                    traceroute
  arp                             tabella ARP
  clear ip                        cancella indirizzo e gateway
  save                            mostra la riga da mettere nella startup config
  sh                              shell Linux completa (exit per tornare)
  exit                            chiudi la consolle (il PC resta acceso)
EOF
}

run() {
    cmd=$1; [ $# -gt 0 ] && shift
    case $cmd in
        "") ;;
        ip) [ $# -eq 0 ] && { help; return; }; set_ip "$@" ;;
        dhcp) set_ip dhcp ;;
        show | sh_ip) show_ip ;;
        ping) h=$1; [ -z "$h" ] && { echo "uso: ping <host> [-c N]"; return; }; shift
            c=5; [ "$1" = "-c" ] && c=$2; ping -c "$c" -W 2 "$h" ;;
        trace | tracert | traceroute) [ -z "$1" ] && { echo "uso: trace <host>"; return; }; traceroute -w 2 -m 20 "$1" ;;
        arp) ip neigh show dev $IF ;;
        clear) ip addr flush dev $IF; ip route del default 2>/dev/null; rm -f $STATE; echo "Configurazione cancellata" ;;
        save) if [ -f $STATE ]; then echo "Copia questa riga nella startup config del nodo:"; cat $STATE; else echo "Niente da salvare"; fi ;;
        sh | bash) /bin/sh ;;
        help | "?") help ;;
        exit | quit | q) exit 0 ;;
        *) echo "Comando sconosciuto: $cmd (scrivi ? per l'aiuto)" ;;
    esac
}

if [ "$1" = "--boot" ]; then
    wait_if
    if [ -f $CONF ]; then
        grep -v '^\s*#' $CONF | while read -r line; do [ -n "$line" ] && run $line; done
    fi
    while :; do sleep 3600; done
fi

echo "NetSO VPC $(hostname) — scrivi ? per l'aiuto"
while printf '%s> ' "$(hostname)" && read -r line; do
    # shellcheck disable=SC2086
    run $line
done
