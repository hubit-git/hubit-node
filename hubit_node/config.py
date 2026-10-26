"""The one config file (/data/config.json) every other part is rendered from."""
import json
import os

VERSION = "0.1.0"
DATA = os.environ.get("HUBIT_DATA", "/data")
PATH = f"{DATA}/config.json"

DEFAULTS = {
    "host": "",
    "api": {"port": 8443, "key": ""},
    "ssh": {"port": 2222},
    # protocols switched on; a group is a set of protocols a subscription may use
    "protocols": ["ssh"],
    "groups": {"ssh": ["ssh"]},
}


def load():
    cfg = json.loads(json.dumps(DEFAULTS))
    if os.path.exists(PATH):
        with open(PATH) as f:
            saved = json.load(f)
        for key, value in saved.items():
            if isinstance(value, dict) and isinstance(cfg.get(key), dict) and key != "groups":
                cfg[key].update(value)
            else:
                cfg[key] = value
    return cfg


def save(cfg):
    os.makedirs(DATA, exist_ok=True)
    tmp = PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, PATH)
