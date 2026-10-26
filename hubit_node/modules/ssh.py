"""SSH for customers: its own sshd on its own port, tunnel only (the host's sshd on 22 stays for the admin)."""
import json
import os
import pwd
import re

from .. import users
from ..config import DATA
from ..system import run

NAME = "ssh"
CONF = "/etc/ssh/hubit_sshd_config"
KEYS = f"{DATA}/ssh"

TEMPLATE = """Port {port}
HostKey {keys}/ssh_host_ed25519_key
HostKey {keys}/ssh_host_rsa_key
PidFile none
AllowGroups p-ssh
PermitRootLogin no
PasswordAuthentication yes
PubkeyAuthentication no
KbdInteractiveAuthentication no
UsePAM yes
AllowTcpForwarding yes
PermitTTY no
X11Forwarding no
AllowAgentForwarding no
PermitTunnel no
GatewayPorts no
MaxAuthTries 3
LoginGraceTime 20
ClientAliveInterval 30
ClientAliveCountMax 3
LogLevel INFO
"""


TABLE = "hubit"
# {uid: username} the nftables rules were made for
applied = None


def ports(cfg):
    return [("ssh", "tcp", cfg["ssh"]["port"])]


def connection(cfg):
    """What a customer needs to connect (the password is the one Hubit knows)."""
    return {"protocol": "ssh", "host": cfg["host"], "port": cfg["ssh"]["port"]}


def render(cfg):
    os.makedirs(KEYS, exist_ok=True)
    os.makedirs("/run/sshd", exist_ok=True)
    for kind in ("ed25519", "rsa"):
        key = f"{KEYS}/ssh_host_{kind}_key"
        if not os.path.exists(key):
            run("ssh-keygen", "-q", "-t", kind, "-N", "", "-f", key)
    with open(CONF, "w") as f:
        f.write(TEMPLATE.format(port=cfg["ssh"]["port"], keys=KEYS))
    run("/usr/sbin/sshd", "-t", "-f", CONF)


def online(cfg):
    """Live connections: [{user, ip, seconds}]. The connection's socket is held by 'sshd: USER [priv]'."""
    out = run("ss", "-tnpH", "state", "established", f"( sport = :{cfg['ssh']['port']} )")
    found = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        ip = parts[3].rsplit(":", 1)[0].strip("[]")
        for pid in re.findall(r"pid=(\d+)", line):
            try:
                with open(f"/proc/{pid}/cmdline") as f:
                    match = re.match(r"sshd: ([a-z][a-z0-9_-]*)", f.read().replace("\0", " "))
            except OSError:
                continue
            if match:
                seconds = int(run("ps", "-o", "etimes=", "-p", pid, check=False).stdout.strip() or 0)
                found.append({"user": match.group(1), "ip": ip, "seconds": seconds, "pid": pid})
                break
    return found


def drop(conn):
    """Closes one connection returned by online(): its unprivileged child first, then the 'sshd: USER [priv]' process."""
    run("pkill", "-KILL", "-P", conn["pid"], check=False)
    run("kill", "-KILL", conn["pid"], check=False)


def rules(uids):
    """The counting rules. A customer's sshd child runs as the customer, so everything the tunnel opens on the
    server leaves with the customer's uid: mark that connection (conntrack mark = uid) when it goes out and count
    its packets in both directions into one counter per user. Loopback is not counted."""
    lines = [f"add table inet {TABLE}", f"delete table inet {TABLE}", f"table inet {TABLE} {{"]
    lines += [f"  counter u{uid} {{ }}" for uid in uids]
    for chain, hook, rule in (
        ("out", "output", lambda u: f'meta skuid {u} ct mark set {u}\n    oifname != "lo" ct mark {u} counter name "u{u}"'),
        ("in", "input", lambda u: f'iifname != "lo" ct mark {u} counter name "u{u}"'),
    ):
        lines.append(f"  chain {chain} {{ type filter hook {hook} priority -150; policy accept;")
        lines += [f"    {rule(uid)}" for uid in uids]
        lines.append("  }")
    lines.append("}")
    return "\n".join(lines) + "\n"


def read_counters():
    """Reads the counters and sets them to zero in one step (nft reset): {uid: bytes}."""
    done = run("nft", "-j", "reset", "counters", "table", "inet", TABLE, check=False)
    if done.returncode != 0:
        return {}
    found = {}
    for item in json.loads(done.stdout).get("nftables", []):
        counter = item.get("counter")
        if counter and counter["name"].startswith("u"):
            found[int(counter["name"][1:])] = counter["bytes"]
    return found


def usage(cfg):
    """Bytes the customers used since the last call: {username: bytes}. Also keeps the rules in step with the users."""
    global applied
    if applied is None:
        run("nft", "delete", "table", "inet", TABLE, check=False)
        applied = {}
    deltas = {applied[uid]: n for uid, n in read_counters().items() if uid in applied}
    wanted = {}
    for row in users.all_users():
        try:
            wanted[pwd.getpwnam(row["username"]).pw_uid] = row["username"]
        except KeyError:
            pass
    if wanted != applied:
        run("nft", "-f", "-", input=rules(sorted(wanted)))
        applied = wanted
    return deltas


def stop(cfg):
    run("nft", "delete", "table", "inet", TABLE, check=False)


def commands(cfg):
    return {"ssh": ["/usr/sbin/sshd", "-D", "-e", "-f", CONF]}
