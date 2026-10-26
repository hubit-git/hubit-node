"""Protocol modules. A module has NAME, ports(cfg), render(cfg), commands(cfg) {process name: argv}, usage(cfg),
online(cfg), drop(conn), stop(cfg) and, if it needs one, sync(cfg) once per loop; the core never knows a protocol by name."""
from . import openvpn, ssh

ALL = {m.NAME: m for m in (ssh, openvpn)}


def enabled(cfg):
    return [ALL[name] for name in cfg["protocols"] if name in ALL]
