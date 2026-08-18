"""
PROTÓTIPO — motor "whiteboard" (estilo NotebookLM): ícones desenhados que
entram na tela sincronizados com uma narração única e contínua.

Ferramentas usadas, todas gratuitas:
- Groq (free tier) escolhe ícones de um vocabulário curado (nomes reais do
  Tabler Icons, MIT) e classifica a relação entre eles (fluxo/oposição/
  lista/destaque) pra variar o layout.
- Edge TTS gera UM áudio contínuo da narração inteira (sem emendas) e expõe
  o timestamp de cada palavra (WordBoundary) — as cenas trocam em cima
  desses timestamps, não o contrário.
- Tabler Icons (SVG, MIT) rasterizados via svglib + reportlab, com uma leve
  distorção procedural ("sketchify") pra imitar traço à mão.

Status: PROTÓTIPO, não integrado ao pipeline de produção. Roda isolado,
gera 1 vídeo de amostra com 1 notícia real por execução. Ver README.md
nesta pasta pra contexto de decisões e gaps conhecidos.

Uso: python experiments/whiteboard/whiteboard_engine.py
"""
import sys, os, io, json, asyncio, math, random, re

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
sys.path.insert(0, _REPO_ROOT)
os.chdir(_REPO_ROOT)  # módulos do projeto (audio.py, video.py...) esperam cwd = raiz do repo

import numpy as np
import requests as _rq
from PIL import Image, ImageDraw, ImageFilter
from bs4 import BeautifulSoup
from moviepy.editor import AudioFileClip, VideoClip, concatenate_videoclips

from fetcher import fetch_latest_news, extract_article_content
from audio import _stream_to_bytes, voice_for_category, clean_text
from video import _get_font, CATEGORY_COLORS, DEFAULT_COLOR

OUTPUT_DIR = os.path.join(_HERE, "output")
ICONS_DIR = os.path.join(_HERE, "icons_cache")
NAMES_JSON = os.path.join(_HERE, "tabler_icon_names.json")
W, H, FPS = 1280, 720, 24
INK = "#1f3a5f"
INK_RGB = (31, 58, 95)
RAW_BASE = "https://raw.githubusercontent.com/tabler/tabler-icons/main/icons/outline/"

os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(ICONS_DIR, exist_ok=True)


def _load_icon_names() -> set:
    """Carrega a lista de nomes de ícones do Tabler (cache local em JSON,
    versionado no repo). Se o cache não existir, busca da API do GitHub."""
    if os.path.exists(NAMES_JSON):
        with open(NAMES_JSON, "r") as f:
            return set(json.load(f))
    print("[icons] cache não encontrado, buscando lista de ícones no GitHub...")
    r = _rq.get("https://api.github.com/repos/tabler/tabler-icons/contents/icons", timeout=15)
    r.raise_for_status()
    outline_sha = next(e["sha"] for e in r.json() if e["name"] == "outline")
    r2 = _rq.get(f"https://api.github.com/repos/tabler/tabler-icons/git/trees/{outline_sha}", timeout=30)
    r2.raise_for_status()
    names = [e["path"][:-4] for e in r2.json()["tree"] if e["path"].endswith(".svg")]
    with open(NAMES_JSON, "w") as f:
        json.dump(names, f)
    return set(names)


ICON_SET = _load_icon_names()

# Vocabulário curado — só nomes que existem no Tabler (validados no runtime)
_VOCAB_RAW = [
    # dinheiro / economia
    "coin", "coins", "cash", "credit-card", "building-bank", "wallet", "receipt",
    "chart-line", "chart-bar", "chart-pie", "trending-up", "trending-down",
    "percentage", "pig-money", "shopping-cart", "tag", "discount",
    # tecnologia
    "robot", "brain", "cpu", "device-mobile", "device-laptop", "device-tv",
    "wifi", "cloud", "database", "code", "bulb", "rocket", "satellite",
    "battery", "plug", "antenna", "camera", "video", "microphone",
    # política / justiça / cidade
    "gavel", "scale", "building", "buildings", "flag", "podium", "microphone-2",
    "file-text", "files", "writing", "certificate", "vote", "user-check",
    # polícia / segurança
    "handcuffs", "shield", "shield-check", "shield-lock", "lock", "key",
    "alert-triangle", "alert-circle", "urgent", "siren", "eye", "search",
    # esporte
    "ball-football", "ball-basketball", "ball-volleyball", "trophy", "medal",
    "run", "play-football", "stopwatch", "olympics",
    # saúde
    "heart", "heartbeat", "activity", "stethoscope", "pill", "vaccine",
    "virus", "ambulance", "first-aid-kit",
    # geral / narrativa
    "news", "speakerphone", "broadcast", "world", "map", "map-pin", "calendar",
    "clock", "hourglass", "users", "user", "home", "car", "plane", "bus",
    "train", "school", "book", "pencil", "mail", "message", "phone",
    "star", "thumb-up", "thumb-down", "hand-stop", "check", "x", "ban",
    "question-mark", "info-circle", "bolt", "flame", "droplet", "cloud-rain",
    "sun", "moon", "leaf", "tree", "recycle", "tool", "hammer", "settings",
    "target", "award", "crown", "gift", "briefcase", "coffee", "arrow-right",
]
VOCAB = sorted(set(n for n in _VOCAB_RAW if n in ICON_SET))
print(f"[vocab] {len(VOCAB)}/{len(_VOCAB_RAW)} ícones do vocabulário existem no Tabler")


# ---------------------------------------------------------------------------
# Groq: cenas + ícones do vocabulário
# ---------------------------------------------------------------------------

def gerar_roteiro_cenas(title: str, resumo: str) -> list[dict] | None:
    from groq import Groq
    key = os.getenv("GROQ_API_KEY")
    if not key:
        return None
    prompt = (
        "Você roteiriza um vídeo curto de notícia no estilo 'quadro branco explicativo'.\n"
        f"TÍTULO: {title}\n"
        f"RESUMO: {resumo}\n\n"
        "Escreva uma narração COMPLETA da notícia em português, com 150 a 220 palavras, "
        "cobrindo todos os fatos importantes da matéria — como uma apresentadora de "
        "podcast explicando a notícia inteira sem parar. As frases devem se conectar "
        "naturalmente (conectivos: 'e é aí que', 'além disso', 'o resultado disso'). "
        "O PRIMEIRO trecho deve apresentar a notícia (funciona como abertura). "
        "Depois divida essa narração em 6 a 9 trechos de 15 a 30 palavras, na ordem, "
        "SEM alterar o texto — lidos em sequência devem reproduzir a narração exata. "
        "Sem opinião, tom claro de podcast explicativo.\n"
        "Para CADA frase escolha 2 ou 3 ícones desta lista (use EXATAMENTE estes nomes):\n"
        f"{', '.join(VOCAB)}\n\n"
        "Para CADA frase, classifique também a RELAÇÃO entre os ícones:\n"
        '- "fluxo": um leva ao outro (causa/consequência, sequência)\n'
        '- "oposicao": conflito ou contraste entre eles\n'
        '- "lista": itens independentes, enumeração\n'
        '- "destaque": um único conceito forte (use 1 ícone só nesse caso)\n\n'
        "Escolha ícones que representem visualmente a frase. Responda APENAS com JSON válido:\n"
        '{"cenas": [{"texto": "...", "icones": ["nome-exato-1", "nome-exato-2"], "relacao": "fluxo"}]}'
    )
    try:
        client = Groq(api_key=key)
        resp = client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5,
            reasoning_effort="low",
            response_format={"type": "json_object"},
        )
        data = json.loads(resp.choices[0].message.content)
        cenas = []
        for c in data.get("cenas", []):
            if not c.get("texto"):
                continue
            icones = [i for i in c.get("icones", []) if i in ICON_SET][:3]
            relacao = c.get("relacao", "fluxo")
            if relacao not in ("fluxo", "oposicao", "lista", "destaque"):
                relacao = "fluxo"
            cenas.append({"texto": c["texto"], "icones": icones, "relacao": relacao})
        return cenas[:9] if len(cenas) >= 2 else None
    except Exception as e:
        print(f"  [Groq] falhou: {e}")
        return None


# ---------------------------------------------------------------------------
# Ícones
# ---------------------------------------------------------------------------

def fetch_icon_svg(name: str) -> str | None:
    path = os.path.join(ICONS_DIR, f"{name}.svg")
    if os.path.exists(path):
        return path
    try:
        r = _rq.get(f"{RAW_BASE}{name}.svg", timeout=10)
        r.raise_for_status()
        with open(path, "w", encoding="utf-8") as f:
            f.write(r.text)
        return path
    except Exception:
        return None


def svg_to_rgba(svg_path: str, target_px: int = 200) -> Image.Image | None:
    from svglib.svglib import svg2rlg
    from reportlab.graphics import renderPM
    try:
        with open(svg_path, "r", encoding="utf-8") as f:
            svg_text = f.read().replace('stroke="currentColor"', f'stroke="{INK}"')
        fixed = svg_path + ".fixed.svg"
        with open(fixed, "w", encoding="utf-8") as f:
            f.write(svg_text)
        drawing = svg2rlg(fixed)
        scale = target_px / max(drawing.width, drawing.height)
        drawing.width *= scale
        drawing.height *= scale
        drawing.scale(scale, scale)
        buf = io.BytesIO()
        renderPM.drawToFile(drawing, buf, fmt="PNG", bg=0xFFFFFF)
        img = Image.open(buf).convert("RGBA")
        arr = np.array(img)
        white = (arr[:, :, 0] > 245) & (arr[:, :, 1] > 245) & (arr[:, :, 2] > 245)
        arr[:, :, 3] = np.where(white, 0, 255)
        return Image.fromarray(arr)
    except Exception:
        return None


def sketchify(img: Image.Image, seed: int) -> Image.Image:
    rng = np.random.default_rng(seed)
    arr = np.array(img)
    h, w = arr.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w]
    freq = rng.uniform(0.04, 0.07)
    dx = (1.6 * np.sin(yy * freq + rng.uniform(0, 6))).astype(np.float32)
    dy = (1.6 * np.cos(xx * freq + rng.uniform(0, 6))).astype(np.float32)
    sx = np.clip(xx + dx, 0, w - 1).astype(np.int32)
    sy = np.clip(yy + dy, 0, h - 1).astype(np.int32)
    return Image.fromarray(arr[sy, sx]).filter(ImageFilter.GaussianBlur(0.4))


# ---------------------------------------------------------------------------
# Render helpers
# ---------------------------------------------------------------------------

def grid_background() -> np.ndarray:
    img = Image.new("RGB", (W, H), (250, 250, 252))
    draw = ImageDraw.Draw(img)
    for x in range(0, W, 40):
        draw.line([(x, 0), (x, H)], fill=(232, 234, 240), width=1)
    for y in range(0, H, 40):
        draw.line([(0, y), (W, y)], fill=(232, 234, 240), width=1)
    return np.array(img)


BG = grid_background()


async def tts_with_boundaries(text: str, voice: str):
    """
    Gera UM áudio TTS contínuo da narração inteira + timestamps de cada palavra
    (eventos WordBoundary do Edge TTS). É o que garante fluidez total: a voz
    nunca é emendada — só as CENAS trocam em cima dos timestamps.
    Retorna (bytes_mp3, [(offset_segundos, palavra), ...]).
    """
    import edge_tts
    data = bytearray()
    words = []
    # edge-tts >= 7.x emite SentenceBoundary por padrão — WordBoundary é opt-in
    com = edge_tts.Communicate(text, voice, boundary="WordBoundary")
    async for chunk in com.stream():
        if chunk["type"] == "audio":
            data.extend(chunk["data"])
        elif chunk["type"] == "WordBoundary":
            words.append((chunk["offset"] / 1e7, chunk["text"]))
    return bytes(data), words


def scene_starts_from_words(cenas, words):
    """
    Calcula o tempo de início de cada cena consumindo os WordBoundary na ordem:
    a cena i começa no timestamp da primeira palavra do seu trecho.
    """
    starts = []
    wi = 0
    for c in cenas:
        n_words = len(clean_text(c["texto"]).split())
        starts.append(words[wi][0] if wi < len(words) else None)
        wi += n_words
    # cena 1 sempre começa em 0
    if starts:
        starts[0] = 0.0
    return starts


def trim_trailing_silence(ac, thresh=0.012, keep=0.10):
    """
    Corta o silêncio do FIM do áudio TTS (Edge TTS deixa ~0,5s de cauda muda).
    Sem isso, cada cena termina com pausa e a narração fica "espaçada" —
    o objetivo é fluxo contínuo tipo NotebookLM.
    """
    try:
        arr = ac.to_soundarray(fps=16000)
        mono = np.abs(arr).max(axis=1) if arr.ndim > 1 else np.abs(arr)
        idx = np.where(mono > thresh)[0]
        if len(idx) == 0:
            return ac
        end = min(ac.duration, idx[-1] / 16000 + keep)
        return ac.subclip(0, end)
    except Exception:
        return ac


def wrap_text(draw, text, font, max_width):
    words = text.split()
    lines, cur = [], []
    for w_ in words:
        test = " ".join(cur + [w_])
        if draw.textbbox((0, 0), test, font=font)[2] <= max_width:
            cur.append(w_)
        else:
            if cur:
                lines.append(" ".join(cur))
            cur = [w_]
    if cur:
        lines.append(" ".join(cur))
    return lines


def _arrow_points(p0, p1, n=24, sag=28, seed=0):
    """Pontos de uma seta curva 'à mão' entre dois pontos (bezier quadrática)."""
    rng = random.Random(seed)
    mx = (p0[0] + p1[0]) / 2 + rng.randint(-15, 15)
    my = (p0[1] + p1[1]) / 2 + sag + rng.randint(-8, 8)
    pts = []
    for i in range(n + 1):
        t = i / n
        x = (1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * mx + t ** 2 * p1[0]
        y = (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * my + t ** 2 * p1[1]
        pts.append((x, y))
    return pts


def draw_arrow_progressive(draw, pts, progress, width=5):
    """Desenha a seta até `progress` (0..1) — efeito de 'sendo desenhada'."""
    if progress <= 0:
        return
    n_vis = max(2, int(len(pts) * min(progress, 1.0)))
    vis = pts[:n_vis]
    draw.line(vis, fill=INK_RGB, width=width, joint="curve")
    if progress >= 1.0:
        # ponta da seta
        (x1, y1), (x0, y0) = vis[-1], vis[-3]
        ang = math.atan2(y1 - y0, x1 - x0)
        L = 16
        for da in (math.radians(150), math.radians(-150)):
            draw.line([(x1, y1), (x1 + L * math.cos(ang + da), y1 + L * math.sin(ang + da))],
                      fill=INK_RGB, width=width)


_VS_RED = (205, 60, 60)


def draw_vs_progressive(draw, cx, cy, progress, size=34, width=7):
    """X desenhado à mão entre dois ícones (oposição): 1º traço, depois o 2º."""
    if progress <= 0:
        return
    s = size
    # traço 1 (\) ocupa progress 0→0.5; traço 2 (/) ocupa 0.5→1
    p1 = min(1.0, progress / 0.5)
    x0, y0, x1, y1 = cx - s, cy - s, cx + s, cy + s
    draw.line([(x0, y0), (x0 + (x1 - x0) * p1, y0 + (y1 - y0) * p1)], fill=_VS_RED, width=width)
    if progress > 0.5:
        p2 = min(1.0, (progress - 0.5) / 0.5)
        draw.line([(x1, y0), (x1 - (x1 - x0) * p2, y0 + (y1 - y0) * p2)], fill=_VS_RED, width=width)


def draw_ring_progressive(draw, cx, cy, rx, ry, progress, seed=0, width=6):
    """Círculo 'à mão' desenhado progressivamente em volta de um ícone."""
    if progress <= 0:
        return
    rng = random.Random(seed)
    n = 48
    n_vis = max(2, int(n * min(progress, 1.0)))
    pts = []
    start = -math.pi / 2
    for i in range(n_vis + 1):
        a = start + 2 * math.pi * (i / n) * 1.04  # passa um pouco do fechamento
        wob = 1 + rng.uniform(-0.03, 0.03)
        pts.append((cx + rx * wob * math.cos(a), cy + ry * wob * math.sin(a)))
    draw.line(pts, fill=INK_RGB, width=width, joint="curve")


# ---------------------------------------------------------------------------
# Cenas
# ---------------------------------------------------------------------------

def make_scene_clip(texto, icons, cat, color, duration, scene_seed=0, relacao="fluxo"):
    f_badge = _get_font(26, bold=True)
    f_text = _get_font(42, bold=True)
    rng = random.Random(scene_seed)

    # "destaque" usa só o primeiro ícone, grande e centralizado
    if relacao == "destaque" and icons:
        big = icons[0]
        bw_, bh_ = big.size
        big = big.resize((int(bw_ * 1.5), int(bh_ * 1.5)), Image.LANCZOS)
        icons = [big]

    n = len(icons)
    positions, rotations = [], []
    if n == 1:
        iw, ih = icons[0].size
        positions = [((W - iw) // 2, 140)]
    else:
        slot_w = (W - 200) // max(n, 1)
        for i in range(n):
            iw, ih = icons[i].size
            px = 100 + i * slot_w + (slot_w - iw) // 2 + rng.randint(-20, 20)
            py = 150 + rng.randint(-25, 35)
            positions.append((px, py))
    rotations = [rng.uniform(-6, 6) for _ in range(n)]

    # Entradas ESPALHADAS pela cena: o último ícone entra por volta de 60% da
    # duração — a cena nunca fica "pronta e parada" logo no início.
    ANIM = 0.55
    if n <= 1:
        appear = [0.4]
    else:
        span = max(duration * 0.6 - 0.4, 1.0)
        appear = [0.4 + i * (span / (n - 1)) for i in range(n)]

    # Conectores conforme a relação
    arrows = []       # [(pts, t_start)] — setas do "fluxo"
    vs_marks = []     # [(cx, cy, t_start)] — X do "oposicao"
    ring = None       # (cx, cy, rx, ry, t_start) — círculo do "destaque"
    if relacao == "fluxo":
        for i in range(n - 1):
            iw0, ih0 = icons[i].size
            iw1, ih1 = icons[i + 1].size
            p0 = (positions[i][0] + iw0 + 8, positions[i][1] + ih0 // 2 + 10)
            p1 = (positions[i + 1][0] - 10, positions[i + 1][1] + ih1 // 2 + 10)
            pts = _arrow_points(p0, p1, seed=scene_seed + i)
            arrows.append((pts, appear[i + 1] + ANIM * 0.6))
    elif relacao == "oposicao":
        for i in range(n - 1):
            iw0, ih0 = icons[i].size
            iw1, ih1 = icons[i + 1].size
            mx = (positions[i][0] + iw0 + positions[i + 1][0]) // 2
            my = (positions[i][1] + ih0 // 2 + positions[i + 1][1] + ih1 // 2) // 2
            vs_marks.append((mx, my, appear[i + 1] + ANIM * 0.6))
    elif relacao == "destaque" and n == 1:
        iw, ih = icons[0].size
        cx, cy = positions[0][0] + iw // 2, positions[0][1] + ih // 2
        ring = (cx, cy, int(iw * 0.85), int(ih * 0.78), 0.4 + ANIM + 0.3)

    _tmp = ImageDraw.Draw(Image.new("RGB", (W, H)))
    lines = wrap_text(_tmp, texto, f_text, W - 160)[:3]

    # bolha de cor clara atrás dos ícones
    r, g, b = color
    blob_fill = (r, g, b, 36)

    def make_frame(t):
        frame = Image.fromarray(BG.copy()).convert("RGBA")
        draw = ImageDraw.Draw(frame)

        badge_txt = f" {cat.upper()} "
        bb = draw.textbbox((0, 0), badge_txt, font=f_badge)
        bw, bh = bb[2] + 20, bb[3] + 14
        draw.rounded_rectangle([(40, 30), (40 + bw, 30 + bh)], radius=8, fill=(*color, 255))
        draw.text((50, 36), badge_txt.strip(), font=f_badge, fill=(255, 255, 255, 255))

        # frase
        alpha = min(1.0, t / 0.4)
        ty = H - 80 - len(lines) * 52
        for line in lines:
            tb = draw.textbbox((0, 0), line, font=f_text)
            tx = (W - tb[2]) // 2
            layer = Image.new("RGBA", frame.size, (0, 0, 0, 0))
            ImageDraw.Draw(layer).text((tx, ty), line, font=f_text,
                                       fill=(25, 30, 45, int(255 * alpha)))
            frame = Image.alpha_composite(frame, layer)
            draw = ImageDraw.Draw(frame)
            ty += 52

        # conectores (desenho progressivo): setas, X de oposição ou círculo
        arrow_layer = Image.new("RGBA", frame.size, (0, 0, 0, 0))
        adraw = ImageDraw.Draw(arrow_layer)
        for pts, t_start in arrows:
            draw_arrow_progressive(adraw, pts, (t - t_start) / 0.5)
        for mx, my, t_start in vs_marks:
            draw_vs_progressive(adraw, mx, my, (t - t_start) / 0.5)
        if ring is not None:
            rcx, rcy, rrx, rry, t_start = ring
            draw_ring_progressive(adraw, rcx, rcy, rrx, rry, (t - t_start) / 0.7,
                                  seed=scene_seed)
        frame = Image.alpha_composite(frame, arrow_layer)

        # ícones (entrada + idle bob + leve rotação fixa + bolha de cor)
        for k, (icon, (px, py), t0, rot) in enumerate(zip(icons, positions, appear, rotations)):
            lt = t - t0
            if lt < 0:
                continue
            p = min(1.0, lt / ANIM)
            e = 1 - (1 - p) ** 3
            scale = 0.4 + 0.6 * e
            bob = 4 * math.sin(2 * math.pi * 0.4 * t + k * 2.1) if p >= 1.0 else 0
            iw, ih = icon.size

            # bolha
            blob = Image.new("RGBA", frame.size, (0, 0, 0, 0))
            bd = ImageDraw.Draw(blob)
            cx, cy = px + iw // 2, py + ih // 2 + bob
            rad = int(iw * 0.72 * scale)
            bd.ellipse([(cx - rad, cy - rad), (cx + rad, cy + rad)], fill=blob_fill)
            frame = Image.alpha_composite(frame, blob)

            res = icon.rotate(rot, expand=True, resample=Image.BICUBIC)
            rw, rh = res.size
            nw, nh = max(1, int(rw * scale)), max(1, int(rh * scale))
            res = res.resize((nw, nh), Image.LANCZOS)
            if e < 1.0:
                a = np.array(res)
                a[:, :, 3] = (a[:, :, 3].astype(float) * e).astype(np.uint8)
                res = Image.fromarray(a)
            frame.alpha_composite(res, (int(cx - nw / 2), int(cy - nh / 2)))

        return np.array(frame.convert("RGB"))

    return VideoClip(make_frame, duration=duration).set_fps(FPS)


def strip_source_suffix(title: str) -> str:
    """Remove sufixo ' - Fonte' que vem colado no título cru do Google News RSS."""
    return re.sub(r"\s*-\s*[\w\sÀ-ÿ.]{1,30}$", "", title).strip() or title


def make_title_clip(title, cat, color, duration, theme_icon=None, source: str = ""):
    f_title = _get_font(54, bold=True)
    f_badge = _get_font(28, bold=True)
    f_src = _get_font(24)
    _tmp = ImageDraw.Draw(Image.new("RGB", (W, H)))
    lines = wrap_text(_tmp, title, f_title, W - 200)[:3]

    def make_frame(t):
        frame = Image.fromarray(BG.copy()).convert("RGBA")
        draw = ImageDraw.Draw(frame)
        alpha = min(1.0, t / 0.5)

        y_cursor = 110
        if theme_icon is not None:
            p = min(1.0, t / 0.6)
            e = 1 - (1 - p) ** 3
            iw, ih = theme_icon.size
            scale = 0.5 + 0.5 * e
            nw, nh = max(1, int(iw * scale)), max(1, int(ih * scale))
            res = theme_icon.resize((nw, nh), Image.LANCZOS)
            if e < 1.0:
                a = np.array(res)
                a[:, :, 3] = (a[:, :, 3].astype(float) * e).astype(np.uint8)
                res = Image.fromarray(a)
            frame.alpha_composite(res, ((W - nw) // 2, y_cursor + (ih - nh) // 2))
            draw = ImageDraw.Draw(frame)
            y_cursor += ih + 20

        badge_txt = f" {cat.upper()} "
        bb = draw.textbbox((0, 0), badge_txt, font=f_badge)
        bw, bh = bb[2] + 24, bb[3] + 16
        bx = (W - bw) // 2
        draw.rounded_rectangle([(bx, y_cursor), (bx + bw, y_cursor + bh)], radius=10, fill=(*color, 255))
        draw.text((bx + 12, y_cursor + 8), badge_txt.strip(), font=f_badge, fill=(255, 255, 255, 255))
        y_cursor += bh + 34

        for line in lines:
            tb = draw.textbbox((0, 0), line, font=f_title)
            tx = (W - tb[2]) // 2
            layer = Image.new("RGBA", frame.size, (0, 0, 0, 0))
            ImageDraw.Draw(layer).text((tx, y_cursor), line, font=f_title,
                                       fill=(25, 30, 45, int(255 * alpha)))
            frame = Image.alpha_composite(frame, layer)
            draw = ImageDraw.Draw(frame)
            y_cursor += 68
        draw.rectangle([(W // 2 - 80, y_cursor + 12), (W // 2 + 80, y_cursor + 18)], fill=(*color, 255))
        if source:
            src_txt = f"Fonte: {source}"
            sb = draw.textbbox((0, 0), src_txt, font=f_src)
            draw.text(((W - sb[2]) // 2, H - 60), src_txt, font=f_src, fill=(140, 145, 160, 220))
        return np.array(frame.convert("RGB"))

    return VideoClip(make_frame, duration=duration).set_fps(FPS)


# ---------------------------------------------------------------------------
# Pipeline do teste (roda isolado — 1 notícia por execução)
# ---------------------------------------------------------------------------

async def main():
    print("=== [1/5] Buscando notícia real (RSS, categorias embaralhadas) ===")
    cats = ["Esporte", "Policial", "Entretenimento", "Mercado Financeiro", "Política", "Tecnologia"]
    random.shuffle(cats)
    item = None
    for cat_try in cats:
        raw = fetch_latest_news(limit=3, categories=[cat_try])
        if raw:
            item = raw[0]
            break
    if not item:
        print("Nenhuma notícia. Abortando.")
        return
    title = strip_source_suffix(item["title"])
    source = item.get("source", "")
    cat = item.get("category", "Notícias")
    color = CATEGORY_COLORS.get(cat, DEFAULT_COLOR)
    resumo_html = item.get("summary", "").strip()
    resumo = BeautifulSoup(resumo_html, "html.parser").get_text(" ", strip=True) if resumo_html else title
    print(f"  [{cat}] {title}")

    # Texto completo da matéria (mesmo mecanismo do pipeline de Shorts) —
    # resumo de RSS costuma ser raso demais pra gerar narração corrida decente.
    print("  Extraindo conteúdo completo da matéria...")
    conteudo = extract_article_content(item.get("link", ""))
    if conteudo and len(conteudo) > len(resumo):
        resumo = conteudo[:2500]
        print(f"  OK — {len(conteudo)} chars extraídos")
    else:
        print("  Falhou/raso — usando resumo do RSS")

    print("\n=== [2/5] Groq: cenas + ícones do vocabulário curado ===")
    cenas = gerar_roteiro_cenas(title, resumo)
    if not cenas:
        print("  Groq indisponível — fallback simples.")
        metade = resumo.rfind(" ", 0, len(resumo) // 2)
        cenas = [
            {"texto": resumo[:metade], "icones": ["news", "world"], "relacao": "lista"},
            {"texto": resumo[metade:].strip(), "icones": ["calendar", "users"], "relacao": "lista"},
        ]
    for i, c in enumerate(cenas, 1):
        print(f"  cena {i} [{c.get('relacao', 'fluxo')}]: {c['texto'][:50]}...  icones={c['icones']}")

    print("\n=== [3/5] Baixando/rasterizando ícones ===")
    for c in cenas:
        imgs = []
        for name in c["icones"]:
            svg = fetch_icon_svg(name)
            if not svg:
                continue
            png = svg_to_rgba(svg)
            if png is None:
                continue
            imgs.append(sketchify(png, seed=hash(name) & 0xFF))
        c["imgs"] = imgs
        print(f"  {len(imgs)} ícones OK: {c['icones']}")

    print("\n=== [4/5] TTS ÚNICO contínuo + sync por WordBoundary ===")
    # Voz feminina única (estilo apresentadora NotebookLM)
    voz = "pt-BR-FranciscaNeural"

    theme_icon = None
    for c in cenas:
        if c.get("imgs"):
            theme_icon = c["imgs"][0]
            break

    # Limpa cada trecho ANTES de montar o texto completo — assim a contagem de
    # palavras por cena bate com os eventos WordBoundary do áudio único.
    for c in cenas:
        c["texto_limpo"] = clean_text(c["texto"])
    full_text = " ".join(c["texto_limpo"] for c in cenas)

    audio_bytes, words = await tts_with_boundaries(full_text, voz)
    if not audio_bytes:
        print("TTS falhou. Abortando.")
        return
    full_path = os.path.join(OUTPUT_DIR, "wb_full.mp3")
    with open(full_path, "wb") as f:
        f.write(audio_bytes)
    full_audio = trim_trailing_silence(AudioFileClip(full_path))
    total = full_audio.duration
    print(f"  narração única: {total:.1f}s | {len(words)} palavras com timestamp")

    cenas_para_words = [{"texto": c["texto_limpo"]} for c in cenas]
    starts = scene_starts_from_words(cenas_para_words, words)
    # sanity: remove starts None (se WordBoundary acabou antes) e força monotônico
    for i in range(len(starts)):
        if starts[i] is None:
            starts[i] = starts[i - 1] + 2.0 if i > 0 else 0.0
    ends = starts[1:] + [total]
    durations = [max(0.6, e - s) for s, e in zip(starts, ends)]
    print("  cenas:", " | ".join(f"{d:.1f}s" for d in durations))

    # Visuais SEM áudio próprio — o áudio único entra no final, por cima de tudo
    clips, audio_refs = [], []
    # cena 1 (abertura da narração) = cartão de título
    clips.append(make_title_clip(title, cat, color, durations[0],
                                 theme_icon=theme_icon, source=source))
    for i, (c, dur) in enumerate(zip(cenas[1:], durations[1:]), 2):
        clips.append(make_scene_clip(c["texto"], c.get("imgs", []), cat, color, dur,
                                     scene_seed=i * 13, relacao=c.get("relacao", "fluxo")))

    if len(clips) < 2:
        print("Cenas insuficientes. Abortando.")
        return

    print("\n=== [5/5] Exportando ===")
    # Fade BRANCO (não preto): em fundo claro, fade preto dá "flash escuro" feio;
    # branco parece virada de página de caderno, coerente com o estilo.
    # Fades curtos: com a narração contínua (sem pausas), transição longa de
    # branco destoaria — 0.15s é só uma "virada de página" rápida.
    WHITE = [255, 255, 255]
    MIN_FADE = 0.4
    faded = [
        c.fadein(0.15, initial_color=WHITE).fadeout(0.12, final_color=WHITE)
        if c.duration > MIN_FADE else c
        for c in clips
    ]
    final = concatenate_videoclips(faded, method="compose")
    # Áudio único contínuo por cima do vídeo inteiro — zero emendas na voz
    final = final.set_audio(full_audio.subclip(0, min(total, final.duration)))
    audio_refs.append(full_audio)
    out = os.path.join(OUTPUT_DIR, "whiteboard_amostra.mp4")
    final.write_videofile(out, fps=FPS, codec="libx264", audio_codec="aac",
                          preset="fast", verbose=False, logger=None)
    dur_total = final.duration
    final.close()
    for a in audio_refs:
        a.close()

    print(f"\nVIDEO_LOCAL_PATH={out}")
    print(f"DURACAO={dur_total:.1f}s CENAS={len(clips)}")


if __name__ == "__main__":
    asyncio.run(main())
