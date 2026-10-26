"""The HTTPS JSON API Hubit talks to (it mirrors the panel driver). One API key in the header X-API-Key; the certificate
is self-signed and Hubit checks its fingerprint (`hubit-node api` prints it). It runs as its own process under the supervisor.
It only reads usage; counting and limits belong to the loop in the supervisor."""
import hmac
import json
import os
import re
import signal
import ssl
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import config, modules, pki, users
from .system import run

TLS_DIR = f"{config.DATA}/api"
MAX_BODY = 2 * 1024 * 1024
TIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")


class ApiError(Exception):
    def __init__(self, status, code):
        self.status, self.code = status, code


def ensure_cert():
    if os.path.exists(f"{TLS_DIR}/tls.crt"):
        return
    os.makedirs(TLS_DIR, exist_ok=True)
    run("openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes", "-days", "3650",
        "-keyout", f"{TLS_DIR}/tls.key", "-out", f"{TLS_DIR}/tls.crt", "-subj", "/CN=hubit-node")
    os.chmod(f"{TLS_DIR}/tls.key", 0o600)


def fingerprint():
    """What Hubit stores to know this node: the sha256 of its certificate, as lowercase hex. The panel checks it in the TLS
    handshake, before the API key is sent, so a self-signed certificate is safe to use."""
    text = run("openssl", "x509", "-in", f"{TLS_DIR}/tls.crt", "-noout", "-fingerprint", "-sha256")
    return text.split("=", 1)[1].replace(":", "").lower()


def connections(cfg):
    """[{user, ip, seconds, protocol}] over every protocol."""
    found = []
    for module in modules.enabled(cfg):
        found += [{**c, "protocol": module.NAME} for c in module.online(cfg)]
    return found


def need_user(name):
    row = users.get(name)
    if not row:
        raise ApiError(404, "user_not_found")
    return row


def fields_from(body, cfg):
    """The limits in a request body, checked, as user columns: only what was given."""
    fields = {}
    if "expire_at" in body:
        value = body["expire_at"]
        if value is not None and not (isinstance(value, str) and TIME_RE.match(value)):
            raise ValueError("expire_at: 'YYYY-MM-DD HH:MM:SS' in UTC, or null")
        fields["expire_at"] = value
    for key in ("data_limit", "devices"):
        if key in body:
            if not isinstance(body[key], int) or isinstance(body[key], bool) or body[key] < 0:
                raise ValueError(f"{key}: a whole number, 0 or more")
            fields[key] = body[key]
    if "group" in body:
        fields["grp"] = body["group"]
    return fields


# --- routes: (cfg, match, body, query) -> (status, payload)

def info(cfg, m, body, query):
    live = connections(cfg)
    return 200, {"version": config.VERSION, "host": cfg["host"], "protocols": cfg["protocols"],
                 "total_users": len(users.all_users()), "online_users": len({c["user"] for c in live})}


def groups(cfg, m, body, query):
    return 200, [{"id": name, "name": name, "protocols": protocols} for name, protocols in cfg["groups"].items()]


def user_list(cfg, m, body, query):
    offset = int(query.get("offset", ["0"])[0])
    limit = min(int(query.get("limit", ["100"])[0]), 5000)
    live = {c["user"] for c in connections(cfg)}
    rows = users.all_users()[offset:offset + limit]
    return 200, [{**users.as_dict(r), "online": r["username"] in live} for r in rows]


def user_create(cfg, m, body, query):
    password = body.get("password")
    if not isinstance(password, str) or len(password) < 8:
        raise ValueError("password: at least 8 characters")
    try:
        users.add(body.get("username", ""), password, body.get("group", "ssh"), cfg,
                  **{"expire_at": None, "data_limit": 0, "devices": 1,
                     **{k: v for k, v in fields_from(body, cfg).items() if k != "grp"}})
    except ValueError as e:
        if str(e) == "username already exists":
            raise ApiError(409, "user_exists")
        raise
    return 201, users.as_dict(users.get(body["username"]))


def user_show(cfg, m, body, query):
    row = need_user(m["user"])
    live = {c["user"] for c in connections(cfg)}
    return 200, {**users.as_dict(row), "online": row["username"] in live}


def user_update(cfg, m, body, query):
    need_user(m["user"])
    fields = fields_from(body, cfg)
    if "password" in body:
        if not isinstance(body["password"], str) or len(body["password"]) < 8:
            raise ValueError("password: at least 8 characters")
        users.set_password(m["user"], body["password"], cfg)
    if fields:
        users.update(m["user"], cfg, **fields)
    return 200, users.as_dict(users.get(m["user"]))


def user_action(cfg, m, body, query):
    need_user(m["user"])
    action = m["action"]
    if action == "disable":
        users.set_status(m["user"], "disabled", cfg)
    elif action == "enable":
        users.enable(m["user"], cfg)
    elif action == "reset-usage":
        users.reset_usage(m["user"], cfg)
    else:
        users.kick(m["user"])
        for c in connections(cfg):
            if c["user"] == m["user"]:
                modules.ALL[c["protocol"]].drop(c)
    return 200, users.as_dict(users.get(m["user"]))


def user_delete(cfg, m, body, query):
    if users.get(m["user"]):
        users.remove(m["user"])
    return 200, {"deleted": True}  # a user that is already gone counts as deleted


def user_connection(cfg, m, body, query):
    row = need_user(m["user"])
    items = [modules.ALL[p].connection(cfg) for p in cfg["groups"][row["grp"]] if p in cfg["protocols"]]
    return 200, {"username": row["username"], "items": items}


def online(cfg, m, body, query):
    return 200, connections(cfg)


def profile(cfg, m, body, query):
    module = modules.ALL.get(m["protocol"])
    if not module or not hasattr(module, "profile"):
        raise ApiError(404, "no_profile")
    return 200, module.profile(cfg)


def pki_show(cfg, m, body, query):
    return 200, {"present": pki.exists(), "expires": pki.expires()}


def pki_put(cfg, m, body, query):
    missing = [name for name in pki.FILES if not isinstance(body.get(name), str)]
    if missing:
        raise ValueError("missing: " + ", ".join(missing))
    pki.install({name: body[name].encode() for name in pki.FILES})
    os.kill(1, signal.SIGHUP)  # the supervisor starts OpenVPN now
    return 200, {"present": True, "expires": pki.expires()}


ROUTES = [
    ("GET", r"/info", info),
    ("GET", r"/groups", groups),
    ("GET", r"/users", user_list),
    ("POST", r"/users", user_create),
    ("GET", r"/users/(?P<user>[a-z][a-z0-9_-]*)", user_show),
    ("PATCH", r"/users/(?P<user>[a-z][a-z0-9_-]*)", user_update),
    ("DELETE", r"/users/(?P<user>[a-z][a-z0-9_-]*)", user_delete),
    ("POST", r"/users/(?P<user>[a-z][a-z0-9_-]*)/(?P<action>disable|enable|reset-usage|kick)", user_action),
    ("GET", r"/users/(?P<user>[a-z][a-z0-9_-]*)/connection", user_connection),
    ("GET", r"/online", online),
    ("GET", r"/profile/(?P<protocol>[a-z]+)", profile),
    ("GET", r"/pki", pki_show),
    ("PUT", r"/pki", pki_put),
]


class Handler(BaseHTTPRequestHandler):
    server_version = "hubit-node"
    timeout = 20  # a client that is slow (also in the TLS handshake) is dropped

    def log_message(self, fmt, *args):
        pass

    def reply(self, status, payload):
        if isinstance(payload, str):
            data, kind = payload.encode(), "text/plain; charset=utf-8"
        else:
            data, kind = json.dumps(payload).encode(), "application/json"
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def handle_any(self):
        try:
            cfg = config.load()
            key = cfg["api"]["key"]
            if not key or not hmac.compare_digest(self.headers.get("X-API-Key", "").encode(), key.encode()):
                raise ApiError(401, "unauthorized")
            url = urlparse(self.path)
            allowed = False
            for method, pattern, fn in ROUTES:
                match = re.fullmatch(pattern, url.path)
                if not match:
                    continue
                allowed = True
                if method != self.command:
                    continue
                size = int(self.headers.get("Content-Length") or 0)
                if size > MAX_BODY:
                    raise ApiError(413, "too_large")
                data = self.rfile.read(size) if size else b""
                body = json.loads(data) if data else {}
                if not isinstance(body, dict):
                    raise ValueError("the body must be a JSON object")
                status, payload = fn(cfg, match.groupdict(), body, parse_qs(url.query))
                return self.reply(status, payload)
            raise ApiError(405 if allowed else 404, "method_not_allowed" if allowed else "not_found")
        except ApiError as e:
            self.reply(e.status, {"error": e.code})
        except (ValueError, KeyError, json.JSONDecodeError) as e:
            self.reply(422, {"error": "invalid", "message": str(e)})
        except Exception as e:
            print(f"[hubit-node] api error on {self.command} {self.path}: {e}", file=sys.stderr, flush=True)
            self.reply(500, {"error": "server_error"})

    do_GET = do_POST = do_PATCH = do_PUT = do_DELETE = handle_any


def main():
    cfg = config.load()
    ensure_cert()
    server = ThreadingHTTPServer(("0.0.0.0", cfg["api"]["port"]), Handler)
    server.daemon_threads = True
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(f"{TLS_DIR}/tls.crt", f"{TLS_DIR}/tls.key")
    # the handshake happens in the thread of the connection, so a slow client cannot block the others
    server.socket = context.wrap_socket(server.socket, server_side=True, do_handshake_on_connect=False)
    print(f"[hubit-node] api listening on {cfg['api']['port']}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
