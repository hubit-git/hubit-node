"""`hubit-node ...`: runs inside the container (bin/hubit-node is the wrapper on the host)."""
import argparse
import datetime
import os
import signal
import sys

from . import api, config, db, modules, pki, users
from .system import run


def reload_node():
    os.kill(1, signal.SIGHUP)


def port_arg(text):
    port = int(text)
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be 1-65535")
    return port


GB = 1024 ** 3


def human(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.2f} {unit}"
        n /= 1024


def limits_from(args):
    """The limits given on the command line, as the user columns: only what was given."""
    fields = {}
    if args.days is not None:
        end = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=args.days)
        fields["expire_at"] = end.strftime("%Y-%m-%d %H:%M:%S")
    if args.expire is not None:
        fields["expire_at"] = None if args.expire == "none" else args.expire
    if args.data is not None:
        fields["data_limit"] = int(args.data * GB)
    if args.devices is not None:
        fields["devices"] = args.devices
    return fields


def connections(cfg):
    """{username: number of live connections over every protocol}"""
    count = {}
    for module in modules.enabled(cfg):
        for c in module.online(cfg):
            count[c["user"]] = count.get(c["user"], 0) + 1
    return count


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
    if "openvpn" in cfg["protocols"]:
        print(f"openvpn certificate: {('valid until ' + pki.expires()) if pki.exists() else 'none yet (hubit-node pki create)'}")
    rows = users.all_users()
    counts = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    print(f"users:     {len(rows)} {counts or ''}, online now: {len(connections(cfg))}")


def cmd_online(args, cfg):
    print(f"{'USER':<20}{'PROTOCOL':<10}{'CLIENT IP':<40}CONNECTED")
    total = 0
    for module in modules.enabled(cfg):
        for c in module.online(cfg):
            minutes, seconds = divmod(c["seconds"], 60)
            print(f"{c['user']:<20}{module.NAME:<10}{c['ip']:<40}{minutes}m {seconds}s")
            total += 1
    print(f"{total} connection(s)")


def cmd_openvpn(args, cfg):
    changed = False
    for proto in ("udp", "tcp"):
        value = getattr(args, proto)
        if value is not None:
            cfg["openvpn"][proto] = 0 if value == "off" else port_arg(value)
            changed = True
    if changed:
        if not any(cfg["openvpn"].get(p) for p in ("udp", "tcp")):
            sys.exit("keep at least one of udp and tcp on (or disable the protocol: hubit-node protocol disable openvpn)")
        config.save(cfg)
        reload_node()
    for proto in ("udp", "tcp"):
        print(f"{proto}: {cfg['openvpn'].get(proto) or 'off'}")
    if changed:
        print("open the ports in the firewall (ufw allow <port>/<udp|tcp>); customers need a new profile only if a port changed")


def cmd_ovpn(args, cfg):
    from .modules import openvpn
    try:
        sys.stdout.write(openvpn.profile(cfg))
    except ValueError as e:
        sys.exit(str(e))


def cmd_protocol(args, cfg):
    if args.action == "list":
        for name in modules.ALL:
            print(f"{name:<10}{'on' if name in cfg['protocols'] else 'off'}")
        return
    if args.name not in modules.ALL:
        sys.exit(f"unknown protocol: {args.name} (known: {', '.join(modules.ALL)})")
    on = args.name in cfg["protocols"]
    if args.action == "enable" and not on:
        cfg["protocols"].append(args.name)
    elif args.action == "disable" and on:
        cfg["protocols"].remove(args.name)
    config.save(cfg)
    reload_node()
    print(f"{args.name}: {args.action}d")


def cmd_pki(args, cfg):
    try:
        if args.action == "create":
            if pki.exists():
                sys.exit("a certificate bundle already exists; export it, or remove /data/pki on purpose first")
            pki.create()
        elif args.action == "import":
            pki.import_bundle(sys.stdin.buffer.read())
        elif args.action == "export":
            if not pki.exists():
                sys.exit("no bundle yet")
            sys.stdout.buffer.write(pki.export())
            return
        elif not pki.exists():
            sys.exit("no certificate bundle yet")
    except (ValueError, RuntimeError, OSError) as e:
        sys.exit(str(e))
    if args.action in ("create", "import"):
        reload_node()
    print(f"server certificate valid until {pki.expires()}", file=sys.stderr if args.action == "export" else sys.stdout)


def cmd_api(args, cfg):
    if args.port is not None:
        used = {cfg["ssh"]["port"], *(cfg["openvpn"].get(p) for p in ("udp", "tcp"))}
        if args.port in used:
            sys.exit("that port is used by SSH or OpenVPN")
        cfg["api"]["port"] = args.port
    if args.new_key:
        import secrets
        cfg["api"]["key"] = secrets.token_urlsafe(32)
    if args.port is not None or args.new_key:
        config.save(cfg)
        reload_node()
    print(f"port:        {cfg['api']['port']}")
    print(f"key:         {cfg['api']['key']}")
    print(f"fingerprint: {api.fingerprint()}")
    if args.new_key:
        print("the key changed: give the new one to Hubit")


def cmd_doctor(args, cfg):
    from . import doctor
    sys.exit(doctor.main())


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
    fields = {"devices": 1, **limits_from(args)}
    try:
        users.add(args.username, password, args.group, cfg, **fields)
    except ValueError as e:
        sys.exit(str(e))
    print(f"user {args.username} created, group {args.group}\npassword: {password}")


def cmd_user_list(args, cfg):
    print(f"{'USER':<16}{'GROUP':<12}{'STATUS':<10}{'ON':<4}{'USED':<12}{'LIMIT':<12}{'DEV':<5}EXPIRES (UTC)")
    live = connections(cfg)
    for r in users.all_users():
        limit = human(r["data_limit"]) if r["data_limit"] else "unlimited"
        devices = r["devices"] or "any"
        print(f"{r['username']:<16}{r['grp']:<12}{r['status']:<10}{live.get(r['username'], 0):<4}"
              f"{human(r['used_bytes']):<12}{limit:<12}{devices:<5}{r['expire_at'] or '-'}")


def cmd_user_show(args, cfg):
    r = need_user(args.username)
    for key in ("username", "grp", "status", "expire_at", "devices", "online_at", "created_at"):
        print(f"{key:<12}{r[key]}")
    print(f"{'data_limit':<12}{human(r['data_limit']) if r['data_limit'] else 'unlimited'}")
    print(f"{'used':<12}{human(r['used_bytes'])} ({r['used_bytes']} bytes)")
    print(f"{'connections':<12}{connections(cfg).get(args.username, 0)}")


def cmd_user_del(args, cfg):
    need_user(args.username)
    users.remove(args.username)
    print("deleted")


def cmd_user_passwd(args, cfg):
    need_user(args.username)
    password = args.password or users.new_password()
    users.set_password(args.username, password, cfg)
    print(f"password: {password}")


def cmd_user_disable(args, cfg):
    need_user(args.username)
    users.set_status(args.username, "disabled", cfg)
    print("disabled")


def cmd_user_enable(args, cfg):
    need_user(args.username)
    print(users.enable(args.username, cfg))


def cmd_user_set(args, cfg):
    need_user(args.username)
    fields = limits_from(args)
    if args.group is not None:
        fields["grp"] = args.group
    try:
        users.update(args.username, cfg, **fields)
    except ValueError as e:
        sys.exit(str(e))
    print(f"status: {users.get(args.username)['status']}")


def cmd_user_reset_usage(args, cfg):
    need_user(args.username)
    users.reset_usage(args.username, cfg)
    print(f"usage reset, status: {users.get(args.username)['status']}")


def cmd_user_kick(args, cfg):
    need_user(args.username)
    users.kick(args.username)
    print("kicked")


def limit_options(p):
    p.add_argument("--days", type=float, help="expires in N days from now")
    p.add_argument("--expire", help="expiry 'YYYY-MM-DD HH:MM:SS' in UTC, or none")
    p.add_argument("--data", type=float, help="data limit in GB (0 = unlimited)")
    p.add_argument("--devices", type=int, help="devices at the same time, by client IP (0 = unlimited)")


def build_parser():
    parser = argparse.ArgumentParser(prog="hubit-node")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="versions, ports, users").set_defaults(fn=cmd_status)
    sub.add_parser("doctor", help="check the server (ports, firewall, forwarding, API)").set_defaults(fn=cmd_doctor)
    sub.add_parser("online", help="who is connected now").set_defaults(fn=cmd_online)
    p = sub.add_parser("host", help="domain or IP shown to customers")
    p.add_argument("value")
    p.set_defaults(fn=cmd_host)
    p = sub.add_parser("openvpn", help="show or change the OpenVPN ports")
    p.add_argument("--udp", help="port or off")
    p.add_argument("--tcp", help="port or off")
    p.set_defaults(fn=cmd_openvpn)
    sub.add_parser("ovpn", help="print the client profile (.ovpn)").set_defaults(fn=cmd_ovpn)
    p = sub.add_parser("protocol", help="list, enable or disable a protocol")
    p.add_argument("action", choices=["list", "enable", "disable"])
    p.add_argument("name", nargs="?")
    p.set_defaults(fn=cmd_protocol)
    p = sub.add_parser("pki", help="the OpenVPN certificate bundle")
    p.add_argument("action", choices=["create", "import", "export", "show"])
    p.set_defaults(fn=cmd_pki)
    p = sub.add_parser("api", help="show or change the API (port, key, certificate fingerprint)")
    p.add_argument("--port", type=port_arg)
    p.add_argument("--new-key", action="store_true", help="make a new API key")
    p.set_defaults(fn=cmd_api)
    p = sub.add_parser("ssh-port", help="port of the customers' SSH")
    p.add_argument("port", type=port_arg)
    p.set_defaults(fn=cmd_ssh_port)

    user = sub.add_parser("user", help="manage users").add_subparsers(dest="sub", required=True)
    p = user.add_parser("add")
    p.add_argument("username")
    p.add_argument("--password")
    p.add_argument("--group", default="ssh")
    limit_options(p)
    p.set_defaults(fn=cmd_user_add)
    p = user.add_parser("set", help="change the limits or the group")
    p.add_argument("username")
    p.add_argument("--group")
    limit_options(p)
    p.set_defaults(fn=cmd_user_set)
    user.add_parser("list").set_defaults(fn=cmd_user_list)
    for name, fn in (("show", cmd_user_show), ("del", cmd_user_del), ("kick", cmd_user_kick),
                     ("reset-usage", cmd_user_reset_usage),
                     ("disable", cmd_user_disable), ("enable", cmd_user_enable)):
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
