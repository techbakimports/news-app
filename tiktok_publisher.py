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

import json
import os
import time

from dotenv import load_dotenv

load_dotenv()

_COOKIES_PATH = os.environ.get(
    "TIKTOK_COOKIES",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "credentials", "tiktok_cookies.json"),
)

TIKTOK_ENABLED = os.path.exists(_COOKIES_PATH)


def _load_cookies() -> list[dict] | None:
    """
    Carrega cookies do arquivo (JSON ou Netscape txt).
    Retorna lista de dicts compatível com Playwright, ou None se falhar.
    """
    if not os.path.exists(_COOKIES_PATH):
        return None
    try:
        with open(_COOKIES_PATH, "r", encoding="utf-8") as f:
            content = f.read().strip()
        # Detecta formato: JSON começa com [ ou {
        if content.startswith("[") or content.startswith("{"):
            raw = json.loads(content)
            cookies = []
            for c in raw:
                cookie = {
                    "name": c["name"],
                    "value": c["value"],
                    "domain": c.get("domain", ".tiktok.com"),
                    "path": c.get("path", "/"),
                }
                # Normaliza expiração para o campo que o Playwright espera
                exp = c.get("expirationDate") or c.get("expiry") or c.get("expires")
                if exp:
                    cookie["expires"] = int(exp)
                return_same_site = c.get("sameSite", "")
                # Playwright aceita: "Strict", "Lax", "None"
                same_site_map = {
                    "no_restriction": "None", "none": "None",
                    "lax": "Lax", "strict": "Strict",
                }
                mapped = same_site_map.get(return_same_site.lower(), "")
                if mapped:
                    cookie["sameSite"] = mapped
                cookies.append(cookie)
            return cookies
        else:
            # Formato Netscape txt — deixa o tiktok-uploader ler direto
            return None
    except Exception as e:
        print(f"  TikTok: erro ao ler cookies: {e}")
        return None


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
        import tiktok_uploader.upload as _tk_module
    except Exception as e:
        print(f"  TikTok: {_diagnosticar_erro_tiktok(e)}")
        return False

    # Monkey-patch: fecha overlay de tutorial (joyride) que bloqueia cliques.
    # O TikTok mostra um tutorial/onboarding que intercepta pointer events.
    # Injetamos remoção do overlay em CADA etapa que interage com a página.
    _original_remove_cookies = _tk_module._remove_cookies_window
    _original_set_interactivity = _tk_module._set_interactivity
    _original_set_description = _tk_module._set_description

    _JOYRIDE_JS = """
        const overlay = document.querySelector('[data-test-id="overlay"]');
        if (overlay) overlay.remove();
        const portal = document.getElementById('react-joyride-portal');
        if (portal) portal.remove();
        // Também remove qualquer tooltip/beacon do joyride
        document.querySelectorAll('.react-joyride__tooltip, .react-joyride__beacon')
            .forEach(el => el.remove());
    """

    def _patched_remove_cookies(page, *args, **kwargs):
        _original_remove_cookies(page, *args, **kwargs)
        try:
            page.evaluate(_JOYRIDE_JS)
        except Exception:
            pass

    def _patched_set_interactivity(page, *args, **kwargs):
        try:
            page.evaluate(_JOYRIDE_JS)
        except Exception:
            pass
        return _original_set_interactivity(page, *args, **kwargs)

    def _patched_set_description(page, description, *args, **kwargs):
        try:
            page.evaluate(_JOYRIDE_JS)
        except Exception:
            pass
        return _original_set_description(page, description, *args, **kwargs)

    _tk_module._remove_cookies_window = _patched_remove_cookies
    _tk_module._set_interactivity = _patched_set_interactivity
    _tk_module._set_description = _patched_set_description

    try:
        caption = description
        if hashtags:
            tags = " ".join(f"#{t.strip('#')}" for t in hashtags)
            caption = f"{description} {tags}"

        print(f"  TikTok: enviando video ({os.path.getsize(video_path) // 1024} KB)...")

        cookies_list = _load_cookies()
        if cookies_list:
            # JSON detectado — passa como lista de dicts (Playwright-compatible)
            result = _tk_upload(
                filename=video_path,
                description=caption,
                cookies_list=cookies_list,
                headless=True,
            )
        else:
            # Netscape txt — passa o path pro parser interno
            result = _tk_upload(
                filename=video_path,
                description=caption,
                cookies=_COOKIES_PATH,
                headless=True,
            )

        # upload_video retorna True se sucesso, False se falhou
        if result is False:
            print("  TikTok: upload retornou falha (video nao publicado)")
            return False

        print("  TikTok: video publicado com sucesso!")
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
