"""The 10 second loop: counts usage, notes who is online, drops the extra devices, and locks users whose time or
data is over (or opens them again when the limits were raised). It runs on the node, so it works when Hubit is away."""
from . import modules, users


def drop_extra_devices(row, connections):
    """A device = a client IP. The oldest `devices` IPs keep their connections, the newer ones are closed."""
    ips = {}
    for module, conn in connections:
        ips[conn["ip"]] = max(ips.get(conn["ip"], 0), conn["seconds"])
    keep = set(sorted(ips, key=ips.get, reverse=True)[: row["devices"]])
    for module, conn in connections:
        if conn["ip"] not in keep:
            module.drop(conn)


def tick(cfg):
    deltas = {}
    online = {}
    for module in modules.enabled(cfg):
        if hasattr(module, "sync"):
            module.sync(cfg)
        for name, n in module.usage(cfg).items():
            deltas[name] = deltas.get(name, 0) + n
        for conn in module.online(cfg):
            online.setdefault(conn["user"], []).append((module, conn))
    users.add_usage(deltas)
    users.mark_online(list(online))
    for row in users.all_users():
        if row["devices"] > 0 and row["username"] in online:
            drop_extra_devices(row, online[row["username"]])
        users.refresh(row["username"], cfg)
