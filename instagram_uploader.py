"""
Upload para Instagram via instagrapi.

Suporta:
  - Reels (vídeos verticais — reaproveita os Shorts)
  - Posts de foto (thumbnails ou imagens)

Autenticação via .env:
  INSTAGRAM_USERNAME=seu_usuario
  INSTAGRAM_PASSWORD=sua_senha

A sessão é salva em credentials/ig_session.json para evitar login repetido.
"""
from __future__ import annotations

import json
import os
import time

from dotenv import load_dotenv

load_dotenv()

_USERNAME = os.environ.get("INSTAGRAM_USERNAME", "")
_PASSWORD = os.environ.get("INSTAGRAM_PASSWORD", "")
SESSION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "credentials", "ig_session.json")

# Flag global — desativa Instagram se credenciais não existirem
INSTAGRAM_ENABLED = bool(_USERNAME and _PASSWORD)


def _get_client():
    """Retorna Client do instagrapi autenticado, com sessão persistida."""
    from instagrapi import Client

    cl = Client()

    # Configurar para parecer dispositivo real (reduz risco de bloqueio)
    cl.delay_range = [1, 3]

    # Tentar reusar sessão salva
    if os.path.exists(SESSION_FILE):
        try:
            cl.load_settings(SESSION_FILE)
            cl.login(_USERNAME, _PASSWORD)
            # Testar se a sessão é válida
            cl.get_timeline_feed()
            print("  Instagram: sessão reutilizada.")
            return cl
        except Exception:
            print("  Instagram: sessão expirada, refazendo login...")

    # Login fresco
    cl.login(_USERNAME, _PASSWORD)
    cl.dump_settings(SESSION_FILE)
    print("  Instagram: login realizado e sessão salva.")
    return cl


def upload_reel(
    video_path: str,
    caption: str,
    thumbnail_path: str | None = None,
) -> str | None:
    """
    Faz upload de um Reel (vídeo vertical) no Instagram.

    Args:
        video_path: caminho do vídeo MP4 (1080x1920 ideal)
        caption: legenda do Reel
        thumbnail_path: imagem de capa (opcional)

    Returns:
        media_id do Reel ou None em caso de falha
    """
    if not INSTAGRAM_ENABLED:
        print("  Instagram: desativado (credenciais não configuradas).")
        return None

    if not os.path.exists(video_path):
        print(f"  Instagram: vídeo não encontrado: {video_path}")
        return None

    try:
        cl = _get_client()

        kwargs = {
            "path": video_path,
            "caption": caption,
        }
        if thumbnail_path and os.path.exists(thumbnail_path):
            kwargs["thumbnail"] = thumbnail_path

        print(f"  Instagram: enviando Reel...")
        media = cl.clip_upload(**kwargs)
        media_id = media.pk
        media_code = media.code
        print(f"  Instagram Reel: https://www.instagram.com/reel/{media_code}/")
        return str(media_id)
    except Exception as e:
        print(f"  Instagram erro (Reel): {e}")
        return None


def upload_photo(
    image_path: str,
    caption: str,
) -> str | None:
    """
    Faz upload de uma foto no feed do Instagram.

    Args:
        image_path: caminho da imagem (JPG/PNG)
        caption: legenda da foto

    Returns:
        media_id ou None em caso de falha
    """
    if not INSTAGRAM_ENABLED:
        print("  Instagram: desativado (credenciais não configuradas).")
        return None

    if not os.path.exists(image_path):
        print(f"  Instagram: imagem não encontrada: {image_path}")
        return None

    try:
        cl = _get_client()
        print(f"  Instagram: enviando foto...")
        media = cl.photo_upload(
            path=image_path,
            caption=caption,
        )
        media_id = media.pk
        media_code = media.code
        print(f"  Instagram Post: https://www.instagram.com/p/{media_code}/")
        return str(media_id)
    except Exception as e:
        print(f"  Instagram erro (foto): {e}")
        return None


def upload_reel_with_retry(
    video_path: str,
    caption: str,
    thumbnail_path: str | None = None,
    max_retries: int = 3,
) -> str | None:
    """Upload de Reel com retry e backoff progressivo."""
    for attempt in range(1, max_retries + 1):
        result = upload_reel(video_path, caption, thumbnail_path)
        if result:
            return result
        if attempt < max_retries:
            wait = attempt * 30
            print(f"  Instagram: tentativa {attempt}/{max_retries} falhou. Aguardando {wait}s...")
            time.sleep(wait)
    print(f"  Instagram: todas as {max_retries} tentativas falharam.")
    return None
