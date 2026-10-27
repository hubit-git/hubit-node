"""Users: the row in SQLite is the truth, the Linux user (what sshd and PAM see) is made from it."""
import contextlib
import datetime
import fcntl
import pwd
import re
import secrets

from . import db
from .system import run

NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
MAIN_GROUP = "hubit"


@contextlib.contextmanager
def locked():
    """useradd/usermod/userdel lock /etc/passwd: the API process and the loop must not run them at the same time."""
    with open("/run/hubit-users.lock", "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def new_password():
    return secrets.token_urlsafe(9)


def hash_password(password):
    return run("openssl", "passwd", "-6", "-stdin", input=password)


def linux_groups(grp, cfg):
    return ",".join(f"p-{p}" for p in cfg["groups"][grp])


def exists(username):
    return run("id", "-u", username, check=False).returncode == 0


def is_ours(username):
    """A Linux user of ours: its main group is ours (one left without a row, when the data was lost, is ours too)."""
    try:
        return pwd.getpwnam(username).pw_gid == int(run("getent", "group", MAIN_GROUP).split(":")[2])
    except (KeyError, IndexError, RuntimeError):
        return False


def apply(row, cfg):
    """Makes the Linux user match the row: groups of its protocols, password, locked when not active."""
    name = row["username"]
    groups = linux_groups(row["grp"], cfg)
    value = row["password_hash"] if row["status"] == "active" else "!" + row["password_hash"]
    with locked():
        if not exists(name):
            run("useradd", "-M", "-N", "-g", MAIN_GROUP, "-s", "/usr/sbin/nologin", name)
        run("usermod", "-G", groups, "-p", value, name)


def ensure_groups(cfg):
    names = {MAIN_GROUP}
    for protocols in cfg["groups"].values():
        names.update(f"p-{p}" for p in protocols)
    for name in names:
        run("groupadd", "-f", name)


def sync_all(cfg):
    """At start: rebuilds every Linux user from the database and removes ours that the database does not know."""
    ensure_groups(cfg)
    with db.connect() as conn:
        rows = conn.execute("SELECT * FROM users").fetchall()
    known = {r["username"] for r in rows}
    for row in rows:
        apply(row, cfg)
    gid = int(run("getent", "group", MAIN_GROUP).split(":")[2])
    for entry in pwd.getpwall():
        if entry.pw_gid == gid and entry.pw_name not in known:
            run("userdel", entry.pw_name, check=False)


def get(username):
    with db.connect() as conn:
        return conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()


def all_users():
    with db.connect() as conn:
        return conn.execute("SELECT * FROM users ORDER BY username").fetchall()


def add(username, password, grp, cfg, expire_at=None, data_limit=0, devices=1):
    if not NAME_RE.match(username):
        raise ValueError("username: a-z 0-9 _ - only, starts with a letter, at most 32 characters")
    if grp not in cfg["groups"]:
        raise ValueError(f"unknown group: {grp}")
    if get(username):
        raise ValueError("username already exists")
    if exists(username) and not is_ours(username):
        raise ValueError("username belongs to a system user")
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO users (username, password_hash, grp, expire_at, data_limit, devices, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (username, hash_password(password), grp, expire_at, data_limit, devices, now()),
        )
    apply(get(username), cfg)


def set_password(username, password, cfg):
    with db.connect() as conn:
        conn.execute("UPDATE users SET password_hash = ? WHERE username = ?", (hash_password(password), username))
    apply(get(username), cfg)


def enable(username, cfg):
    """Switches a disabled user on; one whose time or data is over comes back as expired / limited."""
    set_status(username, "active", cfg)
    return refresh(username, cfg)


def as_dict(row):
    return {k: row[k] for k in ("username", "grp", "status", "expire_at", "data_limit", "devices", "used_bytes",
                                "online_at", "created_at")}


def set_status(username, status, cfg):
    with db.connect() as conn:
        conn.execute("UPDATE users SET status = ? WHERE username = ?", (status, username))
    apply(get(username), cfg)
    if status != "active":
        kick(username)


def remove(username):
    kick(username)
    with db.connect() as conn:
        conn.execute("DELETE FROM users WHERE username = ?", (username,))
    with locked():
        run("userdel", username, check=False)


def kick(username):
    """Drops the live connections of the user (their sshd processes run as the user)."""
    run("pkill", "-KILL", "-u", username, check=False)


def wanted_status(row):
    """What the status of a user should be now: a disabled user stays disabled, the rest follows time and data."""
    if row["status"] == "disabled":
        return "disabled"
    if row["expire_at"] and row["expire_at"] <= now():
        return "expired"
    if row["data_limit"] and row["used_bytes"] >= row["data_limit"]:
        return "limited"
    return "active"


def refresh(username, cfg):
    """Sets expired / limited / active again from the row (after a change of the limits, a reset, or new usage)."""
    row = get(username)
    status = wanted_status(row)
    if status != row["status"]:
        set_status(username, status, cfg)
    return status


def update(username, cfg, **fields):
    """Changes some of expire_at, data_limit, devices, grp."""
    allowed = {"expire_at", "data_limit", "devices", "grp"}
    if not fields or set(fields) - allowed:
        raise ValueError("nothing to change")
    if "grp" in fields and fields["grp"] not in cfg["groups"]:
        raise ValueError(f"unknown group: {fields['grp']}")
    with db.connect() as conn:
        conn.execute(
            "UPDATE users SET " + ", ".join(f"{k} = ?" for k in fields) + " WHERE username = ?",
            (*fields.values(), username),
        )
    apply(get(username), cfg)
    refresh(username, cfg)


def reset_usage(username, cfg):
    with db.connect() as conn:
        conn.execute("UPDATE users SET used_bytes = 0 WHERE username = ?", (username,))
    refresh(username, cfg)


def add_usage(deltas):
    """deltas: {username: bytes}; one transaction."""
    deltas = {name: n for name, n in deltas.items() if n > 0}
    if not deltas:
        return
    with db.connect() as conn:
        conn.execute("BEGIN")
        for name, n in deltas.items():
            conn.execute("UPDATE users SET used_bytes = used_bytes + ? WHERE username = ?", (n, name))
        conn.execute("COMMIT")


def mark_online(names):
    if not names:
        return
    with db.connect() as conn:
        conn.execute(
            f"UPDATE users SET online_at = ? WHERE username IN ({','.join('?' * len(names))})",
            (now(), *names),
        )
