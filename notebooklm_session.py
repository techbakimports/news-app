"""
Gerenciamento de sessao NotebookLM — validacao e auto-refresh.

Funcionalidades:
- check()   → verifica se a sessao esta ativa (retorna True/False)
- refresh() → tenta renovar usando o browser profile persistente (headless)

Uso direto:
    python notebooklm_session.py          # verifica status
    python notebooklm_session.py --refresh  # tenta renovar automaticamente

Como NOTEBOOKLM_REFRESH_CMD (auto-refresh quando sessao expira):
    No .env: NOTEBOOKLM_REFRESH_CMD=python notebooklm_session.py --refresh
"""
import asyncio
import os
import shutil
import sys
import time
from pathlib import Path

# Forcar UTF-8 no terminal Windows
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

# Caminhos
BASE_DIR = Path(__file__).parent
CREDENTIALS_STORAGE = BASE_DIR / "credentials" / "notebooklm_storage_state.json"
DEFAULT_PROFILE_DIR = Path.home() / ".notebooklm" / "profiles" / "default"
DEFAULT_STORAGE = DEFAULT_PROFILE_DIR / "storage_state.json"
BROWSER_PROFILE = DEFAULT_PROFILE_DIR / "browser_profile"

NOTEBOOKLM_URL = "https://notebooklm.google.com/"

# Cookies "mestres" — duram anos. Suficientes pra autenticar e gerar novos rotativos.
_LONG_LIVED_COOKIES = {
    "SID", "HSID", "SSID", "APISID", "SAPISID",
    "__Secure-1PSID", "__Secure-3PSID",
    "__Secure-1PAPISID", "__Secure-3PAPISID",
    "LSID", "__Host-1PLSID", "__Host-3PLSID",
    "OSID", "__Secure-OSID",
    "NID",
}
_GOOGLE_DOMAINS = (".google.com", "google.com", "accounts.google.com", "notebooklm.google.com")
_UA_CHROME = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


def _load_storage_state() -> dict | None:
    """Carrega o storage_state.json mais recente disponivel."""
    import json
    for path in (CREDENTIALS_STORAGE, DEFAULT_STORAGE):
        if path.exists():
            try:
                with open(path, encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                continue
    return None


def _save_storage_state(data: dict) -> None:
    """Salva o storage_state em ambos os caminhos padrao."""
    import json
    DEFAULT_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    CREDENTIALS_STORAGE.parent.mkdir(parents=True, exist_ok=True)
    with open(DEFAULT_STORAGE, "w", encoding="utf-8") as f:
        json.dump(data, f)
    shutil.copy2(DEFAULT_STORAGE, CREDENTIALS_STORAGE)


def _cookies_to_jar(cookies: list[dict]):
    """
    Converte cookies do storage_state pra http.cookiejar.CookieJar com domain/path/expires.
    Isso garante que httpx envia exatamente os cookies certos pra cada dominio.
    """
    from http.cookiejar import Cookie, CookieJar
    jar = CookieJar()
    for c in cookies:
        domain = c.get("domain", "")
        if "google" not in domain:
            continue
        cookie = Cookie(
            version=0,
            name=c["name"],
            value=c["value"],
            port=None,
            port_specified=False,
            domain=domain,
            domain_specified=True,
            domain_initial_dot=domain.startswith("."),
            path=c.get("path", "/"),
            path_specified=True,
            secure=c.get("secure", False),
            expires=int(c["expires"]) if c.get("expires", 0) > 0 else None,
            discard=False,
            comment=None,
            comment_url=None,
            rest={"HttpOnly": ""} if c.get("httpOnly") else {},
            rfc2109=False,
        )
        jar.set_cookie(cookie)
    return jar


def _merge_cookies(storage: dict, new_cookies_by_domain: dict, default_domain: str = ".google.com") -> int:
    """
    Mescla cookies novos (de Set-Cookie) no storage_state.
    Preserva duplicatas por (name, domain) — Google tem o mesmo nome em
    .google.com e accounts.google.com com valores diferentes.

    new_cookies_by_domain: dict[(name, domain)] -> value
    Retorna quantos cookies foram atualizados ou adicionados.
    """
    # Indexa por (name, domain) pra nao perder duplicatas
    existing_cookies = storage.get("cookies", [])
    existing_idx = {(c["name"], c.get("domain", "")): c for c in existing_cookies}
    updated = 0

    # Tambem indexa so por nome pra atualizar TODOS os cookies com aquele nome
    # (quando Google rotaciona, geralmente rotaciona em todos os dominios)
    by_name: dict[str, list] = {}
    for c in existing_cookies:
        by_name.setdefault(c["name"], []).append(c)

    for (name, domain), value in new_cookies_by_domain.items():
        # Caso 1: match exato (name + domain) — so atualiza se valor mudou
        key = (name, domain)
        if key in existing_idx:
            c = existing_idx[key]
            if c["value"] != value:
                c["value"] = value
                c["expires"] = time.time() + 86400 * 365
                updated += 1
            continue

        # Caso 2: mesmo nome em outros dominios — atualiza se valor mudou
        if name in by_name:
            for c in by_name[name]:
                if c["value"] != value:
                    c["value"] = value
                    c["expires"] = time.time() + 86400 * 365
                    updated += 1
            continue

        # Caso 3: cookie novo
        new_cookie = {
            "name": name,
            "value": value,
            "domain": domain or default_domain,
            "path": "/",
            "expires": time.time() + 86400 * 365,
            "httpOnly": False,
            "secure": True,
            "sameSite": "Lax",
        }
        existing_cookies.append(new_cookie)
        existing_idx[key] = new_cookie
        by_name.setdefault(name, []).append(new_cookie)
        updated += 1

    storage["cookies"] = existing_cookies
    return updated


def refresh_via_http(verbose: bool = True) -> bool:
    """
    Rotaciona cookies do Google via HTTP — sem browser.
    Funciona em VPS sem display.

    Estrategia: visita endpoints que o Chrome usa pra rotacionar tokens
    (SIDCC, PSIDTS, PSIDRTS). Os cookies long-lived (SID, SAPISID, etc)
    autenticam a requisicao; o Google responde com Set-Cookie atualizando
    os rotativos.

    Retorna True se renovou pelo menos 1 cookie, False caso contrario.
    """
    try:
        import httpx
    except ImportError:
        if verbose:
            print("❌ httpx nao instalado.")
        return False

    storage = _load_storage_state()
    if storage is None:
        if verbose:
            print("❌ storage_state.json nao encontrado.")
        return False

    cookies = storage.get("cookies", [])
    if not cookies:
        if verbose:
            print("❌ storage_state sem cookies.")
        return False

    # Verifica se os long-lived ainda estao validos
    now = time.time()
    long_lived = [c for c in cookies if c["name"] in _LONG_LIVED_COOKIES]
    expired_long = [c for c in long_lived if 0 < c.get("expires", 0) < now]

    if expired_long:
        if verbose:
            print(f"❌ {len(expired_long)} cookies mestres expirados — precisa login manual.")
            for c in expired_long[:3]:
                print(f"   • {c['name']}")
        return False

    if verbose:
        print(f"🔄 Refresh HTTP iniciando ({len(cookies)} cookies)...")

    jar = _cookies_to_jar(cookies)

    headers = {
        "User-Agent": _UA_CHROME,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Dest": "document",
        "Upgrade-Insecure-Requests": "1",
    }

    # Sequencia que dispara rotacao de SIDCC/SIDTS/PSIDTS/PSIDRTS no Google.
    # myaccount e drive sao endpoints conhecidos por rotacionar agressivamente.
    endpoints = [
        "https://myaccount.google.com/",
        "https://drive.google.com/",
        "https://accounts.google.com/ServiceLogin",
        "https://notebooklm.google.com/",
        "https://accounts.google.com/CheckCookie",
    ]

    all_new_cookies: dict[tuple[str, str], str] = {}

    try:
        with httpx.Client(
            cookies=jar,
            headers=headers,
            follow_redirects=True,
            timeout=20.0,
            http2=False,
        ) as client:
            for url in endpoints:
                try:
                    r = client.get(url)
                    if verbose:
                        print(f"   GET {url[:60]}... → {r.status_code}")
                except Exception as e:
                    if verbose:
                        print(f"   ⚠️  Falhou {url[:60]}: {e}")
                    continue

            # Coleta cookies acumulados — usa (name, domain) pra preservar duplicatas
            for cookie in client.cookies.jar:
                if "google" in (cookie.domain or ""):
                    domain = cookie.domain
                    # Normaliza dominio: httpx as vezes retorna "accounts.google.com" sem leading dot
                    all_new_cookies[(cookie.name, domain)] = cookie.value

    except Exception as e:
        if verbose:
            print(f"❌ Erro na requisicao: {e}")
        return False

    if not all_new_cookies:
        if verbose:
            print("⚠️  Nenhum cookie retornado pelo Google.")
        return False

    # Mescla no storage_state
    updated = _merge_cookies(storage, all_new_cookies)

    if updated == 0:
        if verbose:
            print("ℹ️  Cookies ja estavam atualizados (nada mudou).")
        # Mesmo assim consideramos sucesso — sessao esta viva
        return True

    _save_storage_state(storage)

    if verbose:
        print(f"✅ {updated} cookies renovados via HTTP.")
        print(f"   Salvo em: {CREDENTIALS_STORAGE}")
    return True


async def check(verbose: bool = True) -> bool:
    """
    Verifica se a sessao NotebookLM esta ativa.
    Retorna True se autenticada, False se expirada.
    """
    storage_path = str(CREDENTIALS_STORAGE) if CREDENTIALS_STORAGE.exists() else None

    try:
        from notebooklm import NotebookLMClient
        client = await NotebookLMClient.from_storage(path=storage_path)
        async with client:
            # Se chegar aqui sem erro, a sessao esta ativa
            if verbose:
                print("✅ Sessao NotebookLM ATIVA")
            return True
    except (ValueError, FileNotFoundError) as e:
        if verbose:
            print(f"❌ Sessao NotebookLM EXPIRADA")
            print(f"   Erro: {e}")
        return False
    except Exception as e:
        if verbose:
            print(f"⚠️  Erro inesperado ao verificar sessao: {e}")
        return False


def refresh(verbose: bool = True) -> bool:
    """
    Tenta renovar a sessao NotebookLM.

    Estrategia:
    0. HTTP refresh (sem browser — funciona em VPS sem display)
    1. Headless com browser profile (se Google ainda aceitar os cookies do profile)
    2. Browser visivel com auto-detect de login (abre janela, usuario loga, fecha sozinho)

    Retorna True se renovou com sucesso, False se falhou.
    """
    # Tentativa 0: HTTP puro (rapido, leve, funciona em VPS)
    if verbose:
        print("🔄 Tentativa 0: refresh via HTTP (sem browser)...")
    if refresh_via_http(verbose=verbose):
        return True

    if not BROWSER_PROFILE.exists():
        if verbose:
            print("❌ Browser profile nao encontrado para tentativa Playwright.")
            print(f"   Esperado em: {BROWSER_PROFILE}")
            print("   Refresh HTTP foi a unica opcao — execute 'notebooklm login' se persistir.")
        return False

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        if verbose:
            print("❌ Playwright nao instalado — pulando tentativas com browser.")
            print("   pip install playwright && playwright install chromium")
        return False

    # Tentativa 1: headless
    if verbose:
        print("🔄 Tentativa 1: refresh headless...")
    result = _try_refresh_with_playwright(sync_playwright, headless=True, verbose=verbose)
    if result:
        return True

    # Tentativa 2: browser visivel (auto-detect login)
    if verbose:
        print("\n🔄 Tentativa 2: abrindo browser para login...")
        print("   Faca login na janela do Chromium que abrir.")
        print("   O browser fecha automaticamente ao detectar o login.")
    result = _try_refresh_with_playwright(sync_playwright, headless=False, verbose=verbose, wait_for_login=True)
    return result


def _try_refresh_with_playwright(sync_playwright_cls, headless: bool, verbose: bool, wait_for_login: bool = False) -> bool:
    """Tenta refresh com Playwright (headless ou visivel)."""
    import json

    try:
        with sync_playwright_cls() as p:
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(BROWSER_PROFILE),
                headless=headless,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--password-store=basic",
                ],
                ignore_default_args=["--enable-automation"],
            )

            try:
                page = context.pages[0] if context.pages else context.new_page()

                if verbose:
                    print("   Navegando para NotebookLM...")
                page.goto(NOTEBOOKLM_URL, timeout=30000, wait_until="domcontentloaded")
                time.sleep(3)

                final_url = page.url

                # Se redirecionou para login e nao estamos em modo de espera
                if ("accounts.google.com" in final_url or "signin" in final_url):
                    if not wait_for_login:
                        if verbose:
                            print(f"   Redirecionou para login (headless nao resolve).")
                        return False

                    # Modo espera: aguardar usuario logar (max 120s)
                    if verbose:
                        print("   Aguardando login no browser (max 2 min)...")
                    deadline = time.time() + 120
                    while time.time() < deadline:
                        time.sleep(3)
                        current = page.url
                        if "notebooklm.google.com" in current and "login" not in current:
                            if verbose:
                                print("   ✅ Login detectado!")
                            break
                    else:
                        if verbose:
                            print("   ⏰ Timeout — login nao detectado em 2 minutos.")
                        return False

                # Verificar se estamos na homepage do NotebookLM
                final_url = page.url
                if "notebooklm.google.com" not in final_url or "login" in final_url:
                    if verbose:
                        print(f"   ❌ URL inesperada: {final_url}")
                    return False

                # Estamos logados! Coletar cookies
                if verbose:
                    print("   Coletando cookies frescos...")

                # Navegar pra garantir cookies completos
                page.goto("https://accounts.google.com", wait_until="commit")
                time.sleep(1)
                page.goto(NOTEBOOKLM_URL, wait_until="commit")
                time.sleep(2)

                # Salvar storage state
                storage_state = context.storage_state()

                DEFAULT_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
                with open(DEFAULT_STORAGE, "w", encoding="utf-8") as f:
                    json.dump(storage_state, f)

                CREDENTIALS_STORAGE.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(DEFAULT_STORAGE, CREDENTIALS_STORAGE)

                if verbose:
                    print(f"   ✅ Sessao salva em: {CREDENTIALS_STORAGE}")
                return True

            finally:
                context.close()

    except Exception as e:
        if verbose:
            print(f"   Erro: {e}")
        return False


async def check_and_refresh(verbose: bool = True) -> bool:
    """
    Verifica a sessao. Se expirada, tenta renovar automaticamente.
    Retorna True se a sessao esta ativa (ou foi renovada com sucesso).
    """
    if await check(verbose=verbose):
        return True

    if verbose:
        print("\n🔄 Tentando auto-refresh...")

    # Rodar refresh em thread separada (Playwright sync nao funciona dentro do asyncio loop)
    success = await asyncio.to_thread(refresh, verbose)
    if success:
        if verbose:
            print("\n🔍 Verificando apos refresh...")
        return await check(verbose=verbose)

    return False


# -- Verificacao de TODAS as credenciais --------------------------------------

def check_youtube(verbose: bool = True) -> bool:
    """Verifica se o token OAuth do YouTube esta valido."""
    import pickle
    from google.auth.transport.requests import Request

    token_file = BASE_DIR / "credentials" / "token.json"
    secrets_file = BASE_DIR / "credentials" / "client_secrets.json"

    if not token_file.exists():
        if verbose:
            print("❌ YouTube: token.json nao encontrado")
        return False

    if not secrets_file.exists():
        if verbose:
            print("⚠️  YouTube: client_secrets.json ausente (necessario para renovar)")
        return False

    try:
        with open(token_file, "rb") as f:
            creds = pickle.load(f)

        if creds and creds.valid:
            if verbose:
                print("✅ YouTube: token ATIVO")
            return True

        if creds and creds.expired and creds.refresh_token:
            # Tentar renovar automaticamente
            try:
                creds.refresh(Request())
                with open(token_file, "wb") as f:
                    pickle.dump(creds, f)
                if verbose:
                    print("✅ YouTube: token RENOVADO automaticamente")
                return True
            except Exception as e:
                if verbose:
                    print(f"❌ YouTube: refresh falhou — {e}")
                    print("   Proximo upload vai reautenticar via browser")
                return False

        if verbose:
            print("❌ YouTube: token expirado sem refresh_token — proximo upload reautentica")
        return False

    except Exception as e:
        if verbose:
            print(f"❌ YouTube: erro ao verificar — {e}")
        return False


def check_instagram(verbose: bool = True) -> bool:
    """Verifica se as credenciais do Instagram estao configuradas."""
    from dotenv import load_dotenv
    load_dotenv()

    username = os.environ.get("INSTAGRAM_USERNAME")
    password = os.environ.get("INSTAGRAM_PASSWORD")
    session_file = BASE_DIR / "credentials" / "ig_session.json"

    if not username or not password:
        if verbose:
            print("⬚ Instagram: DESATIVADO (sem credenciais no .env)")
        return None  # None = desativado (nao conta como falha)

    if session_file.exists():
        if verbose:
            print("✅ Instagram: credenciais OK + sessao salva")
        return True
    else:
        if verbose:
            print("⚠️  Instagram: credenciais OK, mas sem sessao salva (vai logar na proxima execucao)")
        return True


def check_tiktok(verbose: bool = True) -> bool:
    """Verifica se os cookies do TikTok existem."""
    cookies_file = BASE_DIR / "credentials" / "tiktok_cookies.json"

    if not cookies_file.exists():
        if verbose:
            print("⬚ TikTok: DESATIVADO (sem cookies)")
        return False

    # Verificar se o arquivo nao esta vazio/corrompido
    try:
        import json
        with open(cookies_file, encoding="utf-8") as f:
            data = json.load(f)
        if not data:
            if verbose:
                print("❌ TikTok: arquivo de cookies vazio")
            return False
        if verbose:
            mtime = os.path.getmtime(cookies_file)
            from datetime import datetime
            dt = datetime.fromtimestamp(mtime).strftime("%d/%m/%Y")
            print(f"✅ TikTok: cookies presentes (salvos em {dt})")
            print("   ⚠️  Cookies podem expirar — reexportar se falhar")
        return True
    except Exception as e:
        if verbose:
            print(f"❌ TikTok: erro ao ler cookies — {e}")
        return False


async def check_all(verbose: bool = True) -> dict:
    """Verifica todas as credenciais do projeto. Retorna dict com status."""
    results = {}

    if verbose:
        print("=" * 45)
        print("   STATUS DAS CREDENCIAIS")
        print("=" * 45)
        print()

    # NotebookLM
    results["notebooklm"] = await check(verbose=verbose)
    if verbose:
        print()

    # YouTube
    results["youtube"] = check_youtube(verbose=verbose)
    if verbose:
        print()

    # Instagram
    results["instagram"] = check_instagram(verbose=verbose)
    if verbose:
        print()

    # TikTok
    results["tiktok"] = check_tiktok(verbose=verbose)

    if verbose:
        print()
        print("-" * 45)
        ativos = sum(1 for v in results.values() if v)
        total = len(results)
        print(f"   {ativos}/{total} credenciais ativas")
        print("-" * 45)

    return results


# -- Verificação rápida pré-pipeline ------------------------------------------

TIKTOK_COOKIE_WARN_DAYS = 14


def check_gemini(verbose: bool = True) -> bool:
    """Verifica se a API key do Gemini esta configurada."""
    from dotenv import load_dotenv
    load_dotenv()
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        if verbose:
            print("❌ Gemini: GEMINI_API_KEY ausente no .env")
        return False
    if verbose:
        print("✅ Gemini: API key configurada")
    return True


def check_notebooklm_quick(verbose: bool = True) -> bool:
    """Verifica rapida: arquivo de sessao existe e nao e muito antigo (< 7 dias)."""
    for path in (CREDENTIALS_STORAGE, DEFAULT_STORAGE):
        if path.exists():
            age_days = (time.time() - os.path.getmtime(path)) / 86400
            if verbose:
                if age_days > 7:
                    print(f"⚠️  NotebookLM: sessao com {int(age_days)} dias — pode ter expirado")
                else:
                    print(f"✅ NotebookLM: sessao presente ({int(age_days)}d)")
            return age_days <= 7
    if verbose:
        print("❌ NotebookLM: sessao nao encontrada")
    return False


def preflight_check(pipeline: str, upload: bool = True, verbose: bool = True) -> dict:
    """
    Verifica credenciais necessarias antes de iniciar um pipeline.

    Args:
        pipeline: "noticias", "audio", "shorts", "tech_news"
        upload: True se vai fazer upload (False para --sem-upload)
        verbose: True para imprimir detalhes

    Returns:
        {"ok": bool, "critical": list[str], "warnings": list[str]}
    """
    critical = []
    warnings = []

    needs_youtube = upload and pipeline in ("noticias", "audio", "shorts", "tech_news")
    needs_instagram = upload and pipeline in ("noticias", "shorts")
    needs_tiktok = upload and pipeline in ("shorts",)
    needs_gemini = pipeline in ("noticias", "shorts")
    needs_notebooklm = pipeline in ("tech_news",)

    if verbose:
        print()
        print("=" * 45)
        print("   VERIFICAÇÃO PRÉ-PIPELINE")
        print("=" * 45)
        print()

    # YouTube
    if needs_youtube:
        try:
            if not check_youtube(verbose=verbose):
                critical.append("YouTube: token expirado — upload vai falhar")
        except Exception as e:
            critical.append(f"YouTube: erro na verificação — {e}")
        if verbose:
            print()

    # Gemini API key
    if needs_gemini:
        if not check_gemini(verbose=verbose):
            critical.append("Gemini: API key ausente — resumos vão falhar")
        if verbose:
            print()

    # NotebookLM (verificação rapida por arquivo — so para tech_news)
    if needs_notebooklm:
        if not check_notebooklm_quick(verbose=verbose):
            critical.append("NotebookLM: sessão ausente ou expirada — pipeline vai falhar")
        if verbose:
            print()

    # Instagram (opcional — so warning)
    if needs_instagram:
        try:
            ig = check_instagram(verbose=verbose)
            if ig is False:
                warnings.append("Instagram: problema na sessão")
        except Exception:
            warnings.append("Instagram: erro na verificação")
        if verbose:
            print()

    # TikTok (opcional — so warning)
    if needs_tiktok:
        try:
            tk = check_tiktok(verbose=verbose)
            if tk:
                cookies_file = BASE_DIR / "credentials" / "tiktok_cookies.json"
                if cookies_file.exists():
                    age_days = (time.time() - os.path.getmtime(cookies_file)) / 86400
                    if age_days > TIKTOK_COOKIE_WARN_DAYS:
                        warnings.append(
                            f"TikTok: cookies com {int(age_days)} dias — podem ter expirado"
                        )
        except Exception:
            warnings.append("TikTok: erro na verificação")
        if verbose:
            print()

    ok = len(critical) == 0

    if verbose:
        print("-" * 45)
        if ok and not warnings:
            print("   ✅ Tudo pronto!")
        elif ok:
            print("   ⚠️  Pronto, mas com avisos:")
            for w in warnings:
                print(f"      • {w}")
        else:
            print("   ❌ PROBLEMAS CRÍTICOS:")
            for c in critical:
                print(f"      • {c}")
            if warnings:
                print("   ⚠️  Avisos:")
                for w in warnings:
                    print(f"      • {w}")
        print("-" * 45)
        print()

    return {"ok": ok, "critical": critical, "warnings": warnings}


# -- Entry point ---------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(
        prog="notebooklm_session.py",
        description="Verifica e renova sessoes/credenciais do projeto",
    )
    parser.add_argument("--refresh", action="store_true",
                        help="Tenta renovar a sessao NotebookLM automaticamente")
    parser.add_argument("--refresh-http", action="store_true",
                        help="Refresh APENAS via HTTP (sem browser) — ideal pra VPS/cron")
    parser.add_argument("--check-only", action="store_true",
                        help="Apenas verifica NotebookLM sem tentar renovar")
    parser.add_argument("--all", action="store_true",
                        help="Verifica todas as credenciais (YouTube, Instagram, TikTok, NotebookLM)")
    parser.add_argument("--quiet", action="store_true",
                        help="Saida minima (exit code: 0=ok, 1=problema)")
    args = parser.parse_args()

    verbose = not args.quiet

    if args.all:
        results = asyncio.run(check_all(verbose=verbose))
        # Falha so se algo que esta configurado nao funciona
        # (None = desativado, nao conta como falha)
        has_failure = any(v is False for v in results.values() if v is not None)
        sys.exit(1 if has_failure else 0)
    elif args.refresh_http:
        success = refresh_via_http(verbose=verbose)
        sys.exit(0 if success else 1)
    elif args.refresh:
        success = refresh(verbose=verbose)
        sys.exit(0 if success else 1)
    elif args.check_only:
        ok = asyncio.run(check(verbose=verbose))
        sys.exit(0 if ok else 1)
    else:
        # Padrao: check NotebookLM + refresh se necessario
        ok = asyncio.run(check_and_refresh(verbose=verbose))
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
