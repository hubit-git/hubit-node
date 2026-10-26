#!/usr/bin/env bash
# Installs hubit-node to /opt/hubit-node and starts it. Run as root from a checkout of the project.
set -euo pipefail

TARGET=/opt/hubit-node
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

[ "$(id -u)" = 0 ] || { echo "run as root" >&2; exit 1; }
[ -f "$SRC/Dockerfile" ] || { echo "run install.sh from a checkout of hubit-node" >&2; exit 1; }

if ! command -v docker >/dev/null; then
    echo "Installing Docker..."
    curl -fsSL https://get.docker.com | sh
fi

# OpenVPN clients are routed through this server (a container on the host network cannot set this itself)
echo "net.ipv4.ip_forward=1" > /etc/sysctl.d/99-hubit-node.conf
sysctl -q -w net.ipv4.ip_forward=1

mkdir -p "$TARGET"
if [ "$SRC" != "$TARGET" ]; then
    cp -r "$SRC/Dockerfile" "$SRC/docker-compose.yml" "$SRC/hubit_node" "$SRC/bin" "$TARGET/"
fi
chmod +x "$TARGET/bin/hubit-node"
ln -sf "$TARGET/bin/hubit-node" /usr/local/bin/hubit-node

cd "$TARGET"
# the published image when there is one, else build it here
if docker compose pull -q 2>/dev/null; then
    docker compose up -d
else
    docker compose up -d --build
fi

echo
echo "hubit-node is running. Try: hubit-node status"
