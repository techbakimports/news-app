"""
Upload para TikTok via tiktok-uploader (Playwright).

Autenticação via cookies do browser:
  1. Faça login no TikTok pelo navegador
  2. Exporte os cookies com a extensão "Get cookies.txt LOCALLY"
  3. Salve como tiktok_cookies.txt na raiz do projeto

Variável no .env (opcional):
  TIKTOK_COOKIES=caminho/para/cookies.txt   (padrão: tiktok_cookies.txt)
"""
from __future__ import annotations

import os
import time

from dotenv import load_dotenv

load_dotenv()

_COOKIES_PATH = os.environ.get(
    "TIKTOK_COOKIES",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "tiktok_cookies.txt"),
)

TIKTOK_ENABLED = os.path.exists(_COOKIES_PATH)


def upload_video(
    video_path: str,
    description: str,
    hashtags: list[str] | None = None,
) -> bool:
    if not TIKTOK_ENABLED:
        print("  TikTok: desativado (cookies não encontrados).")
        return False

    if not os.path.exists(video_path):
        print(f"  TikTok: vídeo não encontrado: {video_path}")
        return False

    try:
        from tiktok_uploader.upload import upload_video as _tk_upload

        caption = description
        if hashtags:
            tags = " ".join(f"#{t.strip('#')}" for t in hashtags)
            caption = f"{description} {tags}"

        print("  TikTok: enviando vídeo...")
        _tk_upload(
            filename=video_path,
            description=caption,
            cookies=_COOKIES_PATH,
            headless=True,
        )
        print("  TikTok: vídeo publicado com sucesso!")
        return True
    except Exception as e:
        print(f"  TikTok erro: {e}")
        return False


def upload_video_with_retry(
    video_path: str,
    description: str,
    hashtags: list[str] | None = None,
    max_retries: int = 3,
) -> bool:
    for attempt in range(1, max_retries + 1):
        result = upload_video(video_path, description, hashtags)
        if result:
            return True
        if attempt < max_retries:
            wait = attempt * 30
            print(f"  TikTok: tentativa {attempt}/{max_retries} falhou. Aguardando {wait}s...")
            time.sleep(wait)
    print(f"  TikTok: todas as {max_retries} tentativas falharam.")
    return False
