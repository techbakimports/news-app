"""
Gera thumbnails 1280×720 para notícias e vídeos de áudio longo.
"""
import io
import os
import numpy as np
from datetime import datetime
from PIL import Image, ImageDraw, ImageFilter

from video import _get_font, _fetch_pexels_with_fallback, CATEGORY_COLORS, DEFAULT_COLOR
from config import CHANNEL_NAME

THUMB_W, THUMB_H = 1280, 720


def _bg_from_pexels(items: list[dict]) -> Image.Image | None:
    """Busca imagem Pexels a partir da notícia mais relevante."""
    for item in items[:3]:
        img = _fetch_pexels_with_fallback(
            item.get("title", ""),
            item.get("category", ""),
            set(),
        )
        if img:
            return img
    return None


def _build_bg(pil_img: Image.Image | None) -> Image.Image:
    """Prepara fundo 1280×720: foto recortada + escurecimento geral."""
    if pil_img is None:
        base = Image.new("RGB", (THUMB_W, THUMB_H), (18, 18, 28))
    else:
        iw, ih = pil_img.size
        ratio = THUMB_W / THUMB_H
        if iw / ih > ratio:
            nw = int(ih * ratio)
            pil_img = pil_img.crop(((iw - nw) // 2, 0, (iw - nw) // 2 + nw, ih))
        else:
            nh = int(iw / ratio)
            pil_img = pil_img.crop((0, (ih - nh) // 2, iw, (ih - nh) // 2 + nh))
        base = pil_img.resize((THUMB_W, THUMB_H), Image.LANCZOS)
        # Leve blur para dar profundidade
        base = base.filter(ImageFilter.GaussianBlur(radius=2))
        arr = np.array(base).astype(float) * 0.45
        base = Image.fromarray(arr.astype(np.uint8))

    base = base.convert("RGBA")

    # Gradiente horizontal: lado esquerdo bem escuro (texto), direito mais claro (foto)
    grad = Image.new("RGBA", (THUMB_W, THUMB_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(grad)
    split = int(THUMB_W * 0.58)
    for x in range(THUMB_W):
        if x <= split:
            alpha = int(200 * (1 - x / split * 0.35))
        else:
            alpha = int(200 * 0.65 * (1 - (x - split) / (THUMB_W - split)))
        draw.line([(x, 0), (x, THUMB_H)], fill=(0, 0, 0, alpha))

    return Image.alpha_composite(base, grad).convert("RGB")


def _wrap_short(draw, text: str, font, max_width: int, max_lines: int = 2) -> list[str]:
    words = text.split()
    lines, cur = [], []
    for w in words:
        test = " ".join(cur + [w])
        if draw.textbbox((0, 0), test, font=font)[2] <= max_width:
            cur.append(w)
        else:
            if cur:
                lines.append(" ".join(cur))
            cur = [w]
        if len(lines) >= max_lines:
            break
    if cur and len(lines) < max_lines:
        lines.append(" ".join(cur))
    # Trunca última linha com "…" se necessário
    if lines:
        last = lines[-1]
        while draw.textbbox((0, 0), last + "…", font=font)[2] > max_width and len(last) > 1:
            last = last[:-1]
        lines[-1] = last + ("…" if lines[-1] != last + "…" else "")
    return lines


def generate_thumbnail(items: list[dict], output_path: str) -> str:
    """
    Gera thumbnail JPEG 1280×720 para o vídeo de notícias.

    items: lista de notícias processadas (com 'title', 'category', 'source')
    output_path: caminho de saída (.jpg)
    """
    # --- Fundo ---
    print("  Buscando imagem para thumbnail...", end=" ", flush=True)
    pil_img = _bg_from_pexels(items)
    print("OK" if pil_img else "sem imagem, usando fundo sólido")
    canvas = _build_bg(pil_img)
    canvas = canvas.convert("RGBA")
    overlay = Image.new("RGBA", (THUMB_W, THUMB_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    pad_l = 52
    max_text_w = int(THUMB_W * 0.55) - pad_l

    # --- Barra lateral vermelha ---
    draw.rectangle([(0, 0), (8, THUMB_H)], fill=(220, 30, 30, 255))

    # --- Badge "NOTÍCIAS DO DIA" ---
    f_badge = _get_font(28, bold=True)
    badge_txt = "  NOTÍCIAS DO DIA  "
    bb = draw.textbbox((0, 0), badge_txt, font=f_badge)
    bw, bh = bb[2] + 8, bb[3] + 12
    badge_y = 42
    draw.rounded_rectangle([(pad_l, badge_y), (pad_l + bw, badge_y + bh)], radius=6, fill=(220, 30, 30, 230))
    draw.text((pad_l + 4, badge_y + 6), badge_txt.strip(), font=f_badge, fill=(255, 255, 255, 255))

    # --- Data ---
    f_date = _get_font(36, bold=True)
    date_str = datetime.now().strftime("%d/%m/%Y")
    date_y = badge_y + bh + 18
    draw.text((pad_l + 2, date_y + 2), date_str, font=f_date, fill=(0, 0, 0, 140))
    draw.text((pad_l, date_y), date_str, font=f_date, fill=(255, 255, 255, 230))

    # --- Headlines (top 3) ---
    f_title = _get_font(44, bold=True)
    f_dot   = _get_font(32, bold=True)
    top_items = items[:3]
    headline_y = date_y + 58
    line_gap = 10

    for item in top_items:
        color = CATEGORY_COLORS.get(item.get("category", ""), DEFAULT_COLOR)
        title = item.get("title", "")
        lines = _wrap_short(draw, title, f_title, max_text_w - 24)

        for i, line in enumerate(lines):
            y = headline_y + i * 52
            # ponto colorido apenas na primeira linha
            if i == 0:
                draw.ellipse([(pad_l, y + 14), (pad_l + 12, y + 26)], fill=(*color, 255))
            draw.text((pad_l + 22, y + 1), line, font=f_title, fill=(0, 0, 0, 150))
            draw.text((pad_l + 20, y), line, font=f_title, fill=(255, 255, 255, 240))

        headline_y += len(lines) * 52 + line_gap + 8

        if headline_y > THUMB_H - 90:
            break

    # --- Nome do canal (rodapé) ---
    f_ch = _get_font(30)
    ch_bb = draw.textbbox((0, 0), CHANNEL_NAME, font=f_ch)
    cx = THUMB_W - ch_bb[2] - pad_l
    cy = THUMB_H - ch_bb[3] - 28
    draw.text((cx + 1, cy + 1), CHANNEL_NAME, font=f_ch, fill=(0, 0, 0, 160))
    draw.text((cx, cy), CHANNEL_NAME, font=f_ch, fill=(200, 200, 200, 200))

    # --- Composição final ---
    final = Image.alpha_composite(canvas, overlay).convert("RGB")
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    final.save(output_path, "JPEG", quality=92, optimize=True)
    print(f"  Thumbnail salva: {output_path}")
    return output_path


# ---------------------------------------------------------------------------
# Thumbnail para vídeos de áudio longo (sons ambiente)
# ---------------------------------------------------------------------------

_AMBIENT_META = {
    "rain":       {"emoji": "🌧️", "label": "CHUVA RELAXANTE",   "color": (40,  100, 200)},
    "ocean":      {"emoji": "🌊", "label": "ONDAS DO MAR",       "color": (20,  140, 180)},
    "fire":       {"emoji": "🔥", "label": "LAREIRA",            "color": (210,  80,  20)},
    "forest":     {"emoji": "🌿", "label": "FLORESTA E NATUREZA","color": (30,  140,  60)},
    "whitenoise": {"emoji": "⬜", "label": "RUÍDO BRANCO",       "color": (160, 160, 180)},
    "brownnoise": {"emoji": "🟫", "label": "RUÍDO MARROM",       "color": (120,  80,  40)},
}


def generate_ambient_thumbnail(
    sound_type: str,
    hours: float,
    pexels_query: str,
    output_path: str,
) -> str:
    """
    Gera thumbnail 1280×720 para vídeo de áudio ambiente.
    Layout centralizado: label grande + duração + emoji.
    """
    meta = _AMBIENT_META.get(sound_type, {"emoji": "🎵", "label": sound_type.upper(), "color": (80, 80, 120)})
    r, g, b = meta["color"]

    # --- Fundo Pexels ---
    print("  Buscando imagem para thumbnail...", end=" ", flush=True)
    from video import _search_pexels
    pil_img, _ = _search_pexels(pexels_query)
    print("OK" if pil_img else "sem imagem")

    if pil_img:
        iw, ih = pil_img.size
        ratio = THUMB_W / THUMB_H
        if iw / ih > ratio:
            nw = int(ih * ratio)
            pil_img = pil_img.crop(((iw - nw) // 2, 0, (iw - nw) // 2 + nw, ih))
        else:
            nh = int(iw / ratio)
            pil_img = pil_img.crop((0, (ih - nh) // 2, iw, (ih - nh) // 2 + nh))
        base = pil_img.resize((THUMB_W, THUMB_H), Image.LANCZOS)
        base = base.filter(ImageFilter.GaussianBlur(radius=3))
        arr = np.array(base).astype(float) * 0.38
        base = Image.fromarray(arr.astype(np.uint8)).convert("RGBA")
    else:
        base = Image.new("RGBA", (THUMB_W, THUMB_H), (int(r * 0.2), int(g * 0.2), int(b * 0.2), 255))

    # Vinheta radial escura nas bordas
    grad = Image.new("RGBA", (THUMB_W, THUMB_H), (0, 0, 0, 0))
    draw_g = ImageDraw.Draw(grad)
    cx, cy = THUMB_W // 2, THUMB_H // 2
    for step in range(60):
        frac = step / 60
        alpha = int(180 * frac ** 1.5)
        rw = int(THUMB_W * (1 - frac * 0.35))
        rh = int(THUMB_H * (1 - frac * 0.35))
        draw_g.rectangle(
            [(cx - rw // 2, cy - rh // 2), (cx + rw // 2, cy + rh // 2)],
            outline=(0, 0, 0, alpha), width=2,
        )
    canvas = Image.alpha_composite(base, grad)

    overlay = Image.new("RGBA", (THUMB_W, THUMB_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # --- Barra colorida inferior ---
    bar_h = 12
    draw.rectangle([(0, THUMB_H - bar_h), (THUMB_W, THUMB_H)], fill=(r, g, b, 240))

    # --- Emoji (topo central) ---
    f_emoji = _get_font(100, bold=False)
    emoji_txt = meta["emoji"]
    eb = draw.textbbox((0, 0), emoji_txt, font=f_emoji)
    draw.text(((THUMB_W - eb[2]) // 2, 60), emoji_txt, font=f_emoji, fill=(255, 255, 255, 220))

    # --- Label do som ---
    f_label = _get_font(96, bold=True)
    label = meta["label"]
    lb = draw.textbbox((0, 0), label, font=f_label)
    lx = (THUMB_W - lb[2]) // 2
    ly = 195
    draw.text((lx + 3, ly + 3), label, font=f_label, fill=(0, 0, 0, 160))
    draw.text((lx, ly), label, font=f_label, fill=(255, 255, 255, 255))

    # Sublinhado colorido
    draw.rectangle([(lx, ly + lb[3] + 8), (lx + lb[2], ly + lb[3] + 14)], fill=(r, g, b, 255))

    # --- Duração ---
    hours_label = f"{int(hours)}h" if hours == int(hours) else f"{round(hours * 60)}min"
    duration_txt = f"{hours_label} para Dormir e Relaxar"
    f_dur = _get_font(54, bold=True)
    db = draw.textbbox((0, 0), duration_txt, font=f_dur)
    dx = (THUMB_W - db[2]) // 2
    dy = ly + lb[3] + 30
    draw.text((dx + 2, dy + 2), duration_txt, font=f_dur, fill=(0, 0, 0, 150))
    draw.text((dx, dy), duration_txt, font=f_dur, fill=(r + 80, g + 80, b + 80, 230))

    # --- Canal ---
    f_ch = _get_font(32)
    ch_bb = draw.textbbox((0, 0), CHANNEL_NAME, font=f_ch)
    draw.text(
        ((THUMB_W - ch_bb[2]) // 2, THUMB_H - ch_bb[3] - 24),
        CHANNEL_NAME, font=f_ch, fill=(200, 200, 200, 180),
    )

    final = Image.alpha_composite(canvas, overlay).convert("RGB")
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    final.save(output_path, "JPEG", quality=92, optimize=True)
    print(f"  Thumbnail salva: {output_path}")
    return output_path
