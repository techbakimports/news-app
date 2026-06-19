"""Gerenciamento de playlists do YouTube."""
from __future__ import annotations
import json
import os
from googleapiclient.errors import HttpError

PLAYLISTS_FILE = "playlists.json"

PLAYLIST_DEFS = {
    "noticias": {
        "title": "Notícias do Dia",
        "description": "Resumos automáticos de notícias do Brasil, gerados diariamente.",
        "keywords": ["resumo de notícias", "notícias —"],
    },
    "tech": {
        "title": "Notícias de Tecnologia 💻",
        "description": "Resumo diário das principais notícias de tecnologia do Brasil e do mundo.",
        "keywords": ["noticias de tecnologia", "tech news"],
    },
    "curiosidades": {
        "title": "Curiosidades 🧠",
        "description": "Curiosidades aleatórias, fatos surpreendentes e descobertas — em formato Short.",
        "keywords": ["curiosidade", "você sabia", "fato curioso"],
    },
    "celebridades": {
        "title": "Celebridades e Fofoca 🌟",
        "description": "Fofocas, entretenimento e novidades dos famosos do Brasil, em formato Short.",
        "keywords": ["celebridades", "fofoca", "famosos"],
    },
    "novela": {
        "title": "Novela IA 🎭",
        "description": "Episódios diários de novela brasileira criados por inteligência artificial.",
        "keywords": ["novela ia", "novela brasileira", "drama ia"],
    },
    "rain": {
        "title": "Chuva para Dormir e Relaxar 🌧️",
        "description": "Horas de sons de chuva para dormir, estudar e relaxar.",
        "keywords": ["chuva relaxante"],
    },
    "ocean": {
        "title": "Ondas do Mar 🌊",
        "description": "Sons suaves de ondas do mar para meditação, sono e relaxamento.",
        "keywords": ["ondas do mar"],
    },
    "fire": {
        "title": "Lareira Aconchegante 🔥",
        "description": "Sons de lareira crepitante para criar uma atmosfera aconchegante.",
        "keywords": ["lareira aconchegante"],
    },
    "forest": {
        "title": "Sons da Floresta 🌿",
        "description": "Sons da natureza — vento e floresta — para relaxar e dormir.",
        "keywords": ["sons da floresta"],
    },
    "whitenoise": {
        "title": "Ruído Branco ⬜",
        "description": "Ruído branco para concentração profunda e sono reparador.",
        "keywords": ["ruído branco"],
    },
    "brownnoise": {
        "title": "Ruído Marrom 🟫",
        "description": "Ruído marrom para foco profundo e relaxamento.",
        "keywords": ["ruído marrom"],
    },
}


def _load_ids() -> dict:
    if os.path.exists(PLAYLISTS_FILE):
        with open(PLAYLISTS_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_ids(ids: dict) -> None:
    with open(PLAYLISTS_FILE, "w", encoding="utf-8") as f:
        json.dump(ids, f, ensure_ascii=False, indent=2)


def _build_youtube():
    from uploader import _get_credentials
    from googleapiclient.discovery import build
    return build("youtube", "v3", credentials=_get_credentials())


def _get_or_create_playlist(youtube, key: str) -> str:
    """Retorna ID da playlist, criando-a no YouTube se ainda não existir."""
    ids = _load_ids()
    if key in ids:
        return ids[key]

    defn = PLAYLIST_DEFS[key]
    resp = youtube.playlists().insert(
        part="snippet,status",
        body={
            "snippet": {
                "title": defn["title"],
                "description": defn["description"],
                "defaultLanguage": "pt",
            },
            "status": {"privacyStatus": "public"},
        },
    ).execute()

    playlist_id = resp["id"]
    ids[key] = playlist_id
    _save_ids(ids)
    print(f"  Playlist criada: {defn['title']}  ({playlist_id})")
    return playlist_id


def add_to_playlist(video_id: str, playlist_key: str) -> bool:
    """
    Adiciona video_id à playlist correspondente a playlist_key.
    Cria a playlist no YouTube se ela ainda não existir.
    Retorna True em sucesso (inclusive se já estava na playlist).
    """
    if playlist_key not in PLAYLIST_DEFS:
        print(f"  Chave de playlist desconhecida: {playlist_key!r}")
        return False

    youtube = _build_youtube()
    playlist_id = _get_or_create_playlist(youtube, playlist_key)

    try:
        youtube.playlistItems().insert(
            part="snippet",
            body={
                "snippet": {
                    "playlistId": playlist_id,
                    "resourceId": {"kind": "youtube#video", "videoId": video_id},
                }
            },
        ).execute()
        print(f"  Adicionado à playlist «{PLAYLIST_DEFS[playlist_key]['title']}»")
        return True
    except HttpError as e:
        msg = str(e).lower()
        if "duplicate" in msg or "playlistcontainsduplicates" in msg:
            return True  # já estava lá — ok
        print(f"  Erro ao adicionar à playlist: {e}")
        return False


def _match_key(title: str) -> str | None:
    """Detecta qual playlist um vídeo pertence com base no título."""
    t = title.lower()
    for key, defn in PLAYLIST_DEFS.items():
        for kw in defn["keywords"]:
            if kw.lower() in t:
                return key
    return None


def organize_existing_videos() -> None:
    """
    Lista todos os vídeos já publicados no canal e os organiza
    nas playlists corretas com base no título.
    """
    youtube = _build_youtube()

    # Descobrir a playlist "uploads" do canal (contém todos os vídeos publicados)
    ch = youtube.channels().list(part="contentDetails", mine=True).execute()
    upload_pl = ch["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
    print(f"  Carregando vídeos do canal...")

    videos: list[tuple[str, str]] = []
    page_token = None
    while True:
        kw: dict = {"part": "snippet", "playlistId": upload_pl, "maxResults": 50}
        if page_token:
            kw["pageToken"] = page_token
        resp = youtube.playlistItems().list(**kw).execute()
        for item in resp["items"]:
            snip = item["snippet"]
            videos.append((snip["resourceId"]["videoId"], snip["title"]))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    print(f"  {len(videos)} vídeo(s) encontrado(s).\n")

    added = errors = unmatched = 0
    for vid_id, title in videos:
        key = _match_key(title)
        if not key:
            print(f"  [?] {title[:65]}")
            unmatched += 1
            continue

        label = PLAYLIST_DEFS[key]["title"]
        print(f"  [{label}]  {title[:50]}")
        if add_to_playlist(vid_id, key):
            added += 1
        else:
            errors += 1

    print(f"\n  Concluído: {added} adicionados | {errors} erros | {unmatched} sem playlist")


if __name__ == "__main__":
    organize_existing_videos()
