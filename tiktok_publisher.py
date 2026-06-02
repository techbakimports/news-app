"""
Upload para TikTok via tiktok-uploader (Playwright).

Autenticação via cookies do browser:
  1. Faça login no TikTok pelo navegador
  2. Exporte os cookies com a extensão "Get cookies.txt LOCALLY"
  3. Salve como credentials/tiktok_cookies.json no projeto

Variável no .env (opcional):
  TIKTOK_COOKIES=caminho/para/cookies.txt   (padrão: tiktok_cookies.json)
"""
from __future__ import annotations

import os
import time

from dotenv import load_dotenv

load_dotenv()

_COOKIES_PATH = os.environ.get(
    "TIKTOK_COOKIES",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "credentials", "tiktok_cookies.json"),
)

TIKTOK_ENABLED = os.path.exists(_COOKIES_PATH)


def _diagnosticar_erro_tiktok(err: Exception) -> str:
    """
    Olha o erro e retorna uma mensagem amigável com causa provável + sugestão.
    """
    msg = str(err).lower()
    err_type = type(err).__name__

    # ImportError — pacote não instalado
    if isinstance(err, ImportError) or isinstance(err, ModuleNotFoundError):
        if "tiktok_uploader" in msg or "tiktok-uploader" in msg:
            return (
                "❌ PACOTE PIP AUSENTE — tiktok-uploader não instalado.\n"
                "      Fix: .venv/bin/pip install tiktok-uploader"
            )
        return f"❌ MÓDULO AUSENTE — {err}\n      Fix: pip install -r requirements.txt"

    # Chrome / chromedriver
    if "chromedriver" in msg or "chrome binary" in msg or "cannot find chrome" in msg:
        return (
            "❌ CHROME NÃO INSTALADO na VPS.\n"
            "      Fix Rocky 9:\n"
            "        sudo tee /etc/yum.repos.d/google-chrome.repo > /dev/null <<'EOF'\n"
            "        [google-chrome]\n"
            "        name=google-chrome\n"
            "        baseurl=https://dl.google.com/linux/chrome/rpm/stable/x86_64\n"
            "        enabled=1\n"
            "        gpgcheck=1\n"
            "        gpgkey=https://dl.google.com/linux/linux_signing_key.pub\n"
            "        EOF\n"
            "        sudo dnf install -y google-chrome-stable"
        )

    if "session not created" in msg or "version of chromedriver" in msg:
        return (
            "❌ CHROME/CHROMEDRIVER COM VERSÃO INCOMPATÍVEL.\n"
            "      Fix: sudo dnf update -y google-chrome-stable\n"
            "      E garantir tiktok-uploader atualizado: pip install -U tiktok-uploader"
        )

    # Cookies expirados ou login required
    if "login" in msg or "authenticat" in msg or "session expired" in msg or "unauthorized" in msg:
        return (
            "❌ COOKIES TIKTOK EXPIRADOS — sessão rejeitada.\n"
            "      Fix:\n"
            "      1. Login no TikTok pelo browser do PC\n"
            "      2. Extensão 'Get cookies.txt LOCALLY' → exporta como JSON\n"
            "      3. Salva em credentials/tiktok_cookies.json e copia pra VPS"
        )

    # Captcha
    if "captcha" in msg or "verify" in msg:
        return (
            "⚠️ TIKTOK PEDIU CAPTCHA — anti-bot ativado.\n"
            "      Causas: muitos uploads no mesmo dia OU sessão suspeita.\n"
            "      Fix: aguardar algumas horas. Reduzir volume diário."
        )

    # Timeout de rede
    if "timeout" in msg or "connection" in msg or "network" in msg:
        return (
            "⚠️ TIMEOUT / REDE — conexão instável durante upload.\n"
            "      Fix: tentar de novo. Se persistir, ver rede da VPS."
        )

    # Vídeo formato/tamanho
    if "video" in msg and ("format" in msg or "size" in msg or "duration" in msg):
        return (
            "❌ VÍDEO REJEITADO PELO TIKTOK — formato/tamanho/duração inválido.\n"
            "      Limites: MP4, < 287 MB, entre 3s e 10 min."
        )

    # Rate limit
    if "rate" in msg or "too many" in msg or "limit" in msg:
        return (
            "⚠️ RATE LIMIT — muitos uploads recentes.\n"
            "      Fix: aguardar 1-2h, depois tentar novamente."
        )

    # Erro genérico — mostra tudo
    return f"❌ ERRO INESPERADO ({err_type}): {err}"


def upload_video(
    video_path: str,
    description: str,
    hashtags: list[str] | None = None,
) -> bool:
    if not TIKTOK_ENABLED:
        print(f"  TikTok: ❌ DESATIVADO — cookies não encontrados em {_COOKIES_PATH}")
        print(f"          Fix: exportar cookies do browser e copiar pra esse caminho")
        return False

    if not os.path.exists(video_path):
        print(f"  TikTok: ❌ vídeo não encontrado: {video_path}")
        return False

    try:
        from tiktok_uploader.upload import upload_video as _tk_upload
    except Exception as e:
        print(f"  TikTok: {_diagnosticar_erro_tiktok(e)}")
        return False

    try:
        caption = description
        if hashtags:
            tags = " ".join(f"#{t.strip('#')}" for t in hashtags)
            caption = f"{description} {tags}"

        print(f"  TikTok: 📤 enviando vídeo ({os.path.getsize(video_path) // 1024} KB)...")
        _tk_upload(
            filename=video_path,
            description=caption,
            cookies=_COOKIES_PATH,
            headless=True,
        )
        print("  TikTok: ✅ vídeo publicado com sucesso!")
        return True
    except Exception as e:
        print(f"  TikTok: {_diagnosticar_erro_tiktok(e)}")
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
