"""SSH for customers: its own sshd on its own port, tunnel only (the host's sshd on 22 stays for the admin)."""
import json
import os
import pwd
import re

from .. import users
from ..config import DATA, host_of
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
MaxStartups 100:30:400
ClientAliveInterval 30
ClientAliveCountMax 3
LogLevel INFO
"""


PAM_FILE = "/etc/pam.d/sshd"
CHECK = "/usr/local/bin/hubit-devcheck"
# after the password was right (Debian's common-auth jumps over pam_deny only on success)
PAM_LINE = f"auth requisite pam_exec.so quiet {CHECK}"

TABLE = "hubit"
# {uid: username} the nftables rules were made for
applied = None


def ports(cfg):
    return [("ssh", "tcp", cfg["ssh"]["port"])]


def connection(cfg):
    """What a customer needs to connect (the password is the one Hubit knows)."""
    return {"protocol": "ssh", "host": host_of(cfg, "ssh"), "port": cfg["ssh"]["port"]}


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
    with open(CHECK, "w") as f:
        f.write("#!/bin/sh\nPYTHONPATH=/app exec python3 -m hubit_node.devcheck ssh\n")
    os.chmod(CHECK, 0o755)
    with open(PAM_FILE) as f:
        pam = f.read()
    if PAM_LINE not in pam:
        if "@include common-auth\n" not in pam:
            raise RuntimeError(f"{PAM_FILE} has no '@include common-auth' line to put the device check after")
        with open(PAM_FILE, "w") as f:
            f.write(pam.replace("@include common-auth\n", f"@include common-auth\n{PAM_LINE}\n", 1))


def snapshot():
    """One pass over /proc: {pid: (parent pid, uid, seconds running, command name)}."""
    ticks = os.sysconf("SC_CLK_TCK")
    uptime = float(open("/proc/uptime").read().split()[0])
    found = {}
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            with open(f"/proc/{name}/stat") as f:
                stat = f.read()
            end = stat.rindex(")")
            fields = stat[end + 2:].split()
            found[int(name)] = (int(fields[1]), os.stat(f"/proc/{name}").st_uid, int(uptime - int(fields[19]) / ticks),
                                stat[stat.index("(") + 1:end])
        except (OSError, ValueError):
            continue  # the process ended while we looked
    return found


def online(cfg):
    """Live connections: [{user, ip, seconds, pid}]. The connection's socket is held by 'sshd: USER [priv]', which has
    a child running as the customer once the login is done (so a login in progress is not counted)."""
    out = run("ss", "-tnpH", "state", "established", f"( sport = :{cfg['ssh']['port']} )")
    if not out:
        return []
    procs = snapshot()
    child_uids = {}
    for ppid, uid, _, _ in procs.values():
        child_uids.setdefault(ppid, set()).add(uid)
    found = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        ip = parts[3].rsplit(":", 1)[0].strip("[]").removeprefix("::ffff:")
        for pid in map(int, re.findall(r"pid=(\d+)", line)):
            info = procs.get(pid)
            if not info or info[3] != "sshd":
                continue
            try:
                with open(f"/proc/{pid}/cmdline") as f:
                    match = re.match(r"sshd: ([a-z][a-z0-9_-]*)", f.read().replace("\0", " "))
                if match and pwd.getpwnam(match.group(1)).pw_uid in child_uids.get(pid, ()):
                    found.append({"user": match.group(1), "ip": ip, "seconds": info[2], "pid": str(pid)})
                    break
            except (OSError, KeyError):
                continue
    return found


def drop(conn):
    """Closes one connection returned by online(): its unprivileged child first, then the 'sshd: USER [priv]' process."""
    run("pkill", "-KILL", "-P", conn["pid"], check=False)
    run("kill", "-KILL", conn["pid"], check=False)


def rules(uids):
    """The counting rules. A customer's sshd child runs as the customer, so everything the tunnel opens on the
    server leaves with the customer's uid: mark that connection (conntrack mark = uid) when it goes out and count
    its packets in both directions into one counter per user. Both the mark and the counter are found by a map
    lookup, so a packet costs the same with 10 customers as with 10000 (a rule per customer slowed all traffic of the
    host by 90 % at 2000). Loopback is not counted."""
    lines = [f"add table inet {TABLE}", f"delete table inet {TABLE}", f"table inet {TABLE} {{"]
    lines += [f"  counter u{uid} {{ }}" for uid in uids]
    if uids:
        lines.append("  map mark_of { type uid : mark; elements = {\n" + ",\n".join(f"    {u} : {u}" for u in uids) + "\n  } }")
        lines.append('  map ctr { type mark : counter; elements = {\n' + ",\n".join(f'    {u} : "u{u}"' for u in uids) + "\n  } }")
    else:
        lines += ["  map mark_of { type uid : mark; }", "  map ctr { type mark : counter; }"]
    lines += [
        "  chain out { type filter hook output priority -150; policy accept;",
        "    ct mark set meta skuid map @mark_of",
        '    oifname != "lo" counter name ct mark map @ctr',
        "  }",
        "  chain in { type filter hook input priority -150; policy accept;",
        '    iifname != "lo" counter name ct mark map @ctr',
        "  }",
        "}",
    ]
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
