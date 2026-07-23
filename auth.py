"""
Gerenciamento de sessões — tokens em memória com expiração.
"""
import hashlib
import os
import secrets
from datetime import datetime, timedelta

SESSION_HOURS = 8
_sessions: dict[str, dict] = {}


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def verify_admin(email: str, password: str) -> bool:
    ok_email = os.getenv("ADMIN_EMAIL", "admin@admin.com").strip()
    ok_pass  = os.getenv("ADMIN_PASSWORD", "").strip()
    return email.strip() == ok_email and password.strip() == ok_pass


def create_session(user_id: str, name: str, role: str) -> str:
    token = secrets.token_urlsafe(32)
    _sessions[token] = {
        "user_id": user_id,
        "name":    name,
        "role":    role,
        "expires": (datetime.now() + timedelta(hours=SESSION_HOURS)).isoformat(),
    }
    return token


def get_session(token: str | None) -> dict | None:
    if not token:
        return None
    s = _sessions.get(token)
    if not s:
        return None
    if datetime.fromisoformat(s["expires"]) < datetime.now():
        _sessions.pop(token, None)
        return None
    return s


def delete_session(token: str | None) -> None:
    if token:
        _sessions.pop(token, None)
