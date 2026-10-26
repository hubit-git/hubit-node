import ipaddress
import subprocess
import urllib.request


def run(*args, input=None, check=True):
    """Runs a command (never through a shell) and returns its stdout."""
    done = subprocess.run(args, input=input, capture_output=True, text=True)
    if check and done.returncode != 0:
        raise RuntimeError(f"{args[0]} failed: {done.stderr.strip() or done.stdout.strip()}")
    return done.stdout.strip() if check else done


def route_ip():
    """The address of this server on its default route ('' when there is none)."""
    parts = run("ip", "-4", "route", "get", "1.1.1.1", check=False).stdout.split(" src ")
    return parts[1].split()[0] if len(parts) > 1 else ""


def public_ip():
    """This server's address as customers see it: asked of a lookup service (behind NAT the route address is a private
    one), else the route address."""
    for url in ("https://api.ipify.org", "https://ifconfig.me/ip"):
        try:
            with urllib.request.urlopen(url, timeout=3) as answer:
                return str(ipaddress.ip_address(answer.read().decode().strip()))
        except Exception:
            continue
    return route_ip()
