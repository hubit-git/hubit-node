FROM debian:12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 openssh-server openssl procps passwd iproute2 nftables iptables openvpn \
    && rm -rf /var/lib/apt/lists/*

# Customers' Linux users live far above the host's own users: the container shares the host's network, and the
# traffic counters match on the numeric uid
RUN sed -i 's/^UID_MIN.*/UID_MIN 100000/; s/^UID_MAX.*/UID_MAX 199999/' /etc/login.defs

COPY hubit_node /app/hubit_node
ENV PYTHONPATH=/app PYTHONUNBUFFERED=1
ENTRYPOINT ["python3", "-m", "hubit_node.supervisor"]
