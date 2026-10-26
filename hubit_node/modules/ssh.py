"""SSH for customers: its own sshd on its own port, tunnel only (the host's sshd on 22 stays for the admin)."""
import os
import re

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


def ports(cfg):
    return [("ssh", "tcp", cfg["ssh"]["port"])]


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
                found.append({"user": match.group(1), "ip": ip, "seconds": seconds})
                break
    return found


def command(cfg):
    return ["/usr/sbin/sshd", "-D", "-e", "-f", CONF]
