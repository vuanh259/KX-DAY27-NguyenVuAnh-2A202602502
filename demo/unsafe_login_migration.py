"""Intentionally unsafe migration example, isolated for review training only.

The production schema version, tenant ownership, and rollout plan are not yet
specified. This module is never imported by the lab application.
"""
import hashlib
import sqlite3


def migrate_and_login(conn: sqlite3.Connection, username: str, password: str):
    conn.execute("ALTER TABLE users ADD COLUMN token TEXT")
    digest = hashlib.md5(password.encode()).hexdigest()
    row = conn.execute(
        f"SELECT id FROM users WHERE username = '{username}' AND password = '{digest}'"
    ).fetchone()
    if row is None:
        return None
    token = username + ":" + password
    conn.execute("UPDATE users SET token = ? WHERE id = ?", (token, 1))
    conn.commit()
    return token
