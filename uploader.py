import os
import pickle
import socket
import time
from datetime import datetime, timedelta, timezone
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
TOKEN_FILE = "token.json"
SECRETS_FILE = "client_secrets.json"

YOUTUBE_CATEGORY_NEWS = "25"  # Notícias e Política


def _get_credentials():
    creds = None
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "rb") as f:
            creds = pickle.load(f)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(SECRETS_FILE):
                raise FileNotFoundError(
                    f"Arquivo '{SECRETS_FILE}' não encontrado.\n"
                    "Baixe em: Google Cloud Console → Credenciais → OAuth 2.0 → Aplicativo para desktop"
                )
            flow = InstalledAppFlow.from_client_secrets_file(SECRETS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "wb") as f:
            pickle.dump(creds, f)
    return creds


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
                raise
            is_retryable = isinstance(e, (OSError, ConnectionError, TimeoutError, socket.timeout)) or (
                isinstance(e, HttpError) and e.resp.status in (429, 500, 502, 503, 504)
            )
            if not is_retryable:
                raise
            max_retries -= 1
            print(f"\n  Falha de conexão ({e}). Tentando novamente em {retry_delay}s... ({max_retries} tentativas restantes)")
            time.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 120)

    video_id = response["id"]
    print(f"  Upload: 100% — concluído!")
    print(f"  URL: https://youtu.be/{video_id}")
    return video_id


def build_description(news_items, date_str):
    """Gera descrição automática com as categorias e fontes do episódio."""
    lines = [
        f"Resumo automático de notícias do dia {date_str}.",
        "",
        "Neste episódio:",
    ]
    for item in news_items:
        cat = item.get("category", "Notícia")
        title = item.get("title", "")
        source = item.get("source", "")
        lines.append(f"• [{cat}] {title} — {source}")
    lines += [
        "",
        "Notícias coletadas automaticamente de fontes públicas brasileiras.",
        "#noticias #brasil #resumodenoticias",
    ]
    return "\n".join(lines)
