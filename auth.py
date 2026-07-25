"""
Gerenciamento de sessões (persistidas em SQLite) e proteção contra força bruta no login.
"""
import os
import secrets
from datetime import datetime, timedelta

import db

SESSION_HOURS = 8

MAX_LOGIN_ATTEMPTS = 5
LOCKOUT_MINUTES = 15
_failed_attempts: dict[str, list[datetime]] = {}


def verify_admin(email: str, password: str) -> bool:
    ok_email = os.getenv("ADMIN_EMAIL", "admin@admin.com").strip()
    ok_pass  = os.getenv("ADMIN_PASSWORD", "").strip()
    return email.strip() == ok_email and password.strip() == ok_pass


def create_session(user_id: str, name: str, role: str) -> str:
    token = secrets.token_urlsafe(32)
    expires = (datetime.now() + timedelta(hours=SESSION_HOURS)).isoformat()
    db.create_session_row(token, user_id, name, role, expires)
    return token


def get_session(token: str | None) -> dict | None:
    if not token:
        return None
    s = db.get_session_row(token)
    if not s:
        return None
    if datetime.fromisoformat(s["expires"]) < datetime.now():
        db.delete_session_row(token)
        return None
    return s


def delete_session(token: str | None) -> None:
    if token:
        db.delete_session_row(token)


# ── Rate limiting de login ────────────────────────────────────────────────────

def is_locked_out(key: str) -> bool:
    key = key.strip().lower()
    cutoff = datetime.now() - timedelta(minutes=LOCKOUT_MINUTES)
    attempts = [t for t in _failed_attempts.get(key, []) if t > cutoff]
    _failed_attempts[key] = attempts
    return len(attempts) >= MAX_LOGIN_ATTEMPTS


def record_failed_attempt(key: str) -> None:
    key = key.strip().lower()
    _failed_attempts.setdefault(key, []).append(datetime.now())


def clear_attempts(key: str) -> None:
    _failed_attempts.pop(key.strip().lower(), None)
