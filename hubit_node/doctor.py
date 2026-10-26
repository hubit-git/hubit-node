"""`hubit-node doctor`: checks what a real server often gets wrong, and says how to fix it."""
import json
import os
import socket
import ssl
import time
import urllib.request

from . import config, modules, pki, users
from .modules import openvpn, ssh
from .system import route_ip, run

results = []


def report(level, text, hint=""):
    results.append(level)
    print(f"[{level}] {text}" + (f"\n       -> {hint}" if hint else ""))


def listening():
    """{(proto, port)} of everything that listens, and the lines of `ss` (with the process names)."""
    lines = run("ss", "-lntupH").splitlines()
    found = set()
    for line in lines:
        parts = line.split()
        if len(parts) >= 5:
            found.add((parts[0], int(parts[4].rsplit(":", 1)[1])))
    return found, lines


def check_system(cfg):
    if os.path.exists("/dev/net/tun"):
        report("OK  ", "/dev/net/tun is there")
    else:
        report("FAIL", "/dev/net/tun is missing", "the host needs the tun module (modprobe tun) and docker-compose.yml must pass the device")
    with open("/proc/sys/net/ipv4/ip_forward") as f:
        if f.read().strip() == "1":
            report("OK  ", "IP forwarding is on")
        else:
            report("FAIL", "IP forwarding is off, OpenVPN clients get no internet",
                   "on the host: sysctl -w net.ipv4.ip_forward=1  and put it in /etc/sysctl.d/99-hubit-node.conf (install.sh does)")
    tables = run("nft", "list", "tables", check=False)
    if tables.returncode != 0:
        report("FAIL", "nft does not work in the container", "the host kernel needs nf_tables; check `docker logs hubit-node`")
        return
    if "ssh" in cfg["protocols"]:
        text = "table inet hubit"
        (report("OK  ", "traffic counting rules (SSH) are loaded") if text in tables.stdout else
         report("WARN", "traffic counting rules are not loaded yet", "they appear within 10 s of the node starting"))
    if "openvpn" in cfg["protocols"] and pki.exists():
        text = "table ip hubit_nat"
        (report("OK  ", "NAT for OpenVPN clients is loaded") if text in tables.stdout else
         report("FAIL", "NAT for OpenVPN clients is missing", "see `docker logs hubit-node`"))
        tool = openvpn.firewall_tool()
        if tool is None:
            report("OK  ", "Docker's FORWARD chain (DOCKER-USER) is not in use, nothing to open")
        elif all(run(tool, "-C", "DOCKER-USER", *rule, check=False).returncode == 0 for rule in openvpn.FORWARD_RULES):
            report("OK  ", f"clients may pass Docker's FORWARD policy ({tool})")
        else:
            report("FAIL", f"the rules in DOCKER-USER ({tool}) are missing", "restart the node: docker restart hubit-node")


def check_ports(cfg):
    found, lines = listening()
    for module in modules.enabled(cfg):
        if module.NAME == "openvpn" and not pki.exists():
            continue
        for label, proto, port in module.ports(cfg):
            if (proto, port) in found:
                report("OK  ", f"{label} listens on {proto}/{port}")
            else:
                report("FAIL", f"nothing listens on {proto}/{port} ({label})", "see `docker logs hubit-node`")
    ssh_line = [line for line in lines if f":{cfg['ssh']['port']} " in line and "sshd" in line]
    if "ssh" in cfg["protocols"]:
        (report("OK  ", "the online list can see which process owns a connection (SYS_PTRACE)") if ssh_line else
         report("FAIL", "`ss` cannot show the owner of connections", "docker-compose.yml needs cap_add: SYS_PTRACE"))
    chains = run("iptables-nft", "-S", check=False).stdout + run("iptables-legacy", "-S", check=False).stdout
    if "ufw-" in chains:
        opens = [f"ufw allow {port}/{proto}" for m in modules.enabled(cfg) for _, proto, port in m.ports(cfg)]
        report("WARN", "ufw is active: the ports of the node must be allowed there", "; ".join(opens))
    report("WARN", "the firewall of your hosting provider (a panel outside the server) is not visible from here",
           "open the same ports there too")


def check_api(cfg):
    context = ssl.create_default_context()
    context.check_hostname, context.verify_mode = False, ssl.CERT_NONE
    request = urllib.request.Request(f"https://127.0.0.1:{cfg['api']['port']}/info", headers={"X-API-Key": cfg["api"]["key"]})
    try:
        with urllib.request.urlopen(request, context=context, timeout=5) as answer:
            info = json.load(answer)
        report("OK  ", f"the API answers on {cfg['api']['port']} (version {info['version']}, {info['total_users']} users)")
    except Exception as e:
        report("FAIL", f"the API does not answer on {cfg['api']['port']}: {e}", "see `docker logs hubit-node`")


def check_config(cfg):
    if "openvpn" in cfg["protocols"]:
        if not pki.exists():
            report("WARN", "no OpenVPN certificate yet", "hubit-node pki import < bundle.tgz   (or pki create)")
        else:
            left = int(run("openssl", "x509", "-in", f"{pki.DIR}/server.crt", "-noout", "-checkend", str(90 * 86400), check=False).returncode)
            (report("WARN", "the server certificate ends within 90 days", pki.expires()) if left else
             report("OK  ", f"the server certificate is valid until {pki.expires()}"))
    if not cfg["host"]:
        report("WARN", "host is not set (customers' files and the API show an empty address)", "hubit-node host <domain-or-ip>")
        return
    try:
        resolved = socket.gethostbyname(cfg["host"])
    except OSError:
        report("FAIL", f"{cfg['host']} does not resolve", "fix the DNS record (a DNS-only record, no CDN proxy)")
        return
    mine = route_ip() or "?"
    if resolved == mine:
        report("OK  ", f"{cfg['host']} points to this server ({mine})")
    else:
        report("WARN", f"{cfg['host']} resolves to {resolved}, this server's address is {mine}",
               "fine behind NAT or while the DNS is not moved yet; otherwise customers cannot reach the node")


def check_runtime(cfg):
    try:
        with open("/run/hubit-tick") as f:
            age = time.time() - float(f.read())
        (report("OK  ", f"the limits loop ran {age:.0f} s ago") if age < 60 else
         report("FAIL", f"the limits loop has not run for {age:.0f} s", "see `docker logs hubit-node`"))
    except OSError:
        up = ssh.snapshot().get(1, (0, 0, 0, ""))[2]  # how long the node (PID 1) has been running
        (report("FAIL", f"the limits loop has not completed once in {up} s: usage, limits and online_at do not work",
                "see `docker logs hubit-node 2>&1 | grep -i loop`") if up > 30 else
         report("WARN", "the limits loop has not run yet", "it runs within seconds of the node starting"))
    memory = int(next(line for line in open("/proc/meminfo") if line.startswith("MemTotal")).split()[1]) // 1024
    report("OK  ", f"server size: {os.cpu_count()} CPU cores, {memory} MB RAM, {len(users.all_users())} users"
           " (about 3 MB of RAM per live SSH connection is a fair guess)")


def main():
    cfg = config.load()
    for check in (check_system, check_ports, check_api, check_config, check_runtime):
        check(cfg)
    fails, warns = results.count("FAIL"), results.count("WARN")
    print(f"\n{fails} failed, {warns} to look at" if fails or warns else "\nall good")
    return 1 if fails else 0
