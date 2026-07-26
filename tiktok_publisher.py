"""
Publicação automática no TikTok via Content Posting API oficial
(developers.tiktok.com — produto "Content Posting API", escopo video.publish).

Enquanto o app não passar pela revisão da TikTok ("app review"), só é
possível publicar em modo privado (SELF_ONLY) — upload_video(public=True)
é automaticamente rebaixado pra SELF_ONLY até a aprovação, evitando erro
de API.
"""
import json
import os
import secrets
import webbrowser
from datetime import datetime, timedelta
from urllib.parse import urlencode

import requests

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TOKEN_FILE = os.path.join(_BASE_DIR, "credentials", "tiktok_token.json")

CLIENT_KEY = os.getenv("TIKTOK_CLIENT_KEY", "")
CLIENT_SECRET = os.getenv("TIKTOK_CLIENT_SECRET", "")
REDIRECT_URI = os.getenv("TIKTOK_REDIRECT_URI", "https://youtube-dark.duckdns.org/tiktok/callback")

AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
INIT_URL = "https://open.tiktokapis.com/v2/post/publish/video/init/"
STATUS_URL = "https://open.tiktokapis.com/v2/post/publish/status/fetch/"

SCOPES = "video.publish"


def get_authorization_url() -> tuple[str, str]:
    """Gera a URL de autorização e o 'state' pra validar o callback. Retorna (url, state)."""
    if not CLIENT_KEY:
        raise RuntimeError("TIKTOK_CLIENT_KEY não configurado no .env")
    state = secrets.token_urlsafe(16)
    params = {
        "client_key": CLIENT_KEY,
        "scope": SCOPES,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "state": state,
    }
    return f"{AUTH_URL}?{urlencode(params)}", state


def exchange_code_for_token(code: str) -> dict:
    """Troca o código de autorização (recebido no redirect) pelo token de acesso."""
    resp = requests.post(
        TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "client_key": CLIENT_KEY,
            "client_secret": CLIENT_SECRET,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": REDIRECT_URI,
        },
        timeout=15,
    )
    resp.raise_for_status()
    token = resp.json()
    if "access_token" not in token:
        raise RuntimeError(f"TikTok não retornou access_token: {token}")
    _save_token(token)
    return token


def _save_token(token: dict) -> None:
    os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
    token["_saved_at"] = datetime.now().isoformat()
    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        json.dump(token, f, ensure_ascii=False, indent=2)


def _load_token() -> dict | None:
    if not os.path.exists(TOKEN_FILE):
        return None
    with open(TOKEN_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _refresh_token(refresh_token: str) -> dict:
    resp = requests.post(
        TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "client_key": CLIENT_KEY,
            "client_secret": CLIENT_SECRET,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        timeout=15,
    )
    resp.raise_for_status()
    token = resp.json()
    if "access_token" not in token:
        raise RuntimeError(f"TikTok refresh falhou: {token}")
    _save_token(token)
    return token


def _get_valid_access_token() -> str:
    """Retorna um access_token válido, renovando via refresh_token se necessário."""
    token = _load_token()
    if not token:
        raise RuntimeError(
            "TikTok não autenticado. No admin, use o botão 'Conectar TikTok', "
            "ou rode: python -c \"from tiktok_publisher import run_oauth_flow; run_oauth_flow()\""
        )
    saved_at = datetime.fromisoformat(token["_saved_at"])
    expires_in = token.get("expires_in", 0)
    if datetime.now() < saved_at + timedelta(seconds=expires_in - 60):
        return token["access_token"]

    print("  [tiktok] Access token expirado — renovando via refresh_token...")
    token = _refresh_token(token["refresh_token"])
    return token["access_token"]


def check_tiktok_token() -> tuple[bool, str]:
    """Verifica se há um token TikTok salvo e ainda renovável. Não faz chamada de API (evita gastar quota)."""
    token = _load_token()
    if not token:
        return False, "Não conectado"
    try:
        saved_at = datetime.fromisoformat(token["_saved_at"])
        refresh_expires_in = token.get("refresh_expires_in", 0)
        refresh_expiry = saved_at + timedelta(seconds=refresh_expires_in)
        if datetime.now() >= refresh_expiry:
            return False, "Refresh token expirado — reconectar"
        return True, f"Conectado (refresh válido até {refresh_expiry.strftime('%d/%m/%Y')})"
    except Exception as e:
        return False, f"Erro ao ler token: {e}"


def run_oauth_flow() -> None:
    """Fluxo interativo único (uso local via terminal): abre o navegador, usuário aprova, cola o código."""
    url, _state = get_authorization_url()
    print("\n  Autenticação TikTok necessária.")
    print(f"  Abra esta URL, faça login na conta que vai publicar e aprove o acesso:\n\n  {url}\n")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    code = input("  Cole aqui o parâmetro 'code' da URL de redirecionamento: ").strip()
    exchange_code_for_token(code)
    print(f"  ✅ Token TikTok salvo em {TOKEN_FILE}")


def upload_video(video_path: str, caption: str, public: bool = False) -> str:
    """
    Publica um vídeo no TikTok via Content Posting API (Direct Post, upload único).

    public=True só tem efeito depois que o app passar pela revisão da TikTok.
    Enquanto não aprovado, é forçado SELF_ONLY (rascunho privado) mesmo com
    public=True, pra evitar erro de API.

    Retorna o publish_id (use check_publish_status() pra acompanhar o processamento —
    a API não devolve a URL pública do vídeo neste passo).
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Vídeo não encontrado: {video_path}")

    access_token = _get_valid_access_token()
    video_size = os.path.getsize(video_path)
    privacy_level = "PUBLIC_TO_EVERYONE" if public else "SELF_ONLY"

    init_resp = requests.post(
        INIT_URL,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=UTF-8",
        },
        json={
            "post_info": {
                "title": caption[:2200],
                "privacy_level": privacy_level,
                "disable_duet": False,
                "disable_comment": False,
                "disable_stitch": False,
            },
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": video_size,
                "chunk_size": video_size,
                "total_chunk_count": 1,
            },
        },
        timeout=30,
    )
    init_resp.raise_for_status()
    init_data = init_resp.json()
    error = init_data.get("error", {})
    if error.get("code") not in (None, "ok"):
        raise RuntimeError(f"TikTok init falhou: {error}")

    publish_id = init_data["data"]["publish_id"]
    upload_url = init_data["data"]["upload_url"]

    with open(video_path, "rb") as f:
        video_bytes = f.read()

    put_resp = requests.put(
        upload_url,
        headers={
            "Content-Type": "video/mp4",
            "Content-Range": f"bytes 0-{video_size - 1}/{video_size}",
        },
        data=video_bytes,
        timeout=120,
    )
    put_resp.raise_for_status()

    print(f"  [tiktok] Upload enviado (publish_id={publish_id}, privacidade={privacy_level})")
    if not public:
        print("  [tiktok] App ainda não auditado — vídeo entrou como RASCUNHO PRIVADO.")
        print("           Abra o app do TikTok na conta pra revisar e publicar manualmente.")

    return publish_id


def check_publish_status(publish_id: str) -> dict:
    """Consulta o status de processamento (PROCESSING_UPLOAD, PUBLISH_COMPLETE, FAILED...)."""
    access_token = _get_valid_access_token()
    resp = requests.post(
        STATUS_URL,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=UTF-8",
        },
        json={"publish_id": publish_id},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


if __name__ == "__main__":
    run_oauth_flow()
