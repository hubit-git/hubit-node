FROM debian:12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 openssh-server openssl procps passwd iproute2 \
    && rm -rf /var/lib/apt/lists/*

COPY hubit_node /app/hubit_node
ENV PYTHONPATH=/app PYTHONUNBUFFERED=1
ENTRYPOINT ["python3", "-m", "hubit_node.supervisor"]
