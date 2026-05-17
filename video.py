import os
import io
import math
import re
import random
import requests
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from moviepy.editor import AudioFileClip, VideoClip, concatenate_videoclips
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

VIDEO_W, VIDEO_H = 1280, 720
FPS = 24
WORDS_PER_MINUTE = 145
VIDEO_OUTPUT_DIR = "./video_news"

# Termos em inglês para queries Pexels (resultados melhores que em português)
_CATEGORY_EN = {
    "Política":           "politics government",
    "Esporte":            "sports",
    "Entretenimento":     "entertainment celebrities",
    "Mercado Financeiro": "finance economy money",
    "Tecnologia":         "technology innovation",
    "Policial":           "police crime investigation",
}

# Palavras irrelevantes para filtrar do título ao montar a query
_PT_STOPWORDS = {
    "a", "o", "e", "de", "da", "do", "em", "no", "na", "com", "que",
    "se", "por", "para", "as", "os", "ao", "um", "uma", "mais", "mas",
    "é", "são", "foi", "seu", "sua", "ele", "ela", "eles", "elas",
    "como", "tem", "ter", "sobre", "após", "entre", "contra", "não",
    "dos", "das", "nos", "nas", "pelo", "pela", "pelos", "pelas",
    "ser", "está", "isso", "este", "esse", "esta", "essa", "também",
}


def _build_pexels_query(title: str, category: str) -> str:
    """Monta query de busca Pexels combinando categoria (EN) + palavras-chave do título."""
    cat_terms = _CATEGORY_EN.get(category, "news")
    words = [
        w for w in re.sub(r"[^\w\s]", " ", title).split()
        if w.lower() not in _PT_STOPWORDS and len(w) > 3
    ][:5]
    return f"{cat_terms} {' '.join(words)}"


CATEGORY_COLORS = {
    "Política": (220, 50, 50),
    "Esporte": (30, 130, 220),
    "Entretenimento": (180, 50, 220),
    "Mercado Financeiro": (30, 170, 80),
    "Tecnologia": (30, 180, 200),
    "Policial": (220, 120, 30),
}
DEFAULT_COLOR = (100, 100, 200)


def _get_font(size, bold=False):
    candidates = [
        # Windows
        "C:/Windows/Fonts/arialbd.ttf"   if bold else "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/calibrib.ttf"  if bold else "C:/Windows/Fonts/calibri.ttf",
        "C:/Windows/Fonts/segoeuib.ttf"  if bold else "C:/Windows/Fonts/segoeui.ttf",
        # Linux — Liberation (substituto do Arial)
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"    if bold else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        # Linux — DejaVu
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"            if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        # Linux — Ubuntu / Noto
        "/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf"                   if bold else "/usr/share/fonts/truetype/ubuntu/Ubuntu-R.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf"                if bold else "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                pass
    return ImageFont.load_default()


def _search_pexels(
    query: str,
    exclude_ids: set | None = None,
    orientation: str = "landscape",
) -> tuple[Image.Image | None, int | None]:
    """
    Busca uma imagem no Pexels.
    Retorna (imagem_PIL, photo_id) ou (None, None) em caso de falha.
    exclude_ids: conjunto de IDs já usados — evita repetição entre segmentos.
    orientation: "landscape" | "portrait" | "square"
    """
    api_key = os.getenv("PEXELS_API_KEY")
    if not api_key:
        return None, None
    if exclude_ids is None:
        exclude_ids = set()
    try:
        r = requests.get(
            "https://api.pexels.com/v1/search",
            headers={"Authorization": api_key},
            params={"query": query, "per_page": 10, "orientation": orientation},
            timeout=10,
        )
        photos = [p for p in r.json().get("photos", []) if p["id"] not in exclude_ids]
        if not photos:
            return None, None
        photo = random.choice(photos[:5])
        img_r = requests.get(photo["src"]["large2x"], timeout=15)
        return Image.open(io.BytesIO(img_r.content)).convert("RGB"), photo["id"]
    except Exception as e:
        print(f"  Erro Pexels ({query[:40]}): {e}")
        return None, None


def _fetch_pexels_with_fallback(title: str, category: str, exclude_ids: set) -> Image.Image | None:
    """
    Tenta três queries progressivamente mais amplas até encontrar uma imagem.
    Registra o ID usado em exclude_ids para não repetir em segmentos subsequentes.
    """
    queries = [
        _build_pexels_query(title, category),        # específico: categoria EN + palavras do título
        _CATEGORY_EN.get(category, "news"),           # só a categoria
        "brazil news journalism",                     # genérico último recurso
    ]
    for q in queries:
        img, photo_id = _search_pexels(q, exclude_ids)
        if img is not None:
            exclude_ids.add(photo_id)
            return img
    return None


def _prepare_bg(image, color):
    iw, ih = image.size
    target_ratio = VIDEO_W / VIDEO_H
    img_ratio = iw / ih
    if img_ratio > target_ratio:
        nw = int(ih * target_ratio)
        left = (iw - nw) // 2
        image = image.crop((left, 0, left + nw, ih))
    else:
        nh = int(iw / target_ratio)
        top = (ih - nh) // 2
        image = image.crop((0, top, iw, top + nh))
    image = image.resize((VIDEO_W, VIDEO_H), Image.LANCZOS)

    arr = np.array(image).astype(float) * 0.42
    image = Image.fromarray(arr.astype(np.uint8)).convert("RGBA")

    grad = Image.new("RGBA", (VIDEO_W, VIDEO_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(grad)
    start = int(VIDEO_H * 0.40)
    for y in range(start, VIDEO_H):
        alpha = int(210 * (y - start) / (VIDEO_H - start))
        draw.line([(0, y), (VIDEO_W, y)], fill=(0, 0, 0, alpha))
    image = Image.alpha_composite(image, grad)

    return np.array(image.convert("RGB"))


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
    return lines[:3]


def _render_static_layer(title, category, channel_name, color):
    overlay = Image.new("RGBA", (VIDEO_W, VIDEO_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    r, g, b = color

    f_badge = _get_font(22, bold=True)
    f_channel = _get_font(20)
    f_title = _get_font(52, bold=True)
    f_date = _get_font(18)

    # Category badge — top left
    badge = f" {category.upper()} "
    bbox = draw.textbbox((0, 0), badge, font=f_badge)
    bw, bh = bbox[2] + 24, bbox[3] + 16
    draw.rounded_rectangle([(40, 28), (40 + bw, 28 + bh)], radius=8, fill=(r, g, b, 230))
    draw.text((52, 36), badge.strip(), font=f_badge, fill=(255, 255, 255, 255))

    # Date — below badge
    date_str = datetime.now().strftime("%d/%m/%Y")
    draw.text((42, 28 + bh + 8), date_str, font=f_date, fill=(220, 220, 220, 180))

    # Channel name — top right
    ch_bbox = draw.textbbox((0, 0), channel_name, font=f_channel)
    cx = VIDEO_W - ch_bbox[2] - 40
    draw.text((cx + 1, 37), channel_name, font=f_channel, fill=(0, 0, 0, 120))
    draw.text((cx, 36), channel_name, font=f_channel, fill=(255, 255, 255, 200))

    # Title — bottom area
    lines = _wrap_text(draw, title, f_title, VIDEO_W - 100)
    lh = draw.textbbox((0, 0), "Ag", font=f_title)[3] + 14
    ty = VIDEO_H - 160 - len(lines) * lh
    for line in lines:
        draw.text((51, ty + 2), line, font=f_title, fill=(0, 0, 0, 180))
        draw.text((50, ty), line, font=f_title, fill=(255, 255, 255, 255))
        ty += lh

    return np.array(overlay)


def _alpha_composite(bg, overlay):
    alpha = overlay[:, :, 3:4].astype(float) / 255.0
    result = bg.astype(float) * (1 - alpha) + overlay[:, :, :3].astype(float) * alpha
    return result.astype(np.uint8)


def _make_intro_clip(channel_name, duration):
    """Slide de abertura com nome do canal — exibido durante o áudio da vinheta."""
    bg_arr = np.full((VIDEO_H, VIDEO_W, 3), (12, 16, 30), dtype=np.uint8)

    overlay = Image.new("RGBA", (VIDEO_W, VIDEO_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    f_channel = _get_font(68, bold=True)
    f_sub = _get_font(26)

    ch_bbox = draw.textbbox((0, 0), channel_name, font=f_channel)
    cx = (VIDEO_W - ch_bbox[2]) // 2
    cy = VIDEO_H // 2 - ch_bbox[3] // 2 - 20

    draw.text((cx + 2, cy + 2), channel_name, font=f_channel, fill=(0, 0, 0, 160))
    draw.text((cx, cy), channel_name, font=f_channel, fill=(255, 255, 255, 255))

    date_str = datetime.now().strftime("%d/%m/%Y")
    d_bbox = draw.textbbox((0, 0), date_str, font=f_sub)
    dx = (VIDEO_W - d_bbox[2]) // 2
    draw.text((dx, cy + ch_bbox[3] + 16), date_str, font=f_sub, fill=(180, 180, 180, 200))

    base = _alpha_composite(bg_arr, np.array(overlay))
    n_frames = int(math.ceil(duration * FPS))

    def make_frame(t):
        frame = base.copy()
        img = Image.fromarray(frame)
        draw2 = ImageDraw.Draw(img)
        progress = t / duration
        draw2.rectangle([(0, VIDEO_H - 5), (int(VIDEO_W * progress), VIDEO_H)], fill=(80, 120, 220))
        return np.array(img)

    return VideoClip(make_frame, duration=duration).set_fps(FPS)


def _gen_waveform(n_frames, bar_count=60):
    base = np.random.rand(bar_count) * 0.35 + 0.25
    base[0] = base[1] = base[-1] = base[-2] = 0.08
    frames = []
    for f in range(n_frames):
        t = f / FPS
        wave = (
            base
            + 0.22 * np.sin(np.linspace(0, 6 * np.pi, bar_count) + t * 2.8)
            + 0.10 * np.sin(np.linspace(0, 12 * np.pi, bar_count) + t * 5.3)
            + 0.07 * np.sin(np.linspace(0, 3 * np.pi, bar_count) - t * 1.9)
        )
        frames.append(np.clip(wave, 0.04, 1.0))
    return frames


def _prepare_segment_data(news_item, duration, channel_name, exclude_ids: set):
    """Pré-renderiza todos os dados estáticos de um segmento (imagem, waveform, overlay)."""
    title = news_item["title"]
    category = news_item.get("category", "Notícia")
    color = CATEGORY_COLORS.get(category, DEFAULT_COLOR)

    print(f"  Buscando imagem [{category}]: {_build_pexels_query(title, category)[:60]}")
    pil_img = _fetch_pexels_with_fallback(title, category, exclude_ids)
    if pil_img is None:
        pil_img = Image.new("RGB", (VIDEO_W, VIDEO_H), (18, 22, 38))

    bg = _prepare_bg(pil_img, color)
    text_layer = _render_static_layer(title, category, channel_name, color)
    base = _alpha_composite(bg, text_layer)

    n_frames = max(1, int(math.ceil(duration * FPS)))
    waveform = _gen_waveform(n_frames)

    return {"base": base, "waveform": waveform, "color": color, "n_frames": n_frames, "duration": duration}


def generate_video(news_items, audio_path, channel_name="NewsApp Brasil", output_filename=None, segment_durations=None, intro_duration=0.0):
    """
    Gera um vídeo usando um único VideoClip contínuo com timestamps cumulativos.
    Elimina o drift de sync causado pelo rounding de frames por segmento no MoviePy.
    """
    if not os.path.exists(VIDEO_OUTPUT_DIR):
        os.makedirs(VIDEO_OUTPUT_DIR)

    if not output_filename:
        ts = datetime.now().strftime("%Y%m%d_%H%M")
        output_filename = f"NewsVideo_{ts}.mp4"

    output_path = os.path.join(VIDEO_OUTPUT_DIR, output_filename)

    print(f"\nCarregando áudio: {audio_path}")
    audio = AudioFileClip(audio_path)
    total_duration = audio.duration
    print(f"Duração total: {total_duration:.1f}s ({total_duration / 60:.1f} min)")

    # Durações reais medidas por segmento (incluindo silêncio gerado por falha de TTS)
    # Não aplicar mínimo artificial — manter sync exato com o áudio
    if segment_durations and len(segment_durations) == len(news_items):
        durations = list(segment_durations)
        print(f"Durações por segmento: {[f'{d:.1f}s' for d in durations]}")
    else:
        summaries = [item.get("ai_summary") or item.get("summary") or item["title"] for item in news_items]
        word_counts = [max(1, len(s.split())) for s in summaries]
        total_words = sum(word_counts)
        durations = [total_duration * (wc / total_words) for wc in word_counts]

    # Pré-renderizar todos os segmentos (imagens, waveforms, overlays)
    print(f"\nPré-renderizando {len(news_items)} segmento(s)...")
    segments_data = []
    used_photo_ids: set = set()  # evita repetir a mesma foto em segmentos diferentes

    # Segmento 0: intro
    if intro_duration > 0:
        print(f"  Slide de abertura: {intro_duration:.1f}s")
        intro_n_frames = max(1, int(math.ceil(intro_duration * FPS)))
        segments_data.append({"type": "intro", "duration": intro_duration, "n_frames": intro_n_frames})

    for i, (item, dur) in enumerate(zip(news_items, durations), 1):
        print(f"\n[{i}/{len(news_items)}] {item.get('category', '')} — {item['title'][:60]}")
        data = _prepare_segment_data(item, dur, channel_name, used_photo_ids)
        data["type"] = "news"
        data["is_last"] = (i == len(news_items))
        segments_data.append(data)

    # Timestamps cumulativos exatos (sem rounding) para cada segmento
    cum_starts = [0.0]
    for seg in segments_data:
        cum_starts.append(cum_starts[-1] + seg["duration"])
    video_total = cum_starts[-1]

    # Pré-renderizar intro frame base
    intro_base = None
    if intro_duration > 0:
        bg_arr = np.full((VIDEO_H, VIDEO_W, 3), (12, 16, 30), dtype=np.uint8)
        overlay = Image.new("RGBA", (VIDEO_W, VIDEO_H), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        f_channel = _get_font(68, bold=True)
        f_sub = _get_font(26)
        ch_bbox = draw.textbbox((0, 0), channel_name, font=f_channel)
        cx = (VIDEO_W - ch_bbox[2]) // 2
        cy = VIDEO_H // 2 - ch_bbox[3] // 2 - 20
        draw.text((cx + 2, cy + 2), channel_name, font=f_channel, fill=(0, 0, 0, 160))
        draw.text((cx, cy), channel_name, font=f_channel, fill=(255, 255, 255, 255))
        date_str = datetime.now().strftime("%d/%m/%Y")
        d_bbox = draw.textbbox((0, 0), date_str, font=f_sub)
        dx = (VIDEO_W - d_bbox[2]) // 2
        draw.text((dx, cy + ch_bbox[3] + 16), date_str, font=f_sub, fill=(180, 180, 180, 200))
        intro_base = _alpha_composite(bg_arr, np.array(overlay))

    print("\nGerando vídeo contínuo (sync exato por timestamps cumulativos)...")

    CROSSFADE = 0.4  # segundos de transição suave entre segmentos

    def _render_news_frame(seg, t_local):
        """Renderiza um frame de notícia (waveform + barra de progresso)."""
        r, g, b = seg["color"]
        n_frames = seg["n_frames"]
        fi = min(int(t_local * FPS), n_frames - 1)

        img = Image.fromarray(seg["base"].copy())
        draw = ImageDraw.Draw(img)

        bars = seg["waveform"][fi]
        bar_count = len(bars)
        bar_w = (VIDEO_W - 100) / bar_count
        bar_max_h = 55
        bar_base_y = VIDEO_H - 28
        light = (min(r + 70, 255), min(g + 70, 255), min(b + 70, 255))
        for bi, amp in enumerate(bars):
            bh = max(3, int(amp * bar_max_h))
            x0 = 50 + bi * bar_w
            draw.rectangle([(x0, bar_base_y - bh), (x0 + bar_w - 2, bar_base_y)], fill=light)

        progress = min(1.0, t_local / max(seg["duration"], 0.001))
        draw.rectangle([(0, VIDEO_H - 5), (int(VIDEO_W * progress), VIDEO_H)], fill=(r, g, b))
        return img

    def _apply_alpha(img: Image.Image, alpha: float) -> Image.Image:
        """Aplica máscara de transparência (0=transparente, 1=opaco)."""
        mask = Image.new("RGBA", (VIDEO_W, VIDEO_H), (0, 0, 0, int(255 * (1 - alpha))))
        return Image.alpha_composite(img.convert("RGBA"), mask).convert("RGB")

    def make_frame_global(t):
        # Encontra o segmento ativo
        seg_idx = len(cum_starts) - 2
        for k in range(len(cum_starts) - 1):
            if cum_starts[k] <= t < cum_starts[k + 1]:
                seg_idx = k
                break

        seg = segments_data[seg_idx]
        t_local = t - cum_starts[seg_idx]
        seg_dur = seg["duration"]

        # ---- Intro --------------------------------------------------------
        if seg["type"] == "intro":
            frame = intro_base.copy()
            img = Image.fromarray(frame)
            # Fade in
            if t_local < 0.5:
                img = _apply_alpha(img, t_local / 0.5)
            draw2 = ImageDraw.Draw(img)
            progress = min(1.0, t_local / max(seg_dur, 0.001))
            draw2.rectangle([(0, VIDEO_H - 5), (int(VIDEO_W * progress), VIDEO_H)], fill=(80, 120, 220))
            return np.array(img)

        # ---- Segmento de notícia ------------------------------------------
        img = _render_news_frame(seg, t_local)
        is_last = seg.get("is_last", False)

        # Crossfade de entrada: mistura com o segmento anterior
        if t_local < CROSSFADE and seg_idx > 0:
            alpha = t_local / CROSSFADE          # 0→1 (fade in)
            prev = segments_data[seg_idx - 1]
            prev_t_local = seg_dur                # mostra último frame do anterior
            if prev["type"] == "intro":
                prev_img = _apply_alpha(Image.fromarray(intro_base.copy()), 1.0)
            else:
                prev_img = _render_news_frame(prev, prev["duration"] - 0.01)
            # Blend: (1-alpha)*anterior + alpha*atual
            img_arr = np.array(img).astype(float)
            prev_arr = np.array(prev_img).astype(float)
            img = Image.fromarray((prev_arr * (1 - alpha) + img_arr * alpha).astype(np.uint8))

        # Fade out no último segmento
        if is_last and seg_dur > 0.8 and t_local > seg_dur - 0.8:
            fade = (t_local - (seg_dur - 0.8)) / 0.8
            img = _apply_alpha(img, 1.0 - fade)

        return np.array(img)

    final = VideoClip(make_frame_global, duration=video_total).set_fps(FPS).set_audio(audio)

    print(f"Exportando: {output_path}  (aguarde...)")
    final.write_videofile(
        output_path,
        fps=FPS,
        codec="libx264",
        audio_codec="aac",
        temp_audiofile="temp_audio.m4a",
        remove_temp=True,
        preset="fast",
        logger="bar",
    )

    audio.close()
    final.close()

    print(f"\nVídeo salvo: {output_path}")
    return output_path
