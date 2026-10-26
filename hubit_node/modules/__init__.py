"""Protocol modules. Each one has NAME, ports(cfg), render(cfg) and command(cfg); the core never knows a protocol by name."""
from . import ssh

ALL = {m.NAME: m for m in (ssh,)}


def enabled(cfg):
    return [ALL[name] for name in cfg["protocols"] if name in ALL]
