import os
import pickle
import socket
import sys
import time
from datetime import datetime, timedelta, timezone
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError

SCOPES = ["https://www.googleapis.com/auth/youtube"]  # cobre upload + playlists
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TOKEN_FILE = os.path.join(_BASE_DIR, "credentials", "token.json")
SECRETS_FILE = os.path.join(_BASE_DIR, "credentials", "client_secrets.json")

YOUTUBE_CATEGORY_NEWS = "25"  # Notícias e Política


def _is_headless():
    """True quando não há display disponível (VPS/servidor Linux sem GUI)."""
    if os.name == "nt":
        return False
    return not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY")


def _get_credentials():
    creds = None
    if os.path.exists(TOKEN_FILE):
        try:
            # Formato JSON (atual)
            creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
        except Exception:
            # Migração: arquivo ainda está no formato pickle antigo
            try:
                with open(TOKEN_FILE, "rb") as f:
                    creds = pickle.load(f)
                # Converte imediatamente para JSON
                if creds:
                    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
                        f.write(creds.to_json())
            except Exception:
                creds = None
        # Re-autentica automaticamente se o token não cobre o escopo atual
        if creds and creds.scopes and not set(SCOPES).issubset(set(creds.scopes)):
            print("  Escopos OAuth desatualizados — reautenticando...")
            creds = None
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                print(f"  Refresh token expirado/revogado: {e}")
                print("  Reautenticando via browser...")
                creds = None
        if not creds or not creds.valid:
            # Sem terminal interativo → falhar rápido (evita travar subprocess)
            if not sys.stdin or not sys.stdin.isatty():
                raise RuntimeError(
                    "Token YouTube expirado/revogado. Reautenticação interativa "
                    "não disponível (sem terminal).\n"
                    "Pra corrigir:\n"
                    "1. No PC com browser, apague credentials/token.json\n"
                    "2. Rode: python -c \"from uploader import _get_credentials; _get_credentials()\"\n"
                    "3. Faça login no Google quando o browser abrir\n"
                    "4. Copie o novo token.json pra VPS: scp credentials/token.json rocky@<vps>:~/news-app/credentials/"
                )
            if not os.path.exists(SECRETS_FILE):
                raise FileNotFoundError(
                    f"Arquivo '{SECRETS_FILE}' não encontrado.\n"
                    "Baixe em: Google Cloud Console → Credenciais → OAuth 2.0 → Aplicativo para desktop"
                )
            flow = InstalledAppFlow.from_client_secrets_file(SECRETS_FILE, SCOPES)
            if _is_headless():
                # Ambiente sem display: exibe URL e aguarda código no terminal
                print("\n  Autenticação YouTube necessária.")
                print("  Abra a URL abaixo em qualquer browser e cole o código aqui:\n")
                creds = flow.run_console()
            else:
                creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "w", encoding="utf-8") as f:
            f.write(creds.to_json())
    return creds


def check_youtube_token() -> tuple[bool, str]:
    """
    Verifica se o token YouTube está válido sem tentar renovar.
    Retorna (ok, mensagem_curta).
    """
    if not os.path.exists(TOKEN_FILE):
        return False, "token.json não encontrado em credentials/"
    try:
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
        if creds.valid:
            return True, "Token válido"
        if creds.refresh_token:
            return True, "Token será renovado automaticamente no upload"
        return False, 'Token revogado. Atualize com: python -c "from uploader import _get_credentials; _get_credentials()"'
    except Exception:
        return False, 'Token inválido. Atualize com: python -c "from uploader import _get_credentials; _get_credentials()"'


def get_youtube_service():
    """Retorna instância autenticada do YouTube Data API v3."""
    return build("youtube", "v3", credentials=_get_credentials())


def upload_video(video_path, title, description, tags=None, publish_at_hour=6, privacy="private"):
    """
    Faz upload do vídeo para o YouTube e agenda a publicação.

    privacy="private"  → vídeo fica privado até publish_at_hour (recomendado para testes)
    privacy="public"   → publicado imediatamente
    privacy="scheduled" → agenda para publish_at_hour no dia seguinte (requer privacyStatus="private" + publishAt)

    Retorna o ID do vídeo no YouTube.
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Vídeo não encontrado: {video_path}")

    title = _sanitize_yt(title)
    description = _sanitize_yt(description)
    if len(description) > 4900:
        description = description[:4900] + "\n..."

    creds = _get_credentials()
    youtube = build("youtube", "v3", credentials=creds)

    # Calcula horário agendado (se for antes do horário alvo de hoje, usa amanhã)
    now = datetime.now()
    scheduled_local = now.replace(hour=publish_at_hour, minute=0, second=0, microsecond=0)
    if scheduled_local <= now:
        scheduled_local += timedelta(days=1)
    publish_at_utc = scheduled_local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    status_body = {"privacyStatus": "private", "publishAt": publish_at_utc}
    if privacy == "public":
        status_body = {"privacyStatus": "public"}

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags or [],
            "categoryId": YOUTUBE_CATEGORY_NEWS,
            "defaultLanguage": "pt",
            "defaultAudioLanguage": "pt",
        },
        "status": status_body,
    }

    media = MediaFileUpload(
        video_path,
        chunksize=4 * 1024 * 1024,  # 4 MB por chunk
        resumable=True,
        mimetype="video/mp4",
    )

    print(f"\nIniciando upload para o YouTube...")
    if privacy != "public":
        print(f"  Agendado para: {scheduled_local.strftime('%d/%m/%Y às %H:%M')}")

    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
    )

    response = None
    max_retries = 10
    retry_delay = 5  # segundos (dobra a cada falha)
    while response is None:
        try:
            status, response = request.next_chunk()
            if status:
                pct = int(status.progress() * 100)
                print(f"  Upload: {pct}%", end="\r")
            retry_delay = 5  # reset após chunk bem-sucedido
        except (OSError, ConnectionError, TimeoutError, socket.timeout, HttpError) as e:
            if max_retries <= 0:
                _log_yt_error(e, "upload de chunk", esgotou_retries=True)
                raise
            is_retryable = isinstance(e, (OSError, ConnectionError, TimeoutError, socket.timeout)) or (
                isinstance(e, HttpError) and e.resp.status in (429, 500, 502, 503, 504)
            )
            if not is_retryable:
                _log_yt_error(e, "upload de chunk")
                raise
            max_retries -= 1
            print(f"\n  ⚠️ Falha de conexão ({type(e).__name__}: {e}). Retry em {retry_delay}s... ({max_retries} restantes)")
            time.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 120)

    video_id = response["id"]
    print(f"  Upload: 100% — ✅ concluído!")
    print(f"  URL: https://youtu.be/{video_id}")
    return video_id


def _log_yt_error(err, contexto: str, esgotou_retries: bool = False):
    """Imprime diagnóstico amigável de erro do YouTube."""
    print(f"\n  ❌ YouTube falhou em: {contexto}")
    print(f"     Tipo: {type(err).__name__}")
    msg = str(err).lower()

    if esgotou_retries:
        print(f"     Esgotou todas as tentativas de retry")

    if "quotaexceeded" in msg or "quota" in msg:
        print("     CAUSA: Cota diária do YouTube esgotada (limite ~6 uploads/dia)")
        print("     FIX:   Aguardar reset (meia-noite PT) ou pedir aumento de quota")
    elif "invaliddescription" in msg or "invalid video description" in msg:
        print("     CAUSA: Descrição contém caracteres rejeitados (< > ou tags HTML)")
        print("     FIX:   _sanitize_yt() deveria ter limpado — verifique build_description")
    elif "invalidtitle" in msg or "invalid video title" in msg:
        print("     CAUSA: Título excede 100 chars ou tem caracteres inválidos")
    elif "invalid_grant" in msg or "token has been revoked" in msg or "expired" in msg:
        print("     CAUSA: Token OAuth revogado/expirado")
        print("     FIX:   Gerar novo token.json no PC e copiar pra VPS (scp)")
    elif "forbidden" in msg or "permission" in msg:
        print("     CAUSA: Permissão negada — verifique scopes do OAuth ou status do canal")
    elif "uploadlimit" in msg:
        print("     CAUSA: Limite de uploads atingido para esta hora")
    elif "videoiduploadtolarge" in msg or "request entity too large" in msg:
        print("     CAUSA: Vídeo excedeu limite de tamanho (~128 GB)")
    elif "5" in msg[:3]:  # 5xx errors
        print("     CAUSA: YouTube com problema interno (5xx) — geralmente passa em minutos")
    else:
        print(f"     Mensagem: {err}")


def upload_thumbnail(video_id: str, thumb_path: str) -> bool:
    """Faz upload da thumbnail para o vídeo já publicado no YouTube."""
    if not os.path.exists(thumb_path):
        print(f"  Thumbnail não encontrada: {thumb_path}")
        return False
    try:
        youtube = get_youtube_service()
        youtube.thumbnails().set(
            videoId=video_id,
            media_body=MediaFileUpload(thumb_path, mimetype="image/jpeg"),
        ).execute()
        print(f"  Thumbnail enviada para https://youtu.be/{video_id}")
        return True
    except Exception as e:
        print(f"  Erro ao enviar thumbnail: {e}")
        return False


def _shorten_url(url: str) -> str:
    """Remove parâmetros e fragmentos desnecessários para encurtar a URL."""
    from urllib.parse import urlparse, urlunparse
    parsed = urlparse(url)
    # Remove query string e fragmento — deixa só o path limpo
    clean = urlunparse((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", "", ""))
    return clean


def _sanitize_yt(text: str) -> str:
    """Remove caracteres que o YouTube rejeita na descrição (< e > de tags HTML do RSS)."""
    import re
    text = re.sub(r"<[^>]*>", "", text)
    return text.replace("<", "").replace(">", "").strip()


def build_description(news_items, date_str):
    """Gera descrição automática com as categorias, fontes e links do episódio."""
    lines = [
        f"Resumo automático de notícias do dia {date_str}.",
        "",
        "Neste episódio:",
    ]
    for item in news_items:
        cat = _sanitize_yt(item.get("category", "Notícia"))
        title = _sanitize_yt(item.get("title", ""))
        source = _sanitize_yt(item.get("source", ""))
        link = item.get("link", "")
        line = f"• [{cat}] {title} — {source}"
        if link:
            line += f"\n{_shorten_url(link)}"
        lines.append(line)
    lines += [
        "",
        "Para ver cada notícia na íntegra, clique nos links acima.",
        "",
        "Notícias coletadas automaticamente de fontes públicas brasileiras.",
        "#noticias #brasil #resumodenoticias",
    ]
    desc = "\n".join(lines)
    if len(desc) > 4900:
        desc = desc[:4900] + "\n..."
    return desc
