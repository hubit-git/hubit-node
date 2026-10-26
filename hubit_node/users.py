"""Users: the row in SQLite is the truth, the Linux user (what sshd and PAM see) is made from it."""
import datetime
import pwd
import re
import secrets

from . import db
from .system import run

NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
MAIN_GROUP = "hubit"


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


def apply(row, cfg):
    """Makes the Linux user match the row: groups of its protocols, password, locked when not active."""
    name = row["username"]
    groups = linux_groups(row["grp"], cfg)
    if not exists(name):
        run("useradd", "-M", "-N", "-g", MAIN_GROUP, "-s", "/usr/sbin/nologin", name)
    run("usermod", "-G", groups, name)
    value = row["password_hash"] if row["status"] == "active" else "!" + row["password_hash"]
    run("usermod", "-p", value, name)


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


def add(username, password, grp, cfg):
    if not NAME_RE.match(username):
        raise ValueError("username: a-z 0-9 _ - only, starts with a letter, at most 32 characters")
    if grp not in cfg["groups"]:
        raise ValueError(f"unknown group: {grp}")
    if get(username):
        raise ValueError("username already exists")
    if exists(username):
        raise ValueError("username belongs to a system user")
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO users (username, password_hash, grp, created_at) VALUES (?, ?, ?, ?)",
            (username, hash_password(password), grp, now()),
        )
    apply(get(username), cfg)


def set_password(username, password, cfg):
    with db.connect() as conn:
        conn.execute("UPDATE users SET password_hash = ? WHERE username = ?", (hash_password(password), username))
    apply(get(username), cfg)


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
    run("userdel", username, check=False)


def sessions(username):
    return int(run("pgrep", "-c", "-u", username, "sshd", check=False).stdout.strip() or 0)


def kick(username):
    """Drops the live connections of the user (their sshd processes run as the user)."""
    run("pkill", "-KILL", "-u", username, check=False)
