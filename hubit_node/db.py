"""SQLite in the data volume: the truth about users (the Linux users are rebuilt from it at every start)."""
import sqlite3

from .config import DATA

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    username      TEXT PRIMARY KEY,
    password_hash TEXT NOT NULL,
    grp           TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'active',
    expire_at     TEXT,
    data_limit    INTEGER NOT NULL DEFAULT 0,
    devices       INTEGER NOT NULL DEFAULT 1,
    used_bytes    INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL
)
"""


def connect():
    conn = sqlite3.connect(f"{DATA}/hubit.db", timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn


def init():
    with connect() as conn:
        conn.execute(SCHEMA)
