"""
Gera YouTube Shorts verticais (1080×1920) a partir das principais notícias do dia.
Duração: ~50s por Short — compatível com o algoritmo de Shorts do YouTube.
"""
from __future__ import annotations
import argparse
import asyncio
import os
import sys
import numpy as np
from datetime import datetime
from PIL import Image, ImageDraw
from moviepy.editor import AudioFileClip, ImageClip
from dotenv import load_dotenv

from fetcher import fetch_latest_news, select_unique_news
from audio import clean_text, _stream_to_bytes
from video import _get_font, _search_pexels, _build_pexels_query, CATEGORY_COLORS, DEFAULT_COLOR
from config import AUDIO_OUTPUT_DIR, CHANNEL_NAME

load_dotenv()

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

SHORTS_W, SHORTS_H = 1080, 1920
FPS = 30
MAX_WORDS_SHORT = 70       # ~29s de fala + margem de segurança ≤ 58s no total
MAX_SHORTS_PER_RUN = 3
SHORTS_OUTPUT_DIR = "./shorts_videos"


# ---------------------------------------------------------------------------
# Summarizer dedicado para Shorts (prompt mais conciso)
# ---------------------------------------------------------------------------

def _summarize_for_short(title: str, category: str, content: str) -> str:
    """Gera um texto de ~60 palavras otimizado para Shorts (impacto imediato)."""
    try:
        from google import genai
        import os
        client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
        prompt = (
            f"Você é um apresentador de notícias no estilo TikTok/Shorts — direto, impactante e sem rodeios.\n"
            f"Escreva UM parágrafo de no máximo 60 palavras sobre a notícia abaixo.\n"
            f"NÃO use markdown, asteriscos ou símbolos. Apenas texto simples em português.\n"
            f"Comece com a frase mais impactante — prenda a atenção imediatamente.\n\n"
            f"Categoria: {category}\n"
            f"Título: {title}\n"
            f"Conteúdo: {content[:600]}\n"
        )
        resp = client.models.generate_content(model="gemini-2.0-flash", contents=prompt)
        text = resp.text.strip()
        # Garante no máximo MAX_WORDS_SHORT palavras
        words = clean_text(text).split()
        return " ".join(words[:MAX_WORDS_SHORT])
    except Exception as e:
        print(f"  Gemini Shorts falhou: {e}. Usando título como fallback.")
        words = clean_text(title).split()
        return " ".join(words[:MAX_WORDS_SHORT])


# ---------------------------------------------------------------------------
# Layout visual vertical
# ---------------------------------------------------------------------------

def _crop_portrait(image: Image.Image) -> Image.Image:
    """Recorta e redimensiona imagem para 1080×1920 (9:16)."""
    target_ratio = SHORTS_W / SHORTS_H   # 9/16 ≈ 0.5625
    iw, ih = image.size
    img_ratio = iw / ih
    if img_ratio > target_ratio:
        # imagem mais larga — corta nas laterais
        nw = int(ih * target_ratio)
        left = (iw - nw) // 2
        image = image.crop((left, 0, left + nw, ih))
    else:
        # imagem mais alta — corta em cima/baixo
        nh = int(iw / target_ratio)
        top = (ih - nh) // 2
        image = image.crop((0, top, iw, top + nh))
    return image.resize((SHORTS_W, SHORTS_H), Image.LANCZOS)


def _dark_overlay(image: Image.Image) -> np.ndarray:
    """Aplica escurecimento + gradiente para legibilidade do texto."""
    arr = np.array(image).astype(float) * 0.35
    base = Image.fromarray(arr.astype(np.uint8)).convert("RGBA")

    grad = Image.new("RGBA", (SHORTS_W, SHORTS_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(grad)
    # Gradiente superior (top 35%)
    for y in range(int(SHORTS_H * 0.35)):
        alpha = int(180 * (1 - y / (SHORTS_H * 0.35)))
        draw.line([(0, y), (SHORTS_W, y)], fill=(0, 0, 0, alpha))
    # Gradiente inferior (bottom 50%)
    start = int(SHORTS_H * 0.50)
    for y in range(start, SHORTS_H):
        alpha = int(220 * (y - start) / (SHORTS_H - start))
        draw.line([(0, y), (SHORTS_W, y)], fill=(0, 0, 0, alpha))

    return np.array(Image.alpha_composite(base, grad).convert("RGB"))


def _wrap_text(draw, text, font, max_width):
    words = text.split()
    lines, current = [], []
    for word in words:
        test = " ".join(current + [word])
        if draw.textbbox((0, 0), test, font=font)[2] <= max_width:
            current.append(word)
        else:
            if current:
                lines.append(" ".join(current))
            current = [word]
    if current:
        lines.append(" ".join(current))
    return lines


def _render_short_frame(
    bg_arr: np.ndarray,
    title: str,
    summary: str,
    category: str,
    source: str,
) -> np.ndarray:
    """Renderiza frame vertical com título, resumo e elementos de UI."""
    color = CATEGORY_COLORS.get(category, DEFAULT_COLOR)
    r, g, b = color

    base = Image.fromarray(bg_arr).convert("RGBA")
    overlay = Image.new("RGBA", (SHORTS_W, SHORTS_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    padding = 60
    max_w = SHORTS_W - padding * 2

    # --- Badge de categoria (topo) ---
    f_badge = _get_font(44, bold=True)
    badge_text = f" {category.upper()} "
    bbox = draw.textbbox((0, 0), badge_text, font=f_badge)
    bw, bh = bbox[2] + 32, bbox[3] + 20
    badge_y = 90
    draw.rounded_rectangle(
        [(padding, badge_y), (padding + bw, badge_y + bh)],
        radius=14, fill=(r, g, b, 230),
    )
    draw.text((padding + 16, badge_y + 10), badge_text.strip(), font=f_badge, fill=(255, 255, 255, 255))

    # --- Nome do canal (topo direito) ---
    f_channel = _get_font(36)
    ch_bbox = draw.textbbox((0, 0), CHANNEL_NAME, font=f_channel)
    cx = SHORTS_W - ch_bbox[2] - padding
    draw.text((cx, badge_y + 12), CHANNEL_NAME, font=f_channel, fill=(220, 220, 220, 180))

    # --- Data (abaixo do badge) ---
    f_date = _get_font(32)
    date_str = datetime.now().strftime("%d/%m/%Y")
    draw.text((padding, badge_y + bh + 16), date_str, font=f_date, fill=(180, 180, 180, 160))

    # --- Título (zona central, 40% da altura) ---
    f_title = _get_font(72, bold=True)
    title_lines = _wrap_text(draw, title, f_title, max_w)[:4]
    title_block_h = len(title_lines) * 86
    title_y = int(SHORTS_H * 0.40) - title_block_h // 2

    for i, line in enumerate(title_lines):
        # Sombra de texto
        draw.text((padding + 3, title_y + i * 86 + 3), line, font=f_title, fill=(0, 0, 0, 180))
        draw.text((padding, title_y + i * 86), line, font=f_title, fill=(255, 255, 255, 255))

    # --- Separador colorido ---
    sep_y = title_y + title_block_h + 24
    draw.rectangle([(padding, sep_y), (padding + 120, sep_y + 6)], fill=(r, g, b, 255))

    # --- Resumo (abaixo do separador) ---
    f_summary = _get_font(46)
    summary_lines = _wrap_text(draw, summary, f_summary, max_w)[:6]
    sum_y = sep_y + 30
    for i, line in enumerate(summary_lines):
        draw.text((padding + 2, sum_y + i * 58 + 2), line, font=f_summary, fill=(0, 0, 0, 160))
        draw.text((padding, sum_y + i * 58), line, font=f_summary, fill=(230, 230, 230, 220))

    # --- Fonte (rodapé) ---
    f_source = _get_font(34)
    source_text = f"📰 {source}"
    src_bbox = draw.textbbox((0, 0), source_text, font=f_source)
    src_y = SHORTS_H - 90
    draw.text((padding, src_y), source_text, font=f_source, fill=(180, 180, 180, 180))

    # --- Ícone Shorts (rodapé direito) ---
    shorts_tag = "#Shorts"
    st_bbox = draw.textbbox((0, 0), shorts_tag, font=f_source)
    draw.text(
        (SHORTS_W - st_bbox[2] - padding, src_y),
        shorts_tag, font=f_source, fill=(r, g, b, 220),
    )

    merged = Image.alpha_composite(base, overlay)
    return np.array(merged.convert("RGB"))


# ---------------------------------------------------------------------------
# Pipeline principal
# ---------------------------------------------------------------------------

async def generate_short(item: dict, upload: bool = True, privacy: str = "public") -> str | None:
    """
    Gera um Short vertical de ~50s para um item de notícia.
    Retorna o caminho do vídeo gerado (ou None em caso de falha).
    """
    os.makedirs(SHORTS_OUTPUT_DIR, exist_ok=True)
    os.makedirs(AUDIO_OUTPUT_DIR, exist_ok=True)

    title = item["title"]
    category = item.get("category", "Notícias")
    source = item.get("source", "")
    content = item.get("_content") or item.get("summary", "")

    print(f"\n  Short: {title[:60]}...")

    # 1. Resumo curto
    print("  [1/4] Resumindo para Shorts...")
    summary = _summarize_for_short(title, category, content)
    narration = f"{title}. {summary}"

    # 2. Áudio TTS
    print("  [2/4] Gerando áudio TTS...")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    audio_filename = f"short_{ts}.mp3"
    audio_path = os.path.join(AUDIO_OUTPUT_DIR, audio_filename)
    data = await _stream_to_bytes(narration)
    if not data:
        print("  TTS falhou — pulando este Short.")
        return None
    with open(audio_path, "wb") as f:
        f.write(data)

    audio_clip = AudioFileClip(audio_path)
    duration = min(audio_clip.duration, 58.0)  # YouTube Shorts: máx 60s

    # 3. Imagem de fundo Pexels — portrait para melhor cobertura do frame 9:16
    print("  [3/4] Buscando imagem Pexels (portrait)...")
    query = _build_pexels_query(title, category)
    pil_img, _ = _search_pexels(query, orientation="portrait")
    if pil_img is None:
        # fallback: landscape também funciona — o crop vai cortar as laterais
        pil_img, _ = _search_pexels(query, orientation="landscape")
    if pil_img:
        portrait = _crop_portrait(pil_img)
        bg_arr = _dark_overlay(portrait)
    else:
        # Fallback: fundo sólido escuro com cor da categoria
        color = CATEGORY_COLORS.get(category, DEFAULT_COLOR)
        bg_solid = Image.new("RGB", (SHORTS_W, SHORTS_H), tuple(int(c * 0.25) for c in color))
        bg_arr = np.array(bg_solid)

    # 4. Renderizar frame e montar vídeo
    print("  [4/4] Montando vídeo vertical...")
    frame = _render_short_frame(bg_arr, title, summary, category, source)

    video_clip = (
        ImageClip(frame)
        .set_duration(duration)
        .set_fps(FPS)
        .set_audio(audio_clip.subclip(0, duration))
    )

    output_path = os.path.join(SHORTS_OUTPUT_DIR, f"Short_{category}_{ts}.mp4")
    video_clip.write_videofile(
        output_path,
        fps=FPS,
        codec="libx264",
        audio_codec="aac",
        preset="fast",
        verbose=False,
        logger=None,
    )
    video_clip.close()
    audio_clip.close()

    print(f"  Vídeo salvo: {output_path} ({duration:.1f}s)")

    # 5. Upload
    if upload:
        from uploader import upload_video
        from playlists import add_to_playlist
        yt_title = f"{title[:80]} #Shorts"
        yt_desc = (
            f"{summary}\n\n"
            f"Fonte: {source}\n"
            f"📰 {CHANNEL_NAME} — notícias rápidas em formato Shorts\n\n"
            "#Shorts #Notícias #Brasil #NewsApp"
        )
        yt_tags = ["shorts", "notícias", "brasil", "resumo", category.lower(), "newsapp"]
        try:
            video_id = upload_video(output_path, yt_title, yt_desc, yt_tags, privacy=privacy)
            print(f"  YouTube Shorts: https://youtu.be/{video_id}")
            add_to_playlist(video_id, "noticias")
            try:
                os.remove(output_path)
            except Exception:
                pass
            return video_id
        except Exception as e:
            print(f"  Erro no upload: {e}")

    return output_path


async def run_shorts_pipeline(
    max_shorts: int = MAX_SHORTS_PER_RUN,
    upload: bool = True,
    privacy: str = "public",
):
    """Busca as principais notícias do dia e gera um Short para cada uma."""
    print(f"\n=== Pipeline de Shorts — {datetime.now().strftime('%d/%m/%Y %H:%M')} ===")

    raw = fetch_latest_news(limit=3)
    items = select_unique_news(raw)[:max_shorts]

    if not items:
        print("Nenhuma notícia encontrada.")
        return

    from fetcher import extract_article_content
    for item in items:
        if not item.get("_content"):
            item["_content"] = extract_article_content(item["link"]) or item.get("summary", "")

    print(f"Gerando {len(items)} Short(s)...")
    for item in items:
        await generate_short(item, upload=upload, privacy=privacy)

    print("\n=== Shorts concluídos! ===")
    from telegram_notifier import notify
    notify(f"✅ <b>Shorts concluídos!</b>\n{len(items)} Short(s) gerado(s).")


# ---------------------------------------------------------------------------
# Short a partir de vídeo local (gerado pelo pipeline principal)
# ---------------------------------------------------------------------------

def generate_short_from_video(
    video_path: str,
    title: str,
    items: list[dict],
    upload: bool = True,
    privacy: str = "public",
    duration_s: float = 55.0,
) -> str | None:
    """
    Corta os primeiros `duration_s` segundos do vídeo landscape e converte
    para 1080×1920 portrait com blur background (estilo TikTok/Reels).
    Faz upload como Short logo após o vídeo principal.

    Retorna o video_id do Short ou None em caso de falha.
    """
    import subprocess
    from uploader import upload_video
    from playlists import add_to_playlist

    os.makedirs(SHORTS_OUTPUT_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = os.path.join(SHORTS_OUTPUT_DIR, f"Short_clip_{ts}.mp4")

    print(f"\n[Short] Convertendo clipe dos primeiros {duration_s:.0f}s para vertical...")

    # Blur background: vídeo original centralizado sobre versão embaçada de si mesmo
    filter_graph = (
        f"[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
        f"crop=1080:1920,boxblur=25:5[bg];"
        f"[0:v]scale=1080:-2[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2"
    )
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-t", str(duration_s),
        "-filter_complex", filter_graph,
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        output_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"  ffmpeg erro: {result.stderr[-300:]}")
            return None
    except Exception as e:
        print(f"  Erro ao gerar clipe vertical: {e}")
        return None

    size_mb = os.path.getsize(output_path) / 1024 / 1024
    print(f"  Clipe vertical salvo: {output_path} ({size_mb:.1f} MB)")

    if not upload:
        return output_path

    # Upload do Short
    category = items[0].get("category", "Notícias") if items else "Notícias"
    yt_title = f"{title[:80]} #Shorts"
    yt_desc = (
        f"Trecho do resumo de notícias — {datetime.now().strftime('%d/%m/%Y')}\n\n"
        + "\n".join(f"• {it.get('title', '')}" for it in items[:3])
        + f"\n\n📰 {CHANNEL_NAME}\n#Shorts #Notícias #Brasil"
    )
    yt_tags = ["shorts", "notícias", "brasil", "resumo", category.lower()]

    try:
        video_id = upload_video(output_path, yt_title, yt_desc, yt_tags, privacy=privacy)
        print(f"  YouTube Short: https://youtu.be/{video_id}")
        add_to_playlist(video_id, "noticias")
        try:
            os.remove(output_path)
        except Exception:
            pass
        return video_id
    except Exception as e:
        print(f"  Erro no upload do Short: {e}")
        return None


# ---------------------------------------------------------------------------
# Shorts de vídeos já postados no canal
# ---------------------------------------------------------------------------

def _list_channel_videos(max_results: int = 10) -> list[dict]:
    """
    Lista os vídeos mais recentes do canal via YouTube Data API.
    Retorna lista de dicts com id, title, description.
    """
    try:
        from uploader import get_youtube_service
        yt = get_youtube_service()

        # Descobre o uploads playlist ID do canal
        ch_resp = yt.channels().list(part="contentDetails", mine=True).execute()
        uploads_id = ch_resp["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]

        videos = []
        page_token = None
        while len(videos) < max_results:
            pl_resp = yt.playlistItems().list(
                part="snippet",
                playlistId=uploads_id,
                maxResults=min(max_results - len(videos), 50),
                pageToken=page_token,
            ).execute()

            for item in pl_resp.get("items", []):
                sn = item["snippet"]
                title = sn.get("title", "")
                # Exclui Shorts já criados (evita loop infinito)
                if "#Shorts" in title or "Short" in title:
                    continue
                videos.append({
                    "id":          sn["resourceId"]["videoId"],
                    "title":       title,
                    "description": sn.get("description", ""),
                    "category":    _guess_category(title),
                    "source":      "YouTube",
                })

            page_token = pl_resp.get("nextPageToken")
            if not page_token or len(videos) >= max_results:
                break

        return videos[:max_results]
    except Exception as e:
        print(f"  Erro ao listar vídeos do canal: {e}")
        return []


def _guess_category(title: str) -> str:
    """Infere categoria a partir de palavras-chave no título."""
    title_l = title.lower()
    if any(w in title_l for w in ["polít", "governo", "câmara", "senado", "eleição", "presidente"]):
        return "Política"
    if any(w in title_l for w in ["fute", "sport", "copa", "campeão", "gol", "futebol", "nba", "basquete"]):
        return "Esporte"
    if any(w in title_l for w in ["dólar", "ibovespa", "bolsa", "economia", "inflação", "mercado", "pib"]):
        return "Mercado Financeiro"
    if any(w in title_l for w in ["celular", "ia ", "inteligência", "tecnologia", "iphone", "google", "meta"]):
        return "Tecnologia"
    if any(w in title_l for w in ["crime", "polícia", "preso", "assassin", "tráfico", "roubo", "operação"]):
        return "Policial"
    if any(w in title_l for w in ["ator", "atriz", "celebridade", "show", "música", "filme", "série"]):
        return "Entretenimento"
    return "Notícias"


async def run_shorts_from_existing(
    max_videos: int = 5,
    upload: bool = True,
    privacy: str = "public",
):
    """
    Gera Shorts a partir de vídeos já publicados no canal.
    Usa título + descrição como base para o resumo TTS e imagem Pexels nova.
    """
    print(f"\n=== Shorts de vídeos existentes — {datetime.now().strftime('%d/%m/%Y %H:%M')} ===")
    print(f"  Buscando {max_videos} vídeo(s) do canal...")

    videos = _list_channel_videos(max_results=max_videos)
    if not videos:
        print("  Nenhum vídeo encontrado no canal.")
        return

    print(f"  {len(videos)} vídeo(s) encontrado(s).")
    for v in videos:
        # Monta item compatível com generate_short()
        item = {
            "title":    v["title"],
            "category": v["category"],
            "source":   f"youtu.be/{v['id']}",
            "_content": v["description"][:600] or v["title"],
        }
        await generate_short(item, upload=upload, privacy=privacy)

    print("\n=== Shorts de vídeos existentes concluídos! ===")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog="shorts.py", description="Gera YouTube Shorts de notícias.")
    parser.add_argument("--quantidade", "-n", type=int, default=MAX_SHORTS_PER_RUN,
                        metavar="N", help=f"quantidade de Shorts (padrão: {MAX_SHORTS_PER_RUN})")
    parser.add_argument("--de-existentes", action="store_true",
                        help="gera Shorts a partir de vídeos já postados no canal")
    parser.add_argument("--sem-upload", action="store_true", help="gera vídeos sem fazer upload")
    parser.add_argument("--privado",    action="store_true", help="sobe como privado")
    args = parser.parse_args()

    privacy = "private" if args.privado else "public"
    upload  = not args.sem_upload

    if args.de_existentes:
        asyncio.run(run_shorts_from_existing(
            max_videos=args.quantidade,
            upload=upload,
            privacy=privacy,
        ))
    else:
        asyncio.run(run_shorts_pipeline(
            max_shorts=args.quantidade,
            upload=upload,
            privacy=privacy,
        ))
