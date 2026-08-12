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
        c.execute("""
            CREATE TABLE IF NOT EXISTS oauth_states (
                state      TEXT PRIMARY KEY,
                client_id  TEXT NOT NULL,
                provider   TEXT NOT NULL DEFAULT 'youtube',
                created_at TEXT NOT NULL
            )
        """)
        c.commit()
        _migrate_clients_youtube_columns(c)


def _migrate_clients_youtube_columns(c: sqlite3.Connection) -> None:
    """
    Adiciona as colunas de conexão do YouTube em `clients` se ainda não
    existirem. CREATE TABLE IF NOT EXISTS não adiciona coluna em tabela já
    criada — por isso o ALTER TABLE condicional aqui, checado via
    PRAGMA table_info a cada init_db() (idempotente, seguro rodar sempre).
    """
    existing = {row["name"] for row in c.execute("PRAGMA table_info(clients)")}
    novas_colunas = {
        "youtube_connected": "INTEGER DEFAULT 0",
        "youtube_channel_id": "TEXT DEFAULT ''",
        "youtube_channel_title": "TEXT DEFAULT ''",
        "youtube_connected_at": "TEXT DEFAULT ''",
    }
    for col, tipo in novas_colunas.items():
        if col not in existing:
            c.execute(f"ALTER TABLE clients ADD COLUMN {col} {tipo}")
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


# ── Conexão do canal do YouTube por cliente ────────────────────────────────────

def set_youtube_connection(cid: str, channel_id: str, channel_title: str) -> None:
    with _conn() as c:
        c.execute(
            "UPDATE clients SET youtube_connected = 1, youtube_channel_id = ?, "
            "youtube_channel_title = ?, youtube_connected_at = ? WHERE id = ?",
            (channel_id, channel_title, datetime.now().isoformat(), cid),
        )
        c.commit()


def clear_youtube_connection(cid: str) -> None:
    with _conn() as c:
        c.execute(
            "UPDATE clients SET youtube_connected = 0, youtube_channel_id = '', "
            "youtube_channel_title = '', youtube_connected_at = '' WHERE id = ?",
            (cid,),
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


# ── OAuth states (fluxo de conexão do YouTube por cliente) ────────────────────
# Tabela em vez de variável global de processo — diferente do fluxo do TikTok
# (só o admin conecta, 1 de cada vez), aqui N clientes podem estar no meio do
# fluxo de conexão simultaneamente.

def create_oauth_state(state: str, client_id: str, provider: str = "youtube") -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO oauth_states (state, client_id, provider, created_at) VALUES (?,?,?,?)",
            (state, client_id, provider, datetime.now().isoformat()),
        )
        c.commit()


def get_oauth_state(state: str) -> dict | None:
    with _conn() as c:
        r = c.execute("SELECT * FROM oauth_states WHERE state = ?", (state,)).fetchone()
        return dict(r) if r else None


def delete_oauth_state(state: str) -> None:
    with _conn() as c:
        c.execute("DELETE FROM oauth_states WHERE state = ?", (state,))
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


def count_client_videos_today() -> int:
    """
    Soma quantos vídeos de CLIENTES (não do admin) foram publicados hoje —
    usado só como aviso informativo de cota do YouTube (~6 uploads/dia
    compartilhados entre admin + todos os clientes no mesmo projeto Google
    Cloud). Não inclui os pipelines do admin, que não passam pela tabela
    `runs` — é uma contagem parcial, só do lado do SaaS.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    with _conn() as c:
        rows = c.execute(
            "SELECT video_ids FROM runs WHERE started_at LIKE ?", (f"{today}%",)
        ).fetchall()
    total = 0
    for r in rows:
        try:
            total += len(json.loads(r["video_ids"] or "[]"))
        except json.JSONDecodeError:
            pass
    return total


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
