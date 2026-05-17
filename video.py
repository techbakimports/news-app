import os
import io
import math
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
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/calibrib.ttf" if bold else "C:/Windows/Fonts/calibri.ttf",
        "C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                pass
    return ImageFont.load_default()


def _search_pexels(query):
    api_key = os.getenv("PEXELS_API_KEY")
    if not api_key:
        print("  PEXELS_API_KEY não configurada — usando fundo padrão.")
        return None
    try:
        r = requests.get(
            "https://api.pexels.com/v1/search",
            headers={"Authorization": api_key},
            params={"query": query, "per_page": 5, "orientation": "landscape"},
            timeout=10,
        )
        photos = r.json().get("photos", [])
        if not photos:
            return None
        photo = random.choice(photos[:3])
        img_r = requests.get(photo["src"]["large2x"], timeout=15)
        return Image.open(io.BytesIO(img_r.content)).convert("RGB")
    except Exception as e:
        print(f"  Erro Pexels: {e}")
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


def _prepare_segment_data(news_item, duration, channel_name):
    """Pré-renderiza todos os dados estáticos de um segmento (imagem, waveform, overlay)."""
    title = news_item["title"]
    category = news_item.get("category", "Notícia")
    color = CATEGORY_COLORS.get(category, DEFAULT_COLOR)
    r, g, b = color

    keywords = f"{category} {' '.join(title.split()[:5])}"
    print(f"  Buscando imagem para '{category}': {title[:50]}...")
    pil_img = _search_pexels(keywords)
    if pil_img is None:
        pil_img = Image.new("RGB", (VIDEO_W, VIDEO_H), (18, 22, 38))

    bg = _prepare_bg(pil_img, color)
    text_layer = _render_static_layer(title, category, channel_name, color)
    base = _alpha_composite(bg, text_layer)

    n_frames = int(math.ceil(duration * FPS))
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

    # Durações reais medidas por segmento ou estimativa por palavras
    if segment_durations and len(segment_durations) == len(news_items):
        durations = [max(5.0, d) for d in segment_durations]
        print(f"Durações por segmento (reais): {[f'{d:.1f}s' for d in durations]}")
    else:
        summaries = [item.get("ai_summary") or item.get("summary") or item["title"] for item in news_items]
        word_counts = [max(1, len(s.split())) for s in summaries]
        total_words = sum(word_counts)
        durations = [max(5.0, total_duration * (wc / total_words)) for wc in word_counts]

    # Pré-renderizar todos os segmentos (imagens, waveforms, overlays)
    print(f"\nPré-renderizando {len(news_items)} segmento(s)...")
    segments_data = []

    # Segmento 0: intro
    if intro_duration > 0:
        print(f"  Slide de abertura: {intro_duration:.1f}s")
        intro_n_frames = int(math.ceil(intro_duration * FPS))
        segments_data.append({"type": "intro", "duration": intro_duration, "n_frames": intro_n_frames})

    for i, (item, dur) in enumerate(zip(news_items, durations), 1):
        print(f"\n[{i}/{len(news_items)}] {item.get('category', '')} — {item['title'][:60]}")
        data = _prepare_segment_data(item, dur, channel_name)
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

    def make_frame_global(t):
        # Encontra o segmento ativo via busca binária nos timestamps cumulativos
        seg_idx = len(cum_starts) - 2  # default: último segmento
        for k in range(len(cum_starts) - 1):
            if cum_starts[k] <= t < cum_starts[k + 1]:
                seg_idx = k
                break

        seg = segments_data[seg_idx]
        t_local = t - cum_starts[seg_idx]
        seg_dur = seg["duration"]

        if seg["type"] == "intro":
            frame = intro_base.copy()
            img = Image.fromarray(frame)
            draw = ImageDraw.Draw(img)
            progress = t_local / seg_dur
            # Fade in nos primeiros 0.5s
            if t_local < 0.5:
                alpha_mask = Image.new("RGBA", (VIDEO_W, VIDEO_H), (0, 0, 0, int(255 * (1 - t_local / 0.5))))
                img = img.convert("RGBA")
                img = Image.alpha_composite(img, alpha_mask)
                img = img.convert("RGB")
            draw2 = ImageDraw.Draw(img)
            draw2.rectangle([(0, VIDEO_H - 5), (int(VIDEO_W * progress), VIDEO_H)], fill=(80, 120, 220))
            return np.array(img)

        # Segmento de notícia
        base = seg["base"]
        waveform = seg["waveform"]
        n_frames = seg["n_frames"]
        r, g, b = seg["color"]
        is_last = seg.get("is_last", False)

        frame = base.copy()
        fi = min(int(t_local * FPS), n_frames - 1)
        img = Image.fromarray(frame)
        draw = ImageDraw.Draw(img)

        bars = waveform[fi]
        bar_count = len(bars)
        bar_w = (VIDEO_W - 100) / bar_count
        bar_max_h = 55
        bar_base_y = VIDEO_H - 28
        light = (min(r + 70, 255), min(g + 70, 255), min(b + 70, 255))
        for i, amp in enumerate(bars):
            bh = max(3, int(amp * bar_max_h))
            x0 = 50 + i * bar_w
            x1 = x0 + bar_w - 2
            draw.rectangle([(x0, bar_base_y - bh), (x1, bar_base_y)], fill=light)

        progress = t_local / seg_dur
        draw.rectangle([(0, VIDEO_H - 5), (int(VIDEO_W * progress), VIDEO_H)], fill=(r, g, b))

        # Fade out nos últimos 0.8s do último segmento
        if is_last and t_local > seg_dur - 0.8:
            fade_progress = (t_local - (seg_dur - 0.8)) / 0.8
            alpha_val = int(255 * fade_progress)
            alpha_mask = Image.new("RGBA", (VIDEO_W, VIDEO_H), (0, 0, 0, alpha_val))
            img_rgba = img.convert("RGBA")
            img = Image.alpha_composite(img_rgba, alpha_mask).convert("RGB")

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
