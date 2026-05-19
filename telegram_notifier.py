"""
Módulo leve para enviar notificações ao Telegram.
Importe e chame notify() no final de qualquer pipeline.
Não depende do python-telegram-bot — usa requests diretamente.
"""
import os
import requests
from dotenv import load_dotenv

load_dotenv()

_TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")


def notify(message: str) -> None:
    """Envia mensagem ao dono do bot. Falha silenciosamente se não configurado."""
    if not _TOKEN or not _CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{_TOKEN}/sendMessage",
            json={"chat_id": _CHAT_ID, "text": message, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception:
        pass
