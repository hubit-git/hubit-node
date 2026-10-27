"""The one config file (/data/config.json) every other part is rendered from."""
import json
import os

VERSION = "0.2.0"
DATA = os.environ.get("HUBIT_DATA", "/data")
PATH = f"{DATA}/config.json"

DEFAULTS = {
    "host": "",
    "api": {"port": 8443, "key": ""},
    "ssh": {"port": 2222},
    "openvpn": {"udp": 1194, "tcp": 1194, "dns": ["1.1.1.1", "8.8.8.8"]},  # a port of 0 = that server is off
    # protocols switched on; a group is a set of protocols a subscription may use
    "protocols": ["ssh", "openvpn"],
}

# these groups always exist; the admin may add more
DEFAULT_GROUPS = {"ssh": ["ssh"], "openvpn": ["openvpn"], "ssh+openvpn": ["ssh", "openvpn"]}


def host_of(cfg, protocol):
    """The address customers use for a protocol: its own host when one is set, else the host of the node."""
    return cfg[protocol].get("host") or cfg["host"]


def load():
    cfg = json.loads(json.dumps(DEFAULTS))
    if os.path.exists(PATH):
        with open(PATH) as f:
            saved = json.load(f)
        for key, value in saved.items():
            if isinstance(value, dict) and isinstance(cfg.get(key), dict):
                cfg[key].update(value)
            else:
                cfg[key] = value
    cfg["groups"] = {**DEFAULT_GROUPS, **cfg.get("groups", {})}
    return cfg


def save(cfg):
    os.makedirs(DATA, exist_ok=True)
    tmp = PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, PATH)
