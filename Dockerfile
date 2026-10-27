# UDP over the SSH tunnel for video calls: badvpn's udpgw (not packaged for Debian 12), built from its source
FROM debian:12-slim AS udpgw
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential cmake git ca-certificates \
    && git clone --depth 1 --branch 1.999.130 https://github.com/ambrop72/badvpn.git /src \
    && cmake -S /src -B /build -DBUILD_NOTHING_BY_DEFAULT=1 -DBUILD_UDPGW=1 \
    && make -C /build install

FROM debian:12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 ca-certificates openssh-server openssl procps passwd iproute2 nftables iptables openvpn \
    && rm -rf /var/lib/apt/lists/*

# Customers' Linux users live far above the host's own users: the container shares the host's network, and the
# traffic counters match on the numeric uid
RUN sed -i 's/^UID_MIN.*/UID_MIN 100000/; s/^UID_MAX.*/UID_MAX 199999/' /etc/login.defs

COPY --from=udpgw /usr/local/bin/badvpn-udpgw /usr/local/bin/badvpn-udpgw
COPY hubit_node /app/hubit_node
ENV PYTHONPATH=/app PYTHONUNBUFFERED=1
ENTRYPOINT ["python3", "-m", "hubit_node.supervisor"]
