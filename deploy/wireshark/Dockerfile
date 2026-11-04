# Wireshark shown in the browser: Xvnc (TigerVNC) + a minimal window manager + Wireshark.
# NetSO runs it with --network host so it can sniff the lab's tap/veth interfaces;
# Xvnc listens on 127.0.0.1 only and the NetSO API proxies it to noVNC in the browser.
FROM debian:bookworm-slim
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      wireshark tigervnc-standalone-server matchbox-window-manager \
      fonts-dejavu-core dbus-x11 ca-certificates \
 && rm -rf /var/lib/apt/lists/*
COPY start.sh /usr/local/bin/netso-wireshark
RUN chmod +x /usr/local/bin/netso-wireshark
ENTRYPOINT ["/usr/local/bin/netso-wireshark"]
