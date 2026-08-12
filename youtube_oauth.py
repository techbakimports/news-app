"""
OAuth do YouTube por CLIENTE do SaaS — fluxo web (connect/callback), separado
de propósito do fluxo desktop do canal principal em uploader.py.

Por que um módulo separado: uploader.py usa InstalledAppFlow (abre navegador
LOCAL, só funciona rodando na máquina do operador) — não serve pra um fluxo
onde o CLIENTE, remotamente, autoriza o próprio canal pelo navegador dele.
Aqui usamos google_auth_oauthlib.flow.Flow com redirect_uri explícito (fluxo
"Web application"), que é o tipo certo de credencial OAuth pra isso.

Pré-requisito: um client OAuth tipo "Web application" no Google Cloud
Console (diferente do client_secrets.json atual, que é tipo "Desktop" e só
aceita http://localhost). Salvar como credentials/client_secrets_web.json.

Cada cliente tem seu próprio arquivo de token em
credentials/tokens/{client_id}_youtube.json — nunca mistura com o
credentials/token.json do canal principal nem com os token_<locale>.json
dos pipelines internacionais.
"""
import os
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

import db

SCOPES = ["https://www.googleapis.com/auth/youtube"]
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_SECRETS_FILE = os.path.join(_BASE_DIR, "credentials", "client_secrets_web.json")
TOKENS_DIR = os.path.join(_BASE_DIR, "credentials", "tokens")

REDIRECT_URI = os.getenv("YOUTUBE_OAUTH_REDIRECT_URI", "https://youtube-dark.duckdns.org/youtube/callback")


def token_path_for_client(client_id: str) -> str:
    return os.path.join(TOKENS_DIR, f"{client_id}_youtube.json")


def get_authorization_url(client_id: str) -> tuple[str, str]:
    """
    Gera a URL de autorização do Google pra este cliente conectar o canal
    dele, e grava o state em oauth_states (não numa variável global — N
    clientes podem estar no meio do fluxo ao mesmo tempo). Retorna (url, state).
    """
    if not os.path.exists(WEB_SECRETS_FILE):
        raise FileNotFoundError(
            f"'{WEB_SECRETS_FILE}' não encontrado.\n"
            "Crie um client OAuth tipo 'Web application' no Google Cloud "
            "Console e salve as credenciais nesse caminho."
        )
    flow = Flow.from_client_secrets_file(WEB_SECRETS_FILE, scopes=SCOPES, redirect_uri=REDIRECT_URI)
    auth_url, state = flow.authorization_url(
        access_type="offline",       # necessário pra receber refresh_token
        include_granted_scopes="true",
        prompt="consent",            # força tela de consentimento sempre (garante refresh_token novo)
    )
    db.create_oauth_state(state, client_id, provider="youtube")
    return auth_url, state


def exchange_code_for_token(code: str, client_id: str) -> Credentials:
    """Troca o código do callback pelo token e salva no arquivo do cliente."""
    flow = Flow.from_client_secrets_file(WEB_SECRETS_FILE, scopes=SCOPES, redirect_uri=REDIRECT_URI)
    flow.fetch_token(code=code)
    creds = flow.credentials

    token_path = token_path_for_client(client_id)
    os.makedirs(os.path.dirname(token_path), exist_ok=True)
    with open(token_path, "w", encoding="utf-8") as f:
        f.write(creds.to_json())
    return creds


def fetch_channel_identity(creds: Credentials) -> dict:
    """Retorna {'channel_id': ..., 'channel_title': ...} do canal autorizado."""
    svc = build("youtube", "v3", credentials=creds)
    resp = svc.channels().list(part="snippet", mine=True).execute()
    items = resp.get("items", [])
    if not items:
        raise RuntimeError("Nenhum canal encontrado para esta conta Google.")
    channel = items[0]
    return {
        "channel_id": channel["id"],
        "channel_title": channel["snippet"]["title"],
    }


def disconnect_client(client_id: str) -> None:
    """Remove o token salvo do cliente (não revoga do lado do Google — só localmente)."""
    token_path = token_path_for_client(client_id)
    if os.path.exists(token_path):
        os.remove(token_path)
