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

# ---- first setup: what to run, on which ports (only when there is no config yet; an update never asks) ----
CONFIG="$TARGET/data/config.json"
INTERACTIVE=0
if [ ! -f "$CONFIG" ] && [ "${HUBIT_YES:-0}" != "1" ] && [ -r /dev/tty ] && [ -w /dev/tty ]; then
    INTERACTIVE=1
fi

CHOSEN_TCP=()   # TCP ports taken by this setup, so two services of the node never get the same one

# port_busy PROTO PORT -> the name of the program that listens there (succeeds only when something does)
port_busy() {
    command -v ss >/dev/null || return 1
    local flag="-ltnp"
    [ "$1" = udp ] && flag="-lunp"
    local line name
    line="$(ss -H $flag "sport = :$2" 2>/dev/null | head -1)"
    [ -n "$line" ] || return 1
    name="$(sed -n 's/.*users:(("\([^"]*\)".*/\1/p' <<<"$line")"
    echo "${name:-another program}"
}

# next_free PROTO PORT -> the first free port after PORT
next_free() {
    local p=$(( $2 + 1 ))
    while port_busy "$1" "$p" >/dev/null || { [ "$1" = tcp ] && [[ " ${CHOSEN_TCP[*]:-} " == *" $p "* ]]; }; do
        p=$(( p + 1 ))
    done
    echo "$p"
}

# ask LABEL DEFAULT -> the answer (the default on an empty line)
ask() {
    local answer
    read -r -p "$1 [$2]: " answer </dev/tty
    echo "${answer:-$2}"
}

# ask_port VAR PROTO LABEL DEFAULT [off]: asks until the port is a number, free and not taken by this setup.
# Not interactive: takes the value of $VAR (or the default) and stops when it cannot be used.
ask_port() {
    local var="$1" proto="$2" label="$3" default="$4" off="${5:-}" value owner
    value="${!var:-$default}"
    while true; do
        [ "$INTERACTIVE" = 1 ] && value="$(ask "$label" "$value")"
        if [ -n "$off" ] && [ "$value" = off ]; then
            value=0
            break
        fi
        if ! [[ "$value" =~ ^[0-9]+$ ]] || [ "$value" -lt 1 ] || [ "$value" -gt 65535 ]; then
            echo "  '$value' is not a port (1-65535${off:+, or off})." >&2
            [ "$INTERACTIVE" = 1 ] || exit 1
            value="$default"
            continue
        elif [ "$proto" = tcp ] && [[ " ${CHOSEN_TCP[*]:-} " == *" $value "* ]]; then
            echo "  $value is already used by another service of this node." >&2
        elif owner="$(port_busy "$proto" "$value")"; then
            echo "  $proto/$value is in use by $owner." >&2
        else
            break
        fi
        [ "$INTERACTIVE" = 1 ] || { echo "Free that port or give another one (variable $var)." >&2; exit 1; }
        value="$(next_free "$proto" "$value")"
        echo "  Free port suggested: $value" >&2
    done
    if [ "$proto" = tcp ] && [ "$value" != 0 ]; then
        CHOSEN_TCP+=("$value")
    fi
    printf -v "$var" '%s' "$value"
}

first_setup() {
    local host protocols yn owner ssh_on=0 ovpn_on=0 udpgw=0 list=""
    echo
    echo "First setup (Enter takes the value in brackets; everything can be changed later with the hubit-node command)"

    host="${HUBIT_HOST:-}"
    [ "$INTERACTIVE" = 1 ] && host="$(ask "Domain or IP customers connect to (- = this server's address)" "${host:--}")"
    [ "$host" = "-" ] && host=""

    protocols="${HUBIT_PROTOCOLS:-ssh,openvpn}"
    [ "$INTERACTIVE" = 1 ] && protocols="$(ask "Protocols to run (ssh, openvpn, or ssh,openvpn)" "$protocols")"
    [[ ",$protocols," == *,ssh,* ]] && ssh_on=1
    [[ ",$protocols," == *,openvpn,* ]] && ovpn_on=1
    if [ "$ssh_on$ovpn_on" = 00 ]; then
        echo "Give at least one of: ssh, openvpn" >&2
        exit 1
    fi

    HUBIT_API_PORT="${HUBIT_API_PORT:-}"
    HUBIT_SSH_PORT="${HUBIT_SSH_PORT:-}"
    HUBIT_UDP_PORT="${HUBIT_UDP_PORT:-}"
    HUBIT_TCP_PORT="${HUBIT_TCP_PORT:-}"

    ask_port HUBIT_API_PORT tcp "API port (Hubit Panel connects here)" 8443

    SSH_PORT=2222
    if [ "$ssh_on" = 1 ]; then
        ask_port HUBIT_SSH_PORT tcp "SSH port of the customers" 2222
        SSH_PORT="$HUBIT_SSH_PORT"
        yn="${HUBIT_UDPGW:-yes}"
        [ "$INTERACTIVE" = 1 ] && yn="$(ask "UDP for video calls over SSH (udpgw, not counted in usage)? yes/no" "$yn")"
        if [[ "$yn" =~ ^[yY] ]]; then
            if owner="$(port_busy tcp 7300)"; then
                echo "  tcp/7300 (udpgw) is in use by $owner: udpgw stays off (hubit-node ssh-udpgw PORT turns it on later)." >&2
            else
                udpgw=7300
            fi
        fi
    fi

    UDP_PORT=0
    TCP_PORT=0
    if [ "$ovpn_on" = 1 ]; then
        ask_port HUBIT_UDP_PORT udp "OpenVPN UDP port (or off)" 1194 off
        ask_port HUBIT_TCP_PORT tcp "OpenVPN TCP port (or off)" 1194 off
        UDP_PORT="$HUBIT_UDP_PORT"
        TCP_PORT="$HUBIT_TCP_PORT"
        if [ "$UDP_PORT" = 0 ] && [ "$TCP_PORT" = 0 ]; then
            echo "Keep at least one of the OpenVPN UDP and TCP ports." >&2
            exit 1
        fi
    fi

    [ "$ssh_on" = 1 ] && list='"ssh"'
    [ "$ovpn_on" = 1 ] && list="${list:+$list, }\"openvpn\""

    echo
    echo "Summary:"
    echo "  address:   ${host:-(the address of this server)}"
    echo "  protocols: $protocols"
    echo "  API:       $HUBIT_API_PORT/tcp"
    [ "$ssh_on" = 1 ] && echo "  SSH:       $SSH_PORT/tcp$([ "$udpgw" != 0 ] && echo ", udpgw on 127.0.0.1:$udpgw")"
    [ "$ovpn_on" = 1 ] && echo "  OpenVPN:   udp $([ "$UDP_PORT" = 0 ] && echo off || echo "$UDP_PORT"), tcp $([ "$TCP_PORT" = 0 ] && echo off || echo "$TCP_PORT")"
    if [ "$INTERACTIVE" = 1 ]; then
        yn="$(ask "Continue? yes/no" yes)"
        [[ "$yn" =~ ^[yY] ]] || { echo "Stopped; nothing was started. Run the command again."; exit 1; }
    fi

    mkdir -p "$TARGET/data"
    cat > "$CONFIG" <<CONFIG_END
{
  "host": "$host",
  "api": {"port": $HUBIT_API_PORT, "key": ""},
  "ssh": {"port": $SSH_PORT, "udpgw": $udpgw},
  "openvpn": {"udp": $UDP_PORT, "tcp": $TCP_PORT, "dns": ["1.1.1.1", "8.8.8.8"]},
  "protocols": [$list]
}
CONFIG_END

    FIREWALL+=("ufw allow $HUBIT_API_PORT/tcp   # the API: Hubit connects here")
    [ "$ssh_on" = 1 ] && FIREWALL+=("ufw allow $SSH_PORT/tcp   # SSH of the customers")
    [ "$UDP_PORT" != 0 ] && FIREWALL+=("ufw allow $UDP_PORT/udp   # OpenVPN UDP")
    [ "$TCP_PORT" != 0 ] && FIREWALL+=("ufw allow $TCP_PORT/tcp   # OpenVPN TCP")
    return 0
}

FIREWALL=()
if [ ! -f "$CONFIG" ]; then
    first_setup
fi

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
if [ "${#FIREWALL[@]}" -gt 0 ]; then
    echo "Open these ports in the firewall of the server (ufw example) and in your hosting provider's firewall too:"
    printf '  %s\n' "${FIREWALL[@]}"
    echo
fi
echo "Next: upload the OpenVPN certificate in Hubit Panel (Panels), add this node there, then check it with: hubit-node doctor"
