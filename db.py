"""
Banco SQLite — clientes do SaaS.
"""
import hashlib
import json
import os
import secrets
import sqlite3
from datetime import datetime

_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "users.db")


def _conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(_DB), exist_ok=True)
    c = sqlite3.connect(_DB)
    c.row_factory = sqlite3.Row
    return c


def init_db() -> None:
    with _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS clients (
                id            TEXT PRIMARY KEY,
                name          TEXT NOT NULL,
                email         TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                nicho         TEXT DEFAULT '',
                nicho_config  TEXT DEFAULT '{}',
                active        INTEGER DEFAULT 1,
                created_at    TEXT NOT NULL
            )
        """)
        c.commit()


def _hash(pw: str) -> str:
    return hashlib.sha256(pw.encode()).hexdigest()


def create_client(name: str, email: str, password: str) -> dict:
    cid = secrets.token_hex(8)
    with _conn() as c:
        c.execute(
            "INSERT INTO clients (id, name, email, password_hash, created_at) VALUES (?,?,?,?,?)",
            (cid, name.strip(), email.strip().lower(), _hash(password), datetime.now().isoformat()),
        )
        c.commit()
    return {"id": cid, "name": name, "email": email}


def get_by_email(email: str) -> dict | None:
    with _conn() as c:
        r = c.execute("SELECT * FROM clients WHERE email = ?", (email.strip().lower(),)).fetchone()
        return dict(r) if r else None


def get_by_id(cid: str) -> dict | None:
    with _conn() as c:
        r = c.execute("SELECT * FROM clients WHERE id = ?", (cid,)).fetchone()
        return dict(r) if r else None


def verify_client(email: str, password: str) -> dict | None:
    c = get_by_email(email)
    if not c or c["password_hash"] != _hash(password) or not c["active"]:
        return None
    return c


def list_clients() -> list[dict]:
    with _conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM clients ORDER BY created_at DESC")]


def toggle_active(cid: str) -> None:
    with _conn() as c:
        c.execute("UPDATE clients SET active = NOT active WHERE id = ?", (cid,))
        c.commit()


def update_nicho(cid: str, nicho: str, nicho_config: str = "{}") -> None:
    with _conn() as c:
        c.execute(
            "UPDATE clients SET nicho = ?, nicho_config = ? WHERE id = ?",
            (nicho, nicho_config, cid),
        )
        c.commit()
