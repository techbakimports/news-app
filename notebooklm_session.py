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
    1. Headless com browser profile (se Google ainda aceitar os cookies do profile)
    2. Browser visivel com auto-detect de login (abre janela, usuario loga, fecha sozinho)

    Retorna True se renovou com sucesso, False se falhou.
    """
    if not BROWSER_PROFILE.exists():
        if verbose:
            print("❌ Browser profile nao encontrado.")
            print(f"   Esperado em: {BROWSER_PROFILE}")
            print("   Execute 'notebooklm login' manualmente primeiro.")
        return False

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        if verbose:
            print("❌ Playwright nao instalado.")
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


# -- Entry point ---------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(
        prog="notebooklm_session.py",
        description="Verifica e renova sessoes/credenciais do projeto",
    )
    parser.add_argument("--refresh", action="store_true",
                        help="Tenta renovar a sessao NotebookLM automaticamente")
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
