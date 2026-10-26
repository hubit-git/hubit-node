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

mkdir -p "$TARGET"
if [ "$SRC" != "$TARGET" ]; then
    cp -r "$SRC/Dockerfile" "$SRC/docker-compose.yml" "$SRC/hubit_node" "$SRC/bin" "$TARGET/"
fi
chmod +x "$TARGET/bin/hubit-node"
ln -sf "$TARGET/bin/hubit-node" /usr/local/bin/hubit-node

cd "$TARGET"
docker compose up -d --build

echo
echo "hubit-node is running. Try: hubit-node status"
