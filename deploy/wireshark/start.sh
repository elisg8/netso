#!/bin/sh
# Env: IFACE + FILTER + OUTFILE (live capture) or READFILE (open a saved capture);
#      DISPLAY_NUM, VNC_PORT, GEOMETRY
set -e
: "${DISPLAY_NUM:=99}" "${VNC_PORT:=15900}" "${GEOMETRY:=1600x900}"
export DISPLAY=":$DISPLAY_NUM" HOME=/root XDG_RUNTIME_DIR=/tmp/xdg
mkdir -p "$XDG_RUNTIME_DIR" && chmod 700 "$XDG_RUNTIME_DIR"

# host network namespace: only loopback, no password (the API in front checks the token)
Xvnc "$DISPLAY" -rfbport "$VNC_PORT" -localhost -SecurityTypes None -AlwaysShared \
  -geometry "$GEOMETRY" -depth 24 -nolisten tcp &
for _ in $(seq 50); do [ -e "/tmp/.X11-unix/X$DISPLAY_NUM" ] && break; sleep 0.1; done

# keeps Wireshark maximized, follows browser resizes
matchbox-window-manager -use_titlebar no &

[ -n "$READFILE" ] && exec wireshark -r "$READFILE"

set -- -k -i "$IFACE"
[ -n "$FILTER" ] && set -- "$@" -f "$FILTER"
[ -n "$OUTFILE" ] && set -- "$@" -w "$OUTFILE"
exec wireshark "$@"
