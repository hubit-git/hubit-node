"""`hubit-node ...`: runs inside the container (bin/hubit-node is the wrapper on the host)."""
import argparse
import os
import signal
import sys

from . import config, db, modules, users
from .system import run


def reload_node():
    os.kill(1, signal.SIGHUP)


def port_arg(text):
    port = int(text)
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be 1-65535")
    return port


def need_user(name):
    row = users.get(name)
    if not row:
        sys.exit(f"no such user: {name}")
    return row


def cmd_status(args, cfg):
    print(f"hubit-node {config.VERSION}")
    print(f"host:      {cfg['host'] or '(not set)'}")
    print("protocols:")
    for module in modules.enabled(cfg):
        for label, proto, port in module.ports(cfg):
            print(f"  {label:<10} {proto}/{port}")
    rows = users.all_users()
    counts = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    online = sum(users.sessions(r["username"]) > 0 for r in rows)
    print(f"users:     {len(rows)} {counts or ''}, online now: {online}")


def cmd_online(args, cfg):
    print(f"{'USER':<20}{'PROTOCOL':<10}{'CLIENT IP':<40}CONNECTED")
    total = 0
    for module in modules.enabled(cfg):
        for c in module.online(cfg):
            minutes, seconds = divmod(c["seconds"], 60)
            print(f"{c['user']:<20}{module.NAME:<10}{c['ip']:<40}{minutes}m {seconds}s")
            total += 1
    print(f"{total} connection(s)")


def cmd_host(args, cfg):
    cfg["host"] = args.value
    config.save(cfg)
    print(f"host = {args.value}")


def cmd_ssh_port(args, cfg):
    if args.port == cfg["api"]["port"]:
        sys.exit("that port is used by the API")
    cfg["ssh"]["port"] = args.port
    config.save(cfg)
    reload_node()
    print(f"ssh port = {args.port} (open it in the firewall: ufw allow {args.port}/tcp)")


def cmd_user_add(args, cfg):
    password = args.password or users.new_password()
    try:
        users.add(args.username, password, args.group, cfg)
    except ValueError as e:
        sys.exit(str(e))
    print(f"user {args.username} created, group {args.group}\npassword: {password}")


def cmd_user_list(args, cfg):
    print(f"{'USER':<20}{'GROUP':<14}{'STATUS':<10}{'ONLINE':<7}CREATED")
    for r in users.all_users():
        print(f"{r['username']:<20}{r['grp']:<14}{r['status']:<10}{users.sessions(r['username']):<7}{r['created_at']}")


def cmd_user_show(args, cfg):
    r = need_user(args.username)
    for key in ("username", "grp", "status", "expire_at", "data_limit", "devices", "used_bytes", "created_at"):
        print(f"{key:<12}{r[key]}")
    print(f"{'online':<12}{users.sessions(args.username)}")


def cmd_user_del(args, cfg):
    need_user(args.username)
    users.remove(args.username)
    print("deleted")


def cmd_user_passwd(args, cfg):
    need_user(args.username)
    password = args.password or users.new_password()
    users.set_password(args.username, password, cfg)
    print(f"password: {password}")


def cmd_user_state(status):
    def run_state(args, cfg):
        need_user(args.username)
        users.set_status(args.username, status, cfg)
        print(status)
    return run_state


def cmd_user_kick(args, cfg):
    need_user(args.username)
    users.kick(args.username)
    print("kicked")


def build_parser():
    parser = argparse.ArgumentParser(prog="hubit-node")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="versions, ports, users").set_defaults(fn=cmd_status)
    sub.add_parser("online", help="who is connected now").set_defaults(fn=cmd_online)
    p = sub.add_parser("host", help="domain or IP shown to customers")
    p.add_argument("value")
    p.set_defaults(fn=cmd_host)
    p = sub.add_parser("ssh-port", help="port of the customers' SSH")
    p.add_argument("port", type=port_arg)
    p.set_defaults(fn=cmd_ssh_port)

    user = sub.add_parser("user", help="manage users").add_subparsers(dest="sub", required=True)
    p = user.add_parser("add")
    p.add_argument("username")
    p.add_argument("--password")
    p.add_argument("--group", default="ssh")
    p.set_defaults(fn=cmd_user_add)
    user.add_parser("list").set_defaults(fn=cmd_user_list)
    for name, fn in (("show", cmd_user_show), ("del", cmd_user_del), ("kick", cmd_user_kick),
                     ("disable", cmd_user_state("disabled")), ("enable", cmd_user_state("active"))):
        p = user.add_parser(name)
        p.add_argument("username")
        p.set_defaults(fn=fn)
    p = user.add_parser("passwd")
    p.add_argument("username")
    p.add_argument("--password")
    p.set_defaults(fn=cmd_user_passwd)
    return parser


def main():
    args = build_parser().parse_args()
    db.init()
    args.fn(args, config.load())


if __name__ == "__main__":
    main()
