"""
clipper.py — Corta vídeos do YouTube em Shorts com legendas animadas (karaoke).

Fluxo:
    URL YouTube → yt-dlp download → faster-whisper transcrição (segmentos + word timestamps) →
    LLM seleciona melhores frases por tema → corte preciso em boundary de frase →
    legenda karaoke animada → salva local (ou upload YouTube)

Uso (teste — sem upload):
    python clipper.py --url "https://youtu.be/..." --clips 3 --sem-upload
    python clipper.py --url "..." --clips 3 --tema "inteligência artificial" --sem-upload

Uso (produção):
    python clipper.py --url "..." --clips 3
    python clipper.py --url "..." --clips 3 --privado

Dependências novas:
    pip install faster-whisper yt-dlp
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime

import numpy as np
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont

load_dotenv()

from config import AUDIO_OUTPUT_DIR, DRIVE_SYNC_DIR

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
_LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
os.makedirs(_LOG_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.FileHandler(os.path.join(_LOG_DIR, "clipper.log"), encoding="utf-8"),
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


# ---------------------------------------------------------------------------
# Configurações
# ---------------------------------------------------------------------------
SHORTS_W         = 1080
SHORTS_H         = 1920
WORDS_PER_CHUNK  = 3      # palavras exibidas por vez na legenda
CAPTION_FONT_SZ  = 72     # tamanho da fonte da legenda
WHISPER_MODEL    = "base" # tiny | base | small | medium (base = bom custo-benefício na VPS)
MAX_CLIP_DURATION = 89    # segundos máximos por clipe (Short limit = 3 min)
MIN_CLIP_DURATION = 30    # segundos mínimos


# ---------------------------------------------------------------------------
# Estruturas de dados
# ---------------------------------------------------------------------------

@dataclass
class WordInfo:
    word:  str
    start: float
    end:   float


@dataclass
class SegmentInfo:
    """Segmento de frase do Whisper — unidade de corte natural."""
    idx:   int
    text:  str           # texto completo da frase
    start: float
    end:   float
    words: list[WordInfo]


@dataclass
class ClipSegment:
    start:  float
    end:    float
    reason: str = ""


@dataclass
class CaptionState:
    words:      list[str]   # palavras do chunk atual
    active_idx: int         # índice da palavra sendo falada
    t_start:    float       # tempo relativo ao início do clipe
    t_end:      float


# ---------------------------------------------------------------------------
# 1. Download via yt-dlp
# ---------------------------------------------------------------------------

def download_youtube_video(url: str, output_dir: str) -> str:
    """Baixa o vídeo em até 720p. Retorna o caminho do .mp4."""
    os.makedirs(output_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    template = os.path.join(output_dir, f"yt_{ts}.%(ext)s")

    cmd = [
        "yt-dlp",
        "--format",
        "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]/best[height<=720]",
        "--merge-output-format", "mp4",
        "--output", template,
        "--no-playlist",
        url,
    ]

    print(f"  Baixando: {url}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp falhou:\n{result.stderr[:600]}")

    for fname in os.listdir(output_dir):
        if fname.startswith(f"yt_{ts}") and fname.endswith(".mp4"):
            path = os.path.join(output_dir, fname)
            size_mb = os.path.getsize(path) / 1_048_576
            print(f"  Download OK: {fname} ({size_mb:.1f} MB)")
            return path

    raise FileNotFoundError("Arquivo .mp4 baixado não encontrado.")


# ---------------------------------------------------------------------------
# 2. Transcrição com timestamps por palavra (faster-whisper)
# ---------------------------------------------------------------------------

def transcribe_video(video_path: str) -> tuple[list[WordInfo], list[SegmentInfo]]:
    """
    Transcreve com timestamps por palavra E por segmento de frase.
    Retorna (words, segments).

    - words: lista completa de palavras com timestamps individuais (para legenda karaoke)
    - segments: frases completas do Whisper com índice (para seleção precisa de cortes)
    """
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise ImportError(
            "faster-whisper não instalado. Execute:\n  pip install faster-whisper"
        )

    print(f"  Carregando modelo Whisper ({WHISPER_MODEL})...")
    model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")

    print("  Transcrevendo...")
    raw_segments, info = model.transcribe(
        video_path,
        language="pt",
        word_timestamps=True,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
    )

    all_words:    list[WordInfo]    = []
    all_segments: list[SegmentInfo] = []

    for idx, seg in enumerate(raw_segments):
        seg_words: list[WordInfo] = []
        if seg.words:
            for w in seg.words:
                word_clean = w.word.strip()
                if word_clean:
                    wi = WordInfo(word=word_clean, start=w.start, end=w.end)
                    seg_words.append(wi)
                    all_words.append(wi)

        all_segments.append(SegmentInfo(
            idx=idx,
            text=seg.text.strip(),
            start=seg.start,
            end=seg.end,
            words=seg_words,
        ))

    print(
        f"  Transcrição OK — {len(all_words)} palavras | "
        f"{len(all_segments)} frases | duração: {info.duration:.0f}s"
    )
    return all_words, all_segments


# ---------------------------------------------------------------------------
# 3. Seleção dos melhores momentos via LLM (baseada em segmentos de frase)
# ---------------------------------------------------------------------------

def _segments_for_llm(segments: list[SegmentInfo], max_chars: int = 7000) -> str:
    """
    Formata os segmentos de frase do Whisper para o LLM.
    Cada linha = 1 frase completa com índice e timestamps.
    Ex: [12] (45.2s→48.7s) "Isso é o maior problema da inteligência artificial hoje."
    """
    lines = []
    for seg in segments:
        lines.append(f"[{seg.idx}] ({seg.start:.1f}s→{seg.end:.1f}s) \"{seg.text}\"")
    full = "\n".join(lines)
    return full[:max_chars]


def select_best_clips(
    segments: list[SegmentInfo],
    words: list[WordInfo],
    n: int = 3,
    tema: str = "",
    target_duration: int = 60,
) -> list[ClipSegment]:
    """
    Usa LLM para selecionar os N melhores trechos a partir dos segmentos de frase.
    O LLM escolhe ÍNDICES de segmento (não timestamps) → cortes sempre em boundaries naturais.

    Args:
        segments:        frases do Whisper com índice
        words:           palavras individuais (para fallback de timestamps)
        n:               quantidade de clipes
        tema:            tema específico para focar os cortes (opcional)
        target_duration: duração alvo de cada clipe em segundos
    """
    groq_key   = os.getenv("GROQ_API_KEY", "")
    gemini_key = os.getenv("GEMINI_API_KEY", "")

    if not segments:
        return []

    total       = segments[-1].end
    transcript  = _segments_for_llm(segments)
    max_seg_idx = segments[-1].idx

    tema_instrucao = (
        f"FOCO DO TEMA: selecione apenas trechos que falem sobre \"{tema}\".\n"
        f"Ignore momentos que não sejam diretamente relacionados a esse tema.\n\n"
        if tema else
        ""
    )

    prompt = (
        f"Você é um editor especialista em conteúdo viral para YouTube Shorts.\n\n"
        f"Abaixo está a transcrição de um vídeo de {total:.0f}s dividida em frases numeradas.\n"
        f"Selecione os {n} melhores trechos contínuos para se tornarem YouTube Shorts virais.\n\n"
        f"{tema_instrucao}"
        f"Critérios de seleção:\n"
        f"- Começo que prenda em 2 segundos (frase de impacto, pergunta forte, revelação)\n"
        f"- Trecho com ideia completa — não corta no meio de um raciocínio\n"
        f"- Entre {MIN_CLIP_DURATION}s e {MAX_CLIP_DURATION}s de duração total\n"
        f"- Frases consecutivas que formem uma narrativa coesa\n\n"
        f"TRANSCRIÇÃO (índice → timestamps → frase):\n{transcript}\n\n"
        f"Responda APENAS com JSON válido usando ÍNDICES de segmento:\n"
        f'[{{"start_seg": 12, "end_seg": 28, "reason": "motivo breve em português"}}, ...]\n'
        f"start_seg e end_seg são índices inteiros das frases acima (0 a {max_seg_idx}).\n"
        f"O trecho inclui todas as frases de start_seg até end_seg (inclusive)."
    )

    def _parse_to_clips(text: str) -> list[ClipSegment]:
        m = re.search(r'\[.*?\]', text, re.DOTALL)
        if not m:
            return []
        try:
            data = json.loads(m.group())
        except json.JSONDecodeError:
            return []

        seg_by_idx = {s.idx: s for s in segments}
        clips = []

        for item in data:
            i_start = int(item.get("start_seg", 0))
            i_end   = int(item.get("end_seg", i_start + 5))
            i_start = max(0, min(i_start, max_seg_idx))
            i_end   = max(i_start, min(i_end, max_seg_idx))

            seg_s = seg_by_idx.get(i_start)
            seg_e = seg_by_idx.get(i_end)

            if not seg_s or not seg_e:
                continue

            start = seg_s.start
            end   = seg_e.end
            dur   = end - start

            if dur < MIN_CLIP_DURATION:
                continue
            # Se ultrapassar o máximo, encurta retroativamente procurando boundary
            if dur > MAX_CLIP_DURATION:
                for back_idx in range(i_end, i_start, -1):
                    seg_back = seg_by_idx.get(back_idx)
                    if seg_back and seg_back.end - start <= MAX_CLIP_DURATION:
                        end = seg_back.end
                        break

            clips.append(ClipSegment(
                start=start,
                end=end,
                reason=item.get("reason", ""),
            ))

        return clips[:n]

    # Groq primário
    if groq_key:
        try:
            from groq import Groq
            client = Groq(api_key=groq_key)
            resp = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
            )
            clips = _parse_to_clips(resp.choices[0].message.content)
            if clips:
                print(f"  [Groq] {len(clips)} trecho(s) selecionado(s)")
                return clips
        except Exception as e:
            print(f"  Groq falhou: {e}")

    # Gemini fallback
    if gemini_key:
        try:
            from google import genai as google_genai
            gclient = google_genai.Client(api_key=gemini_key)
            response = gclient.models.generate_content(
                model="gemini-2.0-flash", contents=prompt
            )
            clips = _parse_to_clips(response.text)
            if clips:
                print(f"  [Gemini] {len(clips)} trecho(s) selecionado(s)")
                return clips
        except Exception as e:
            print(f"  Gemini falhou: {e}")

    # Fallback: divide uniformemente em N partes
    print("  LLM indisponível — divisão automática em partes iguais")
    step = (total - 30) / max(n, 1)
    return [
        ClipSegment(
            start=max(0.0, i * step),
            end=min(total, i * step + target_duration),
        )
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# 4. Legenda animada (karaoke)
# ---------------------------------------------------------------------------

def _find_font(size: int) -> ImageFont.FreeTypeFont:
    """Encontra uma fonte bold disponível no sistema."""
    candidates = [
        # Linux
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf",
        "/usr/share/fonts/liberation/LiberationSans-Bold.ttf",
        # Windows
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/Arial Bold.ttf",
        # macOS
        "/Library/Fonts/Arial Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    # Fallback sem bold
    return ImageFont.load_default()


def _build_caption_states(
    words: list[WordInfo],
    clip_start: float,
    clip_end: float,
) -> list[CaptionState]:
    """
    Agrupa as palavras em chunks de WORDS_PER_CHUNK e gera um CaptionState
    para cada transição de palavra dentro do chunk.
    """
    clip_words = [w for w in words if clip_start <= w.start < clip_end]
    if not clip_words:
        return []

    states: list[CaptionState] = []
    total_clip = clip_end - clip_start

    for chunk_i in range(0, len(clip_words), WORDS_PER_CHUNK):
        chunk = clip_words[chunk_i : chunk_i + WORDS_PER_CHUNK]
        chunk_texts = [w.word for w in chunk]

        for j, word in enumerate(chunk):
            t_start = word.start - clip_start

            if j + 1 < len(chunk):
                # Próxima palavra dentro do mesmo chunk
                t_end = chunk[j + 1].start - clip_start
            else:
                # Fim do chunk: próxima palavra do próximo chunk ou fim do clipe
                next_idx = chunk_i + WORDS_PER_CHUNK
                if next_idx < len(clip_words):
                    t_end = clip_words[next_idx].start - clip_start
                else:
                    t_end = total_clip

            t_start = max(0.0, t_start)
            t_end   = min(total_clip, t_end)

            if t_end > t_start:
                states.append(CaptionState(
                    words=chunk_texts,
                    active_idx=j,
                    t_start=t_start,
                    t_end=t_end,
                ))

    return states


def _render_caption_frame(
    state: CaptionState,
    font: ImageFont.FreeTypeFont,
    w: int,
    h: int,
) -> np.ndarray:
    """
    Renderiza um frame RGBA com o chunk de palavras da legenda.
    Palavra ativa: amarelo brilhante | demais: branco | fundo: pílula escura.
    """
    img  = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Mede largura de cada palavra (com espaço)
    word_widths = []
    for word in state.words:
        bb = draw.textbbox((0, 0), word + " ", font=font)
        word_widths.append(bb[2] - bb[0])

    total_text_w = sum(word_widths)
    line_h = CAPTION_FONT_SZ + 12

    pad_x, pad_y = 36, 20
    bg_w = total_text_w + pad_x * 2
    bg_h = line_h + pad_y * 2

    # Posição: 72% da altura da tela
    x_bg = (w - bg_w) // 2
    y_bg = int(h * 0.72)

    # Fundo arredondado semi-transparente
    draw.rounded_rectangle(
        [x_bg, y_bg, x_bg + bg_w, y_bg + bg_h],
        radius=22,
        fill=(0, 0, 0, 185),
    )

    # Texto palavra a palavra
    x = x_bg + pad_x
    y = y_bg + pad_y
    for i, (word, ww) in enumerate(zip(state.words, word_widths)):
        color = (255, 220, 0, 255) if i == state.active_idx else (255, 255, 255, 215)
        draw.text((x, y), word, font=font, fill=color)
        x += ww

    return np.array(img)


def _precompute_captions(
    states: list[CaptionState],
    w: int,
    h: int,
) -> list[tuple[float, float, np.ndarray]]:
    """Pré-computa uma imagem RGBA para cada estado de legenda."""
    font = _find_font(CAPTION_FONT_SZ)
    return [
        (s.t_start, s.t_end, _render_caption_frame(s, font, w, h))
        for s in states
    ]


# ---------------------------------------------------------------------------
# 5. Renderização do clipe com legendas
# ---------------------------------------------------------------------------

def render_clip(
    video_path: str,
    segment: ClipSegment,
    words: list[WordInfo],
    output_path: str,
) -> str:
    """
    Corta o trecho, converte para retrato 1080×1920, sobrepõe legendas animadas.
    Retorna o caminho do vídeo gerado.
    """
    from moviepy.editor import VideoFileClip, ImageClip, CompositeVideoClip

    duration = segment.end - segment.start
    print(f"  Cortando {segment.start:.1f}s → {segment.end:.1f}s  ({duration:.1f}s)")

    video = VideoFileClip(video_path).subclip(segment.start, segment.end)

    # --- Crop portrait (center) + resize ---
    vw, vh = video.size
    target_vw = int(vh * 9 / 16)

    if target_vw <= vw:
        x1 = (vw - target_vw) // 2
        video_cropped = video.crop(x1=x1, width=target_vw)
    else:
        # Vídeo mais estreito que 9:16 — aceita sem crop
        video_cropped = video

    video_portrait = video_cropped.resize((SHORTS_W, SHORTS_H))

    # --- Legendas animadas ---
    caption_states = _build_caption_states(words, segment.start, segment.end)

    if not caption_states:
        print("  (sem palavras transcritas neste trecho — sem legenda)")
        video_portrait.write_videofile(
            output_path, fps=24, codec="libx264", audio_codec="aac",
            preset="fast", verbose=False, logger=None,
        )
        video_portrait.close()
        video.close()
        return output_path

    precomputed = _precompute_captions(caption_states, SHORTS_W, SHORTS_H)
    print(f"  {len(precomputed)} estados de legenda pré-computados")

    # Cria um ImageClip para cada estado
    caption_clips = []
    for t_start, t_end, img_arr in precomputed:
        dur = min(t_end, duration) - t_start
        if dur <= 0:
            continue
        cap = (
            ImageClip(img_arr, ismask=False)
            .set_start(t_start)
            .set_duration(dur)
        )
        caption_clips.append(cap)

    final = CompositeVideoClip(
        [video_portrait] + caption_clips,
        size=(SHORTS_W, SHORTS_H),
    ).set_audio(video_portrait.audio)

    final.write_videofile(
        output_path,
        fps=24,
        codec="libx264",
        audio_codec="aac",
        preset="fast",
        verbose=False,
        logger=None,
    )

    final.close()
    video_portrait.close()
    video.close()
    return output_path


# ---------------------------------------------------------------------------
# 6. Pipeline principal
# ---------------------------------------------------------------------------

async def run_clipper(
    url: str,
    n_clips: int = 3,
    upload: bool = False,       # padrão FALSE — teste local primeiro
    privacy: str = "public",
    tema: str = "",             # tema/assunto para focar os cortes
    on_progress=None,
    hashtags: list[str] | None = None,
    playlist_key: str = "clips",
) -> list[str]:
    """
    Pipeline completo: URL YouTube → Shorts com legendas animadas.

    Por padrão upload=False — salva os clipes localmente para revisão.
    Para postar no YouTube, passe upload=True explicitamente.

    Retorna lista de caminhos locais (upload=False) ou video_ids (upload=True).
    """
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Pasta de saída: video_news/clips/<timestamp>/ — fácil de achar
    clips_base = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "video_news", "clips"
    )
    work_dir = os.path.join(clips_base, ts)
    os.makedirs(work_dir, exist_ok=True)

    print(f"\n--- Clipper: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')} ---")
    print(f"  URL: {url}")
    print(f"  Clipes: {n_clips} | Tema: '{tema or 'automático'}' | Upload: {upload}")

    # 1. Download
    print("\n[1/4] Baixando vídeo...")
    if on_progress:
        try: await on_progress("⬇️ Baixando vídeo do YouTube...")
        except Exception: pass

    try:
        video_path = download_youtube_video(url, work_dir)
    except Exception as e:
        print(f"❌ Erro no download: {e}")
        return []

    # 2. Transcrição
    print("\n[2/4] Transcrevendo áudio (Whisper)...")
    if on_progress:
        try: await on_progress("🎙️ Transcrevendo com Whisper...")
        except Exception: pass

    try:
        words, whisper_segs = transcribe_video(video_path)
    except Exception as e:
        print(f"❌ Erro na transcrição: {e}")
        return []

    if not words or not whisper_segs:
        print("❌ Nenhuma palavra transcrita. Abortando.")
        return []

    # Salva a transcrição completa em texto para revisão
    transcript_path = os.path.join(work_dir, "transcript.txt")
    with open(transcript_path, "w", encoding="utf-8") as f:
        for seg in whisper_segs:
            f.write(f"[{seg.idx}] ({seg.start:.1f}s→{seg.end:.1f}s) {seg.text}\n")
    print(f"  Transcrição salva: {transcript_path}")

    # 3. Seleção de clipes
    tema_label = f" (tema: '{tema}')" if tema else ""
    print(f"\n[3/4] Selecionando {n_clips} melhores momentos{tema_label}...")
    if on_progress:
        try: await on_progress(f"🤖 Selecionando {n_clips} melhores momentos...")
        except Exception: pass

    clip_segments = select_best_clips(whisper_segs, words, n=n_clips, tema=tema)
    if not clip_segments:
        print("❌ Nenhum segmento selecionado.")
        return []

    print(f"\n  {'─'*50}")
    for i, seg in enumerate(clip_segments, 1):
        dur = seg.end - seg.start
        print(f"  Clipe {i}: {seg.start:.1f}s → {seg.end:.1f}s  ({dur:.0f}s)")
        print(f"           {seg.reason[:80]}")
    print(f"  {'─'*50}")

    # 4. Renderizar
    print(f"\n[4/4] Renderizando {len(clip_segments)} clipes...")
    if on_progress:
        try: await on_progress(f"🎬 Renderizando {len(clip_segments)} clipes...")
        except Exception: pass

    if hashtags is None:
        hashtags = ["Shorts", "Clips", "Podcast", "Brasil"]

    results: list[str] = []   # caminhos locais ou video_ids

    for i, seg in enumerate(clip_segments, 1):
        print(f"\n  ── Clipe {i}/{len(clip_segments)} ──")
        clip_path = os.path.join(work_dir, f"clip_{i:02d}.mp4")

        try:
            render_clip(video_path, seg, words, clip_path)
        except Exception as e:
            print(f"  ❌ Erro ao renderizar: {e}")
            continue

        if not upload:
            print(f"  💾 Salvo: {clip_path}")
            results.append(clip_path)
            continue

        # Título: primeiras palavras do trecho
        trecho_words = [w for w in words if seg.start <= w.start < seg.end]
        title_preview = " ".join(w.word for w in trecho_words[:8]).strip()
        yt_title = f"{title_preview}... #Shorts"
        hash_line = " ".join(f"#{h}" for h in hashtags)
        yt_desc   = f"Clipe gerado automaticamente.\n\n{hash_line}"

        try:
            from uploader import upload_video as yt_upload
            from playlists import add_to_playlist

            video_id = yt_upload(clip_path, yt_title, yt_desc, hashtags, privacy=privacy)
            if video_id:
                results.append(video_id)
                print(f"  ✅ https://youtu.be/{video_id}")
                try:
                    add_to_playlist(video_id, playlist_key)
                except Exception:
                    pass
        except Exception as e:
            print(f"  ❌ Erro no upload: {e}")

        try:
            os.remove(clip_path)
        except Exception:
            pass

        # Espaçamento entre uploads
        if upload and i < len(clip_segments):
            print(f"\n  ⏳ Aguardando 10 min antes do próximo clipe...")
            await asyncio.sleep(600)

    # Remove apenas o vídeo original (clipes ficam na pasta para revisão)
    try:
        os.remove(video_path)
    except Exception:
        pass

    print(f"\n{'─'*50}")
    if not upload:
        print(f"✅ {len(results)} clipe(s) salvos em:\n   {work_dir}")
        print(f"   Transcrição: {transcript_path}")
    else:
        # Notificação Telegram
        try:
            from telegram_notifier import notify
            if results:
                notify(
                    f"✂️ <b>Clipper:</b> {len(results)} Short(s) publicado(s)!\n"
                    f"Primeiro: https://youtu.be/{results[0]}"
                )
            else:
                notify("⚠️ <b>Clipper:</b> nenhum clipe enviado ao YouTube.")
        except Exception:
            pass

    return results


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog="clipper.py", add_help=True)
    parser.add_argument("--url",     required=True, help="URL do vídeo do YouTube")
    parser.add_argument("--clips",   type=int, default=3, help="Número de clipes (padrão 3)")
    parser.add_argument("--tema",    type=str, default="", help="Tema/assunto para focar os cortes")
    parser.add_argument("--upload",  action="store_true", help="Faz upload no YouTube (padrão: só salva local)")
    parser.add_argument("--privado", action="store_true", help="Publica como privado")
    args, _ = parser.parse_known_args()

    asyncio.run(run_clipper(
        url=args.url,
        n_clips=args.clips,
        tema=args.tema,
        upload=args.upload,          # padrão False — teste seguro
        privacy="private" if args.privado else "public",
    ))
