"""The 10 second loop: counts usage, notes who is online, drops the extra devices, and locks users whose time or
data is over (or opens them again when the limits were raised). It runs on the node, so it works when Hubit is away."""
from . import modules, users
from .system import is_local


def device_of(module, conn):
    """(key, newest wins) of a connection. A device = a client IP. Behind a reverse tunnel every client is 127.0.0.1, so
    there each connection is a device. Normally the oldest devices are kept; the newest wins for an SSH connection from
    a local address (SSH notices a dead client only after a minute or two, which must not lock the customer out)."""
    if not is_local(conn["ip"]):
        return conn["ip"], False
    return f"{conn['ip']}:{conn.get('addr') or conn.get('pid')}", module.NAME == "ssh"


def drop_extra_devices(row, connections):
    """The `devices` best-ranked devices keep their connections, the others are closed."""
    ranks = {}
    for module, conn in connections:
        key, newest = device_of(module, conn)
        rank = -conn["seconds"] if newest else conn["seconds"]
        ranks[key] = max(ranks.get(key, rank), rank)
    keep = set(sorted(ranks, key=ranks.get, reverse=True)[: row["devices"]])
    for module, conn in connections:
        if device_of(module, conn)[0] not in keep:
            module.drop(conn)


def tick(cfg):
    deltas = {}
    online = {}
    for module in modules.enabled(cfg):
        try:
            if hasattr(module, "sync"):
                module.sync(cfg)
            for name, n in module.usage(cfg).items():
                deltas[name] = deltas.get(name, 0) + n
            for conn in module.online(cfg):
                online.setdefault(conn["user"], []).append((module, conn))
        except Exception as e:  # what the other modules found is still used
            print(f"[hubit-node] {module.NAME} failed in the loop: {e}", flush=True)
    users.add_usage(deltas)
    users.mark_online(list(online))
    for row in users.all_users():
        if row["devices"] > 0 and row["username"] in online:
            drop_extra_devices(row, online[row["username"]])
        users.refresh(row["username"], cfg)
