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
        c.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                token   TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                name    TEXT NOT NULL,
                role    TEXT NOT NULL,
                expires TEXT NOT NULL
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS runs (
                id          TEXT PRIMARY KEY,
                client_id   TEXT NOT NULL,
                started_at  TEXT NOT NULL,
                finished_at TEXT,
                status      TEXT NOT NULL,
                video_ids   TEXT DEFAULT '[]',
                message     TEXT DEFAULT ''
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


# ── Sessões ──────────────────────────────────────────────────────────────────

def create_session_row(token: str, user_id: str, name: str, role: str, expires: str) -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO sessions (token, user_id, name, role, expires) VALUES (?,?,?,?,?)",
            (token, user_id, name, role, expires),
        )
        c.commit()


def get_session_row(token: str) -> dict | None:
    with _conn() as c:
        r = c.execute("SELECT * FROM sessions WHERE token = ?", (token,)).fetchone()
        return dict(r) if r else None


def delete_session_row(token: str) -> None:
    with _conn() as c:
        c.execute("DELETE FROM sessions WHERE token = ?", (token,))
        c.commit()


# ── Histórico de execuções (clientes) ─────────────────────────────────────────

def create_run(client_id: str) -> str:
    rid = secrets.token_hex(8)
    with _conn() as c:
        c.execute(
            "INSERT INTO runs (id, client_id, started_at, status) VALUES (?,?,?,?)",
            (rid, client_id, datetime.now().isoformat(), "running"),
        )
        c.commit()
    return rid


def update_run(rid: str, status: str, video_ids: list[str] | None = None, message: str = "") -> None:
    with _conn() as c:
        c.execute(
            "UPDATE runs SET status=?, finished_at=?, video_ids=?, message=? WHERE id=?",
            (status, datetime.now().isoformat(), json.dumps(video_ids or []), message, rid),
        )
        c.commit()


def list_runs(client_id: str, limit: int = 10) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM runs WHERE client_id = ? ORDER BY started_at DESC LIMIT ?",
            (client_id, limit),
        ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        try:
            d["video_ids"] = json.loads(d.get("video_ids") or "[]")
        except json.JSONDecodeError:
            d["video_ids"] = []
        result.append(d)
    return result
