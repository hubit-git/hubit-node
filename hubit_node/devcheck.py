"""Run by sshd's PAM (pam_exec, as root) after the password was right: refuses a login that would be one device too
many. A device = a client IP, so another connection from an address that is already connected is always fine.
`python3 -m hubit_node.devcheck ssh`; exit 1 = refuse. A failure of the check itself lets the login through (a bug here
must never lock the customers out); the loop of the node drops the extra device later."""
import os
import sys

from . import config, modules, users


def main():
    module = modules.ALL[sys.argv[1]]
    user = os.environ.get("PAM_USER", "")
    row = users.get(user)
    if not row or row["devices"] <= 0:
        return 0
    ips = {c["ip"] for c in module.online(config.load()) if c["user"] == user}
    return 1 if os.environ.get("PAM_RHOST", "") not in ips and len(ips) >= row["devices"] else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"devcheck failed, login allowed: {e}", file=sys.stderr)
        sys.exit(0)
