"""
Pipeline de Novela — gera episódios de novela com IA.

Fluxo:
  Gemini/Groq gera roteiro (JSON) → TTS por personagem → imagens Pexels
  → frames 1280×720 com legenda → vídeo MP4 → YouTube

Uso:
    python novela.py
    python novela.py --sem-upload
    python novela.py --privado
    python novela.py --episodio 3   # força número do episódio
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler

import numpy as np
from dotenv import load_dotenv
from PIL import Image, ImageDraw

os.chdir(os.path.dirname(os.path.abspath(__file__)))
load_dotenv()

# -- Logging -------------------------------------------------------------------

_LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
os.makedirs(_LOG_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        RotatingFileHandler(
            os.path.join(_LOG_DIR, "novela.log"),
            maxBytes=5 * 1024 * 1024, backupCount=0, encoding="utf-8",
        ),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)
_orig_print = print
def print(*args, **kwargs):  # noqa: A001
    _orig_print(*args, **kwargs)
    msg = " ".join(str(a) for a in args)
    if msg.strip():
        log.info(msg)

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# -- Imports do projeto --------------------------------------------------------

from audio import clean_text, _stream_to_bytes
from video import _get_font, _search_pexels, _wrap_text
from config import AUDIO_OUTPUT_DIR, CHANNEL_NAME

# -- Constantes ----------------------------------------------------------------

W, H = 1280, 720
FPS = 24
NOVELA_OUTPUT_DIR = "./novela_videos"
PERSONAGENS_DIR = "./personagens"
HISTORICO_PATH = "./logs/novela_historico.json"
TITULO_SERIE = "Segredos do Coração"

NARRADOR_VOZ = "pt-BR-AntonioNeural"
NARRADOR_COR = (160, 160, 160)

MAX_CENAS_POR_EPISODIO = 18
MAX_PALAVRAS_CENA = 60

# -- Carrega personagens -------------------------------------------------------

def _load_characters() -> dict[str, dict]:
    """Carrega todos os JSONs de personagens da pasta personagens/."""
    chars: dict[str, dict] = {}
    if not os.path.exists(PERSONAGENS_DIR):
        return chars
    for f in os.listdir(PERSONAGENS_DIR):
        if f.endswith(".json"):
            try:
                with open(os.path.join(PERSONAGENS_DIR, f), encoding="utf-8") as fh:
                    data = json.load(fh)
                    chars[data["nome"]] = data
            except Exception as e:
                print(f"  Erro ao carregar personagem {f}: {e}")
    return chars


# -- Histórico de episódios ----------------------------------------------------

def _load_historico() -> list[dict]:
    try:
        with open(HISTORICO_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save_historico(ep: dict):
    hist = _load_historico()
    hist.append(ep)
    os.makedirs(os.path.dirname(HISTORICO_PATH), exist_ok=True)
    with open(HISTORICO_PATH, "w", encoding="utf-8") as f:
        json.dump(hist, f, ensure_ascii=False, indent=2)


# -- Geração do roteiro --------------------------------------------------------

def _build_prompt(chars: dict, episodio: int, historico: list) -> str:
    chars_desc = "\n".join(
        f"- {c['nome']} ({c['tipo']}): {c['personalidade']}"
        for c in chars.values()
    )
    hist_resumo = ""
    if historico:
        ultimos = historico[-3:]
        hist_resumo = "Episódios anteriores:\n" + "\n".join(
            f"- Ep {e['episodio']}: {e['sinopse']}" for e in ultimos
        )

    return f"""Você é roteirista de novelas brasileiras dramáticas.

Série: "{TITULO_SERIE}"
Episódio: {episodio}

Personagens:
{chars_desc}

{hist_resumo}

Escreva o roteiro do episódio {episodio} em formato JSON com esta estrutura EXATA:
{{
  "titulo": "título dramático do episódio",
  "sinopse": "resumo em 1-2 frases do que acontece",
  "cenas": [
    {{
      "personagem": "nome do personagem ou Narrador",
      "texto": "fala ou narração (máximo {MAX_PALAVRAS_CENA} palavras)",
      "cenario": "descrição curta do cenário em inglês para busca de imagem (ex: luxury mansion living room)"
    }}
  ]
}}

REGRAS:
- Entre {MAX_CENAS_POR_EPISODIO - 4} e {MAX_CENAS_POR_EPISODIO} cenas por episódio
- Use APENAS os nomes exatos dos personagens listados ou "Narrador"
- Comece sempre com o Narrador contextualizando a cena
- Alterne diálogos entre os personagens — drama, traição, conflito
- Cada fala máximo {MAX_PALAVRAS_CENA} palavras
- Termine com gancho dramático para o próximo episódio
- Responda APENAS com o JSON, sem markdown, sem explicações"""


def _parse_roteiro(text: str) -> dict | None:
    text = text.strip()
    text = re.sub(r"^```json\s*", "", text)
    text = re.sub(r"^```\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                pass
    return None


def _gerar_roteiro(chars: dict, episodio: int, historico: list) -> dict | None:
    prompt = _build_prompt(chars, episodio, historico)

    # Tenta Groq primeiro
    groq_key = os.getenv("GROQ_API_KEY")
    if groq_key:
        try:
            from groq import Groq
            client = Groq(api_key=groq_key)
            resp = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.85,
                max_tokens=2000,
            )
            roteiro = _parse_roteiro(resp.choices[0].message.content)
            if roteiro:
                print("  Roteiro gerado via Groq")
                return roteiro
        except Exception as e:
            print(f"  Groq falhou: {e}")

    # Fallback Gemini
    gemini_key = os.getenv("GEMINI_API_KEY")
    if gemini_key:
        try:
            from google import genai
            client = genai.Client(api_key=gemini_key)
            resp = client.models.generate_content(
                model="gemini-2.0-flash",
                contents=prompt,
            )
            roteiro = _parse_roteiro(resp.text)
            if roteiro:
                print("  Roteiro gerado via Gemini")
                return roteiro
        except Exception as e:
            print(f"  Gemini falhou: {e}")

    return None


# -- Renderização de frame -----------------------------------------------------

def _dark_gradient(img: Image.Image) -> np.ndarray:
    """Aplica gradiente escuro na metade inferior (área das legendas)."""
    arr = np.array(img.convert("RGB"), dtype=np.float32)
    gradient = np.ones((H, W), dtype=np.float32)
    fade_start = int(H * 0.55)
    for y in range(fade_start, H):
        t = (y - fade_start) / (H - fade_start)
        gradient[y] = 1.0 - (t * 0.78)
    arr[:, :, 0] *= gradient
    arr[:, :, 1] *= gradient
    arr[:, :, 2] *= gradient
    return arr.clip(0, 255).astype(np.uint8)


def _render_novela_frame(
    bg_arr: np.ndarray,
    personagem: str,
    texto: str,
    cor_personagem: tuple[int, int, int],
    episodio: int,
) -> np.ndarray:
    """Renderiza frame 1280×720 com legenda dramática estilo novela."""
    r, g, b = cor_personagem

    base = Image.fromarray(bg_arr).convert("RGBA")
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    padding = 48

    # --- Badge do canal (topo esquerdo) ---
    f_canal = _get_font(26)
    draw.text((padding, 24), f"🎭 {CHANNEL_NAME}", font=f_canal, fill=(220, 220, 220, 160))

    # --- Episódio (topo direito) ---
    ep_text = f"EP {episodio:02d}"
    f_ep = _get_font(26, bold=True)
    ep_bbox = draw.textbbox((0, 0), ep_text, font=f_ep)
    draw.text((W - ep_bbox[2] - padding, 24), ep_text, font=f_ep, fill=(r, g, b, 200))

    # --- Badge do personagem ---
    f_nome = _get_font(36, bold=True)
    nome_upper = personagem.upper()
    nome_bbox = draw.textbbox((0, 0), nome_upper, font=f_nome)
    bw = nome_bbox[2] + 28
    bh = nome_bbox[3] + 14
    badge_y = H - 160
    draw.rounded_rectangle(
        [(padding, badge_y), (padding + bw, badge_y + bh)],
        radius=8, fill=(r, g, b, 220),
    )
    draw.text((padding + 14, badge_y + 7), nome_upper, font=f_nome, fill=(255, 255, 255, 255))

    # --- Texto do diálogo ---
    f_texto = _get_font(42)
    max_w = W - padding * 2
    linhas = _wrap_text(draw, texto, f_texto, max_w)[:4]
    texto_y = badge_y + bh + 14
    for i, linha in enumerate(linhas):
        # sombra
        draw.text((padding + 2, texto_y + i * 52 + 2), linha, font=f_texto, fill=(0, 0, 0, 180))
        draw.text((padding, texto_y + i * 52), linha, font=f_texto, fill=(255, 255, 255, 240))

    merged = Image.alpha_composite(base, overlay)
    return np.array(merged.convert("RGB"))


def _kenburns_clip(frame_arr: np.ndarray, duration: float, fps: int = FPS):
    """VideoClip com efeito Ken Burns: zoom lento de 100% → 112%."""
    from moviepy.editor import VideoClip

    h, w = frame_arr.shape[:2]
    zoom_start, zoom_end = 1.0, 1.12

    def make_frame(t: float) -> np.ndarray:
        progress = min(t / max(duration, 0.001), 1.0)
        zoom = zoom_start + (zoom_end - zoom_start) * progress
        crop_w = int(w / zoom)
        crop_h = int(h / zoom)
        x0 = (w - crop_w) // 2
        y0 = (h - crop_h) // 2
        cropped = frame_arr[y0:y0 + crop_h, x0:x0 + crop_w]
        return np.array(Image.fromarray(cropped).resize((w, h), Image.LANCZOS))

    return VideoClip(make_frame, duration=duration).set_fps(fps)


# -- Pipeline principal --------------------------------------------------------

async def generate_novela_episode(
    episodio: int | None = None,
    upload: bool = True,
    privacy: str = "public",
    on_progress=None,
) -> str | None:
    """
    Gera um episódio completo da novela e faz upload no YouTube.
    Retorna o video_id ou o caminho local (se upload=False).
    """
    from moviepy.editor import AudioFileClip, concatenate_videoclips

    os.makedirs(NOVELA_OUTPUT_DIR, exist_ok=True)
    os.makedirs(AUDIO_OUTPUT_DIR, exist_ok=True)

    async def _progress(msg: str):
        print(f"  {msg}")
        if on_progress:
            try:
                await on_progress(msg)
            except Exception:
                pass

    # 1. Carrega personagens e histórico
    chars = _load_characters()
    if not chars:
        print("Nenhum personagem encontrado em personagens/. Abortando.")
        return None
    print(f"  Personagens carregados: {', '.join(chars)}")

    historico = _load_historico()
    if episodio is None:
        episodio = len(historico) + 1

    await _progress(f"[1/4] Gerando roteiro do episódio {episodio}...")

    # 2. Gera roteiro
    roteiro = _gerar_roteiro(chars, episodio, historico)
    if not roteiro or not roteiro.get("cenas"):
        print("  Falha ao gerar roteiro. Abortando.")
        return None

    titulo_ep = roteiro.get("titulo", f"Episódio {episodio}")
    sinopse = roteiro.get("sinopse", "")
    cenas = roteiro["cenas"]
    print(f"  Título: {titulo_ep}")
    print(f"  Cenas: {len(cenas)}")

    await _progress(f"[2/4] Gerando áudio e imagens ({len(cenas)} cenas)...")

    # 3. Processa cada cena
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    clips = []
    used_pexels: set = set()

    for i, cena in enumerate(cenas, 1):
        nome = cena.get("personagem", "Narrador")
        texto = clean_text(cena.get("texto", ""))
        cenario = cena.get("cenario", "dramatic scene")
        if not texto:
            continue

        # Resolve voz e cor do personagem
        char_data = chars.get(nome)
        voz = char_data["voz"] if char_data else NARRADOR_VOZ
        cor = tuple(char_data["cor"]) if char_data else NARRADOR_COR

        print(f"    Cena {i}/{len(cenas)} — {nome}")

        # Áudio TTS
        audio_path = os.path.join(AUDIO_OUTPUT_DIR, f"novela_{ts}_cena{i:02d}.mp3")
        data = await _stream_to_bytes(texto, voice=voz)
        if not data:
            print(f"    TTS falhou na cena {i}, pulando.")
            continue
        with open(audio_path, "wb") as f:
            f.write(data)

        audio_clip = AudioFileClip(audio_path)
        duration = audio_clip.duration + 0.3  # pequena pausa após a fala

        # Imagem de fundo (Pexels)
        pil_img, pexels_id = _search_pexels(cenario, orientation="landscape", exclude_ids=used_pexels)
        if pexels_id:
            used_pexels.add(pexels_id)
        if pil_img is None:
            pil_img, _ = _search_pexels("dramatic scene", orientation="landscape")
        if pil_img:
            bg = pil_img.resize((W, H), Image.LANCZOS)
            bg_arr = _dark_gradient(bg)
        else:
            bg_solid = Image.new("RGB", (W, H), (20, 20, 30))
            bg_arr = np.array(bg_solid)

        # Renderiza frame
        frame = _render_novela_frame(bg_arr, nome, texto, cor, episodio)

        clip = _kenburns_clip(frame, duration).set_audio(
            audio_clip.subclip(0, min(audio_clip.duration, duration))
        )
        clips.append(clip)
        # Guarda referência ao AudioFileClip e ao path para fechar só após o render
        clips[-1]._novela_audio_clip = audio_clip
        clips[-1]._novela_audio_path = audio_path

    if not clips:
        print("  Nenhuma cena gerada. Abortando.")
        return None

    await _progress(f"[3/4] Montando vídeo ({len(clips)} cenas)...")

    # 4. Concatena e exporta (fade in/out suave em cada cena)
    # Guard: fades só se a cena durar mais que fadein+fadeout combinados
    _MIN_FADE_DUR = 0.3 + 0.25 + 0.05
    clips_faded = [
        c.fadein(0.3).fadeout(0.25) if c.duration > _MIN_FADE_DUR else c
        for c in clips
    ]
    final = concatenate_videoclips(clips_faded, method="compose")
    output_path = os.path.join(NOVELA_OUTPUT_DIR, f"Novela_Ep{episodio:02d}_{ts}.mp4")
    final.write_videofile(
        output_path,
        fps=FPS,
        codec="libx264",
        audio_codec="aac",
        preset="fast",
        verbose=False,
        logger=None,
    )
    duracao = int(final.duration) if final.duration is not None else 0
    final.close()
    for c in clips:
        getattr(c, "_novela_audio_clip", None) and c._novela_audio_clip.close()
        try:
            os.remove(getattr(c, "_novela_audio_path", ""))
        except Exception:
            pass
        c.close()

    print(f"  Vídeo salvo: {output_path} ({duracao}s)")

    if not upload:
        _save_historico({
            "episodio": episodio,
            "titulo": titulo_ep,
            "sinopse": sinopse,
            "ts": datetime.now().isoformat(),
        })
        return output_path

    await _progress(f"[4/4] Fazendo upload no YouTube...")

    # 5. Upload YouTube
    try:
        from uploader import upload_video as yt_upload
        from playlists import add_to_playlist

        yt_title = f"{TITULO_SERIE} | {titulo_ep} | Ep {episodio:02d}"
        yt_desc = (
            f"📺 {TITULO_SERIE} — Episódio {episodio:02d}\n\n"
            f"{sinopse}\n\n"
            f"#Novela #NovelaBrasileira #IA #Shorts #Drama"
        )
        yt_tags = ["novela", "drama", "ia", "novela brasileira", TITULO_SERIE.lower()]

        video_id = yt_upload(output_path, yt_title, yt_desc, yt_tags, privacy=privacy)
        print(f"  YouTube: https://youtu.be/{video_id}")

        _save_historico({
            "episodio": episodio,
            "titulo": titulo_ep,
            "sinopse": sinopse,
            "ts": datetime.now().isoformat(),
        })

        try:
            add_to_playlist(video_id, "novela")
        except Exception as e:
            print(f"  add_to_playlist falhou: {e}")

        try:
            os.remove(output_path)
        except Exception:
            pass

        return video_id

    except Exception as e:
        print(f"  Erro no upload: {e}")
        return None


# -- CLI -----------------------------------------------------------------------

async def _main():
    parser = argparse.ArgumentParser(description="Pipeline de Novela IA")
    parser.add_argument("--sem-upload", action="store_true")
    parser.add_argument("--privado", action="store_true")
    parser.add_argument("--episodio", type=int, default=None)
    args = parser.parse_args()

    privacy = "private" if args.privado else "public"
    upload = not args.sem_upload

    if upload:
        from uploader import check_youtube_token
        ok, msg = check_youtube_token()
        if not ok:
            print(f"❌ Token YouTube inválido: {msg}")
            sys.exit(1)

    print(f"\n=== NOVELA IA — {TITULO_SERIE} ===")
    print(f"Upload: {upload} | Privacidade: {privacy}")
    if args.episodio:
        print(f"Episódio forçado: {args.episodio}")

    result = await generate_novela_episode(
        episodio=args.episodio,
        upload=upload,
        privacy=privacy,
    )

    if result:
        print(f"\nConcluído: {result}")
    else:
        print("\nPipeline falhou.")


if __name__ == "__main__":
    asyncio.run(_main())
