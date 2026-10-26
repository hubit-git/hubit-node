"""OpenVPN for customers: a UDP and a TCP server (each can be off), the login is the customer's Linux username and
password (PAM, only for the group p-openvpn), one shared client profile for everybody."""
import os
import socket
import time

from .. import pki
from ..system import run

NAME = "openvpn"
RUN = "/run/hubit-ovpn"
PAM = "/etc/pam.d/hubit-openvpn"
NAT_TABLE = "hubit_nat"

# proto, tunnel device, the network of its clients
SERVERS = {"udp": ("hubit-udp", "10.8.0.0"), "tcp": ("hubit-tcp", "10.9.0.0")}

PAM_TEXT = """auth     required pam_succeed_if.so user ingroup p-openvpn quiet
auth     required pam_unix.so
account  required pam_unix.so
"""

# openvpn runs this when a client leaves and tells us the final bytes of that session
DISCONNECT = """#!/bin/sh
echo "$common_name $bytes_received $bytes_sent" > "%s/$untrusted_ip:$untrusted_port.$$.$(date +%%s)"
"""

TEMPLATE = """port {port}
proto {proto}
dev {dev}
dev-type tun
server {network} 255.255.255.0
topology subnet
ca {pki}/ca.crt
cert {pki}/server.crt
key {pki}/server.key
dh none
tls-crypt {pki}/tls-crypt.key
tls-version-min 1.2
data-ciphers AES-256-GCM:AES-128-GCM:CHACHA20-POLY1305
auth SHA256
verify-client-cert none
username-as-common-name
plugin /usr/lib/openvpn/openvpn-plugin-auth-pam.so hubit-openvpn
script-security 2
client-disconnect {run}/disconnect.sh
push "redirect-gateway def1 bypass-dhcp"
{dns}
keepalive 10 30
user nobody
group nogroup
persist-key
persist-tun
management {run}/{proto}.sock unix
{udp_only}verb 3
"""

PROFILE = """client
dev tun
nobind
{remotes}
server-poll-timeout 5
connect-retry 2
resolv-retry infinite
remote-cert-tls server
verify-x509-name {name} name
auth-user-pass
auth-nocache
explicit-exit-notify 1
auth SHA256
data-ciphers AES-256-GCM:AES-128-GCM:CHACHA20-POLY1305
persist-key
persist-tun
verb 3
<ca>
{ca}
</ca>
<tls-crypt>
{tls_crypt}
</tls-crypt>
"""

# {session address: (username, bytes seen last time)} of the connections alive at the last look
state = {}
# the last answer of clients(): usage() and online() both ask in the same tick
looked = {"at": 0.0, "clients": []}


def servers(cfg):
    return [(proto, cfg["openvpn"][proto]) for proto in SERVERS if cfg["openvpn"].get(proto)]


def ports(cfg):
    return [(f"openvpn", proto, port) for proto, port in servers(cfg)]


def connection(cfg):
    return {"protocol": "openvpn", "host": cfg["host"], **{proto: port for proto, port in servers(cfg)},
            "profile": "/profile/openvpn"}


def ready(cfg):
    return pki.exists()


def render(cfg):
    if not ready(cfg):
        return
    os.makedirs(RUN, exist_ok=True)
    os.chmod(RUN, 0o733)  # openvpn (as nobody) writes the disconnect notes here, only root can list them
    with open(PAM, "w") as f:
        f.write(PAM_TEXT)
    with open(f"{RUN}/disconnect.sh", "w") as f:
        f.write(DISCONNECT % RUN)
    os.chmod(f"{RUN}/disconnect.sh", 0o755)
    dns = "\n".join(f'push "dhcp-option DNS {ip}"' for ip in cfg["openvpn"]["dns"])
    for proto, port in servers(cfg):
        dev, network = SERVERS[proto]
        with open(f"/etc/openvpn/hubit-{proto}.conf", "w") as f:
            f.write(TEMPLATE.format(port=port, proto=proto, dev=dev, network=network, pki=pki.DIR, run=RUN, dns=dns,
                                    udp_only="explicit-exit-notify 1\n" if proto == "udp" else ""))
    open_network()


def commands(cfg):
    if not ready(cfg):
        return {}
    return {f"openvpn-{proto}": ["openvpn", "--config", f"/etc/openvpn/hubit-{proto}.conf"] for proto, _ in servers(cfg)}


def open_network():
    """The clients' traffic is routed through this server: masquerade it, and let it pass Docker's FORWARD policy."""
    nets = ", ".join(f"{network}/24" for _, network in SERVERS.values())
    run("nft", "-f", "-", input=(
        f"add table ip {NAT_TABLE}\ndelete table ip {NAT_TABLE}\n"
        f"table ip {NAT_TABLE} {{\n"
        f"  chain post {{\n"
        f"    type nat hook postrouting priority 100; policy accept;\n"
        f'    ip saddr {{ {nets} }} oifname != "hubit-*" masquerade\n'
        f"  }}\n"
        f"}}\n"))
    for rule in (("-i", "hubit-+", "-j", "ACCEPT"), ("-o", "hubit-+", "-m", "conntrack", "--ctstate",
                                                      "RELATED,ESTABLISHED", "-j", "ACCEPT")):
        if run("iptables", "-C", "DOCKER-USER", *rule, check=False).returncode != 0:
            run("iptables", "-I", "DOCKER-USER", *rule, check=False)  # no DOCKER-USER chain = nothing to open


def stop(cfg):
    run("nft", "delete", "table", "ip", NAT_TABLE, check=False)
    for rule in (("-i", "hubit-+", "-j", "ACCEPT"), ("-o", "hubit-+", "-m", "conntrack", "--ctstate",
                                                      "RELATED,ESTABLISHED", "-j", "ACCEPT")):
        run("iptables", "-D", "DOCKER-USER", *rule, check=False)


def ask(proto, command):
    """One command to the management socket of a server; returns what it answered."""
    sock = socket.socket(socket.AF_UNIX)
    sock.settimeout(3)
    try:
        sock.connect(f"{RUN}/{proto}.sock")
        sock.sendall(command.encode() + b"\n")
        text = b""
        while not (text.rstrip().endswith(b"END") or b"SUCCESS" in text or b"ERROR" in text):
            chunk = sock.recv(65536)
            if not chunk:
                break
            text += chunk
        return text.decode(errors="replace")
    finally:
        sock.close()


def clients(cfg):
    """Connected clients of every server: [{user, ip, addr, proto, seconds, total}]."""
    if time.monotonic() - looked["at"] < 2:
        return looked["clients"]
    found = []
    for proto, _ in servers(cfg):
        try:
            text = ask(proto, "status 3")
        except OSError:
            continue
        columns = []
        for line in text.splitlines():
            cells = line.split("\t")
            if cells[0] == "HEADER" and cells[1] == "CLIENT_LIST":
                columns = cells[2:]
            elif cells[0] == "CLIENT_LIST" and columns:
                row = dict(zip(columns, cells[1:]))
                addr = row["Real Address"]
                found.append({
                    "user": row.get("Username") or row["Common Name"],
                    "ip": addr.rsplit(":", 1)[0], "addr": addr, "proto": proto,
                    "seconds": max(int(time.time()) - int(row["Connected Since (time_t)"]), 0),
                    "total": int(row["Bytes Received"]) + int(row["Bytes Sent"]),
                })
    looked.update(at=time.monotonic(), clients=found)
    return found


def finished():
    """The notes openvpn left when sessions ended: {session address: (username, total bytes)}."""
    notes = {}
    if not os.path.isdir(RUN):
        return notes
    for name in os.listdir(RUN):
        path = f"{RUN}/{name}"
        if name.endswith(".sock") or name == "disconnect.sh" or "." not in name:
            continue
        try:
            user, received, sent = open(path).read().split()
            notes[name.rsplit(".", 2)[0]] = (user, int(received) + int(sent))
            os.remove(path)
        except (OSError, ValueError):
            pass
    return notes


def usage(cfg):
    """Bytes used since the last call: {username: bytes}. A session's counter restarts at every connection, so
    this keeps what it showed last time; the final numbers of ended sessions come from the disconnect notes."""
    deltas = {}
    notes = finished()
    alive = clients(cfg)
    for c in alive:
        if c["addr"] in notes:
            continue
        last = state.get(c["addr"], (c["user"], 0))[1]
        deltas[c["user"]] = deltas.get(c["user"], 0) + max(c["total"] - last, 0)
        state[c["addr"]] = (c["user"], c["total"])
    for addr, (user, total) in notes.items():
        last = state.pop(addr, (user, 0))[1]
        deltas[user] = deltas.get(user, 0) + max(total - last, 0)
    listed = {c["addr"] for c in alive}
    for addr in [a for a in state if a not in listed]:
        del state[addr]  # gone without a note (openvpn was killed): at most one look of its bytes is lost
    return deltas


def online(cfg):
    return clients(cfg)


def drop(conn):
    ask(conn["proto"], f"kill {conn['addr']}")


def profile(cfg):
    """The client file, the same for every customer (the username and password are typed in the app)."""
    if not cfg["host"]:
        raise ValueError("set the host first: hubit-node host <domain-or-ip>")
    if not ready(cfg):
        raise ValueError("no certificate yet: hubit-node pki create (or import)")
    remotes = "\n".join(f"remote {cfg['host']} {port} {proto}" for proto, port in servers(cfg))
    if not remotes:
        raise ValueError("OpenVPN is off on both UDP and TCP")
    return PROFILE.format(remotes=remotes, name=pki.SERVER_NAME, ca=pki.read("ca.crt"),
                          tls_crypt=pki.read("tls-crypt.key"))
