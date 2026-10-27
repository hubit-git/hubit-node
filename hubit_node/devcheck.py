"""Run by sshd's PAM (pam_exec, as root) after the password was right: refuses a login that would be one device too
many. A device = a client IP (a connection, when the client is local: see limits.device_of), so another connection from an
address that is already connected is always fine.
`python3 -m hubit_node.devcheck ssh`; exit 1 = refuse. A failure of the check itself lets the login through (a bug here
must never lock the customers out); the loop of the node drops the extra device later."""
import os
import sys
from datetime import datetime, timezone

from . import config, modules, users
from .system import is_local


def notice(row):
    """What the customer sees in the log of the app after a good login: usage, data limit and time left."""
    gb = lambda n: f"{n / 1024 ** 3:.2f} GB"
    text = f"Used {gb(row['used_bytes'])}" + (f" of {gb(row['data_limit'])}" if row["data_limit"] else " (no data limit)")
    if row["expire_at"]:
        end = datetime.strptime(row["expire_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        days = (end - datetime.now(timezone.utc)).days
        text += f" | {days} days left" if days >= 0 else " | ended"
    return text


def too_many(module, user, row):
    ips = {c["ip"] for c in module.online(config.load()) if c["user"] == user}
    host = os.environ.get("PAM_RHOST", "")
    if is_local(host):
        # Behind a reverse tunnel every client is local, so the newest connection wins: the loop of the node closes the
        # older local ones. Refused only when the devices with real addresses already fill the limit.
        return len({ip for ip in ips if not is_local(ip)}) >= row["devices"]
    return host not in ips and len(ips) >= row["devices"]


def main():
    module = modules.ALL[sys.argv[1]]
    user = os.environ.get("PAM_USER", "")
    row = users.get(user)
    if not row:
        return 0
    if row["devices"] > 0 and too_many(module, user, row):
        return 1
    print(notice(row))  # pam_exec's "stdout" hands it to sshd, which shows it to the client
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"devcheck failed, login allowed: {e}", file=sys.stderr)
        sys.exit(0)
