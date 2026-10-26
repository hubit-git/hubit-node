#!/usr/bin/env bash
# Installs (or updates) hubit-node in /opt/hubit-node and starts it. Run as root:
#   bash <(curl -fsSL https://raw.githubusercontent.com/hubit-git/hubit-node/main/install.sh)
# or from a checkout of the project: ./install.sh. Running it again updates; the data in /opt/hubit-node/data stays.
set -euo pipefail

TARGET=/opt/hubit-node
REPO=hubit-git/hubit-node
BRANCH="${HUBIT_NODE_BRANCH:-main}"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd || true)"

[ "$(id -u)" = 0 ] || { echo "run as root" >&2; exit 1; }

if ! command -v docker >/dev/null; then
    echo "Installing Docker..."
    curl -fsSL https://get.docker.com | sh
fi

# OpenVPN clients are routed through this server (a container on the host network cannot set this itself)
echo "net.ipv4.ip_forward=1" > /etc/sysctl.d/99-hubit-node.conf
sysctl -q -w net.ipv4.ip_forward=1

mkdir -p "$TARGET"
rm -rf "$TARGET/hubit_node"  # a file removed in a new version must not stay behind
if [ -f "$SRC/Dockerfile" ]; then
    [ "$SRC" = "$TARGET" ] || cp -r "$SRC/Dockerfile" "$SRC/docker-compose.yml" "$SRC/hubit_node" "$SRC/bin" "$TARGET/"
else
    echo "Downloading hubit-node ($BRANCH)..."
    curl -fsSL "https://github.com/$REPO/archive/refs/heads/$BRANCH.tar.gz" \
        | tar -xz -C "$TARGET" --strip-components=1 --wildcards '*/Dockerfile' '*/docker-compose.yml' '*/hubit_node/*' '*/bin/*'
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

echo "Waiting for the node..."
for _ in $(seq 40); do
    hubit-node api >/dev/null 2>&1 && break
    sleep 1
done

echo
echo "hubit-node is running. What to give Hubit when you add this server as a panel:"
hubit-node api || true
echo
echo "Next: hubit-node host <domain-or-ip> ; hubit-node pki import < bundle.tgz ; hubit-node doctor"
