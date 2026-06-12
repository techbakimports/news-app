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
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime

import numpy as np
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont

# Patch de compatibilidade: PIL.Image.ANTIALIAS foi removido no Pillow 10+
# moviepy < 2.0 ainda usa ANTIALIAS internamente
if not hasattr(Image, "ANTIALIAS"):
    Image.ANTIALIAS = Image.LANCZOS  # type: ignore[attr-defined]

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
        RotatingFileHandler(os.path.join(_LOG_DIR, "clipper.log"), maxBytes=5*1024*1024, backupCount=0, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

# Garante UTF-8 no stdout/stderr (Windows usa cp1252 por padrão)
import sys as _sys
if hasattr(_sys.stdout, "reconfigure"):
    try:
        _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        _sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

_orig_print = print


def print(*args, **kwargs):  # noqa: A001
    _orig_print(*args, **kwargs)
    msg = " ".join(str(a) for a in args)
    if msg.strip():
        log.info(msg)


# ---------------------------------------------------------------------------
# Configurações
# ---------------------------------------------------------------------------
SHORTS_W          = 1080
SHORTS_H          = 1920
WORDS_PER_CHUNK   = 3     # palavras exibidas por vez na legenda
CAPTION_FONT_SZ   = 88    # tamanho da fonte (maior = mais legível no celular)
CAPTION_STROKE    = 5     # espessura do contorno preto nas letras
CAPTION_PADDING   = 40    # margem mínima de cada lado (evita texto saindo da borda)
CAPTION_LINE_GAP  = 8     # espaço entre linhas quando há quebra
WHISPER_MODEL     = "base" # tiny | base | small | medium (base = bom custo-benefício na VPS)
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
# 2b. Cache de transcrição (JSON) — evita re-transcrever o mesmo vídeo
# ---------------------------------------------------------------------------

_CACHE_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "video_news", "clips", "_cache"
)


def _transcript_cache_path(url: str) -> str:
    """Retorna o caminho do arquivo de cache para uma URL."""
    key = hashlib.md5(url.encode()).hexdigest()
    os.makedirs(_CACHE_DIR, exist_ok=True)
    return os.path.join(_CACHE_DIR, f"{key}.json")


def _load_transcript_cache(url: str) -> tuple[list[WordInfo], list[SegmentInfo]] | None:
    """
    Carrega transcrição cacheada para a URL, se existir.
    Retorna (words, segments) ou None se não houver cache.
    """
    path = _transcript_cache_path(url)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        words = [WordInfo(**w) for w in data["words"]]
        segments = [
            SegmentInfo(
                idx=s["idx"],
                text=s["text"],
                start=s["start"],
                end=s["end"],
                words=[WordInfo(**w) for w in s["words"]],
            )
            for s in data["segments"]
        ]
        cached_at = data.get("cached_at", "?")
        print(f"  Cache de transcrição encontrado (salvo em {cached_at})")
        print(f"  {len(words)} palavras | {len(segments)} frases — pulando Whisper")
        return words, segments
    except Exception as e:
        print(f"  Cache corrompido ({e}) — re-transcrevendo")
        return None


def _save_transcript_cache(
    url: str,
    words: list[WordInfo],
    segments: list[SegmentInfo],
) -> None:
    """Salva words + segments como JSON para re-uso em runs futuras."""
    path = _transcript_cache_path(url)
    data = {
        "url": url,
        "cached_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "words": [{"word": w.word, "start": w.start, "end": w.end} for w in words],
        "segments": [
            {
                "idx": s.idx,
                "text": s.text,
                "start": s.start,
                "end": s.end,
                "words": [{"word": w.word, "start": w.start, "end": w.end} for w in s.words],
            }
            for s in segments
        ],
    }
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        size_mb = os.path.getsize(path) / 1_048_576
        print(f"  Transcrição cacheada: {path} ({size_mb:.1f} MB)")
    except Exception as e:
        print(f"  Aviso: não foi possível salvar cache ({e})")


# ---------------------------------------------------------------------------
# 3. Seleção dos melhores momentos via LLM (baseada em segmentos de frase)
# ---------------------------------------------------------------------------

def _segments_for_llm(segments: list[SegmentInfo], max_segs: int = 400) -> str:
    """
    Formata segmentos de frase para o LLM, amostrando uniformemente o vídeo inteiro.
    Para podcasts longos (milhares de segmentos), amostra max_segs igualmente distribuídos
    para que o LLM veja o início, meio e fim — não só os primeiros minutos.

    Ex: [12] (45.2s->48.7s) "Isso e o maior problema da IA hoje."
    """
    if not segments:
        return ""

    # Amostragem uniforme: se tiver mais segmentos que max_segs, pega 1 a cada N
    if len(segments) > max_segs:
        step = len(segments) / max_segs
        sampled = [segments[int(i * step)] for i in range(max_segs)]
    else:
        sampled = segments

    lines = []
    for seg in sampled:
        # Sem caracteres especiais que possam quebrar encoding no log
        text = seg.text.replace('"', "'")
        lines.append(f"[{seg.idx}] ({seg.start:.0f}s->{seg.end:.0f}s) \"{text}\"")
    return "\n".join(lines)


def select_best_clips(
    segments: list[SegmentInfo],
    words: list[WordInfo],
    n: int = 3,
    tema: str = "",
    target_duration: int = 60,
) -> list[ClipSegment]:
    """
    Seleção em 2 fases para cortes precisos:

    Fase 1 (coarse) — LLM vê até 400 segmentos amostrados do vídeo inteiro e retorna
    N tempos centrais onde o tema está mais intenso.

    Fase 2 (precise) — Para cada centro, pega TODOS os segmentos reais em ±2 min e
    pede ao LLM o start/end exato da discussão completa sobre o assunto.

    Resultado: cortes que começam antes do assunto ser introduzido e terminam
    depois da conclusão, nunca no meio de uma frase ou raciocínio.
    """
    groq_key   = os.getenv("GROQ_API_KEY", "")
    gemini_key = os.getenv("GEMINI_API_KEY", "")

    if not segments:
        return []

    total       = segments[-1].end
    max_seg_idx = segments[-1].idx
    seg_by_idx  = {s.idx: s for s in segments}

    # ── Helper: chama Groq → Gemini, retorna texto ou None ──────────────────
    def _call_llm(prompt: str) -> str | None:
        if groq_key:
            try:
                from groq import Groq
                resp = Groq(api_key=groq_key).chat.completions.create(
                    model="llama-3.3-70b-versatile",
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.2,
                )
                return resp.choices[0].message.content
            except Exception as e:
                print(f"  Groq falhou: {e}")
        if gemini_key:
            try:
                try:
                    from google import genai as _genai
                    return _genai.Client(api_key=gemini_key).models.generate_content(
                        model="gemini-2.0-flash", contents=prompt
                    ).text
                except ImportError:
                    import google.generativeai as _genai2
                    _genai2.configure(api_key=gemini_key)
                    return _genai2.GenerativeModel("gemini-2.0-flash").generate_content(prompt).text
            except Exception as e:
                print(f"  Gemini falhou: {e}")
        return None

    # ── Helper: converte range de índices em ClipSegment respeitando limites ─
    def _clip_from_idx(i_start: int, i_end: int, reason: str = "") -> ClipSegment | None:
        i_start = max(0, min(i_start, max_seg_idx))
        i_end   = max(i_start, min(i_end, max_seg_idx))
        seg_s = seg_by_idx.get(i_start)
        seg_e = seg_by_idx.get(i_end)
        if not seg_s or not seg_e:
            return None
        start = seg_s.start
        end   = seg_e.end
        if end - start < MIN_CLIP_DURATION:
            return None
        if end - start > MAX_CLIP_DURATION:
            for back in range(i_end, i_start, -1):
                sb = seg_by_idx.get(back)
                if sb and sb.end - start <= MAX_CLIP_DURATION:
                    end = sb.end
                    break
        return ClipSegment(start=start, end=end, reason=reason)

    # ── Fase 1: coarse — identifica N tempos centrais no vídeo inteiro ───────
    tema_inst = (
        f"FOCO DO TEMA: apenas trechos diretamente relacionados a \"{tema}\".\n\n"
        if tema else ""
    )
    coarse_prompt = (
        f"Você é um editor de YouTube Shorts especializado em conteúdo viral.\n\n"
        f"Transcrição amostrada de um vídeo de {total:.0f}s (frases do vídeo inteiro):\n"
        f"{_segments_for_llm(segments)}\n\n"
        f"{tema_inst}"
        f"Identifique os {n} momentos mais virais e impactantes. "
        f"Para cada um, retorne o tempo (em segundos) onde o assunto está mais intenso.\n\n"
        f"Responda APENAS JSON:\n"
        f'[{{"center_time": 1234, "reason": "descrição do assunto em português"}}, ...]\n'
        f"center_time: número inteiro de segundos (0 a {int(total)})."
    )

    coarse_text = _call_llm(coarse_prompt)
    coarse_regions: list[dict] = []
    if coarse_text:
        m = re.search(r'\[.*?\]', coarse_text, re.DOTALL)
        if m:
            try:
                for item in json.loads(m.group()):
                    ct = item.get("center_time")
                    if ct is not None:
                        coarse_regions.append({
                            "center_time": max(0.0, min(float(ct), total)),
                            "reason": item.get("reason", ""),
                        })
            except Exception:
                pass

    if not coarse_regions:
        print("  LLM indisponível — divisão automática em partes iguais")
        step = (total - 30) / max(n, 1)
        return [
            ClipSegment(
                start=max(0.0, i * step),
                end=min(total, i * step + target_duration),
            )
            for i in range(n)
        ]

    print(f"  [Fase 1] {len(coarse_regions)} região(ões) identificada(s)")

    # ── Fase 2: precise — corte exato em janela completa ±2 min ─────────────
    clips: list[ClipSegment] = []
    window = max(target_duration + 30, 120)   # segundos de cada lado do centro

    for region in coarse_regions[:n]:
        center = region["center_time"]
        reason = region["reason"]

        # Todos os segmentos reais no intervalo — sem amostragem
        local_segs = [s for s in segments if center - window <= s.start <= center + window]
        if not local_segs:
            local_segs = [s for s in segments
                          if abs(s.start - center) == min(abs(s2.start - center) for s2 in segments)]

        local_text = "\n".join(
            f'[{s.idx}] ({s.start:.0f}s->{s.end:.0f}s) "{s.text.replace(chr(34), chr(39))}"'
            for s in local_segs
        )
        lo_idx = local_segs[0].idx
        hi_idx = local_segs[-1].idx

        tema_local = f"Foco no tema: \"{tema}\".\n" if tema else ""
        precise_prompt = (
            f"Você é um editor de vídeo. Trecho de um podcast ao redor do tempo {center:.0f}s.\n\n"
            f"{tema_local}"
            f"Assunto identificado: \"{reason}\"\n\n"
            f"Encontre o corte que captura essa discussão COMPLETA:\n"
            f"- Comece ANTES de o assunto ser introduzido (não no meio de uma fala)\n"
            f"- Termine DEPOIS da conclusão do raciocínio (não corte antes do fim)\n"
            f"- Duração entre {MIN_CLIP_DURATION}s e {MAX_CLIP_DURATION}s\n"
            f"- Prefira frases de impacto ou revelação para começar\n\n"
            f"TRANSCRIÇÃO COMPLETA desta região:\n{local_text}\n\n"
            f"Responda APENAS JSON com índices reais desta transcrição:\n"
            f'[{{"start_seg": {lo_idx}, "end_seg": {hi_idx}, "reason": "motivo do corte"}}]\n'
            f"Use índices inteiros de {lo_idx} a {hi_idx}."
        )

        precise_text = _call_llm(precise_prompt)
        added = False
        if precise_text:
            m = re.search(r'\[.*?\]', precise_text, re.DOTALL)
            if m:
                try:
                    for item in json.loads(m.group()):
                        rs = item.get("start_seg")
                        re_ = item.get("end_seg")
                        if rs is None or re_ is None:
                            continue
                        clip = _clip_from_idx(int(rs), int(re_), item.get("reason", reason))
                        if clip:
                            clips.append(clip)
                            print(
                                f"  [Fase 2] {clip.start:.0f}s → {clip.end:.0f}s "
                                f"({clip.end - clip.start:.0f}s) | {clip.reason[:60]}"
                            )
                            added = True
                            break
                except Exception:
                    pass

        if not added:
            # Fallback: centro ± metade da duração alvo
            fb = ClipSegment(
                start=max(0.0, center - target_duration / 2),
                end=min(total, center + target_duration / 2),
                reason=reason,
            )
            clips.append(fb)
            print(f"  [Fase 2 fallback] {fb.start:.0f}s → {fb.end:.0f}s")

    return clips[:n]


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
    Renderiza um frame RGBA com o chunk de palavras em estilo Opus Clip:
    - Texto em MAIÚSCULAS, sempre visível dentro das bordas
    - Quebra automática de linha se o texto não cabe na largura
    - Palavra ativa: amarelo vibrante; demais: branco
    - Contorno preto em todas as letras
    """
    img  = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    upper_words = [word.upper() for word in state.words]
    available_w = w - 2 * CAPTION_PADDING

    # Mede largura de cada palavra (com espaço incluído)
    def _ww(word: str) -> int:
        bb = draw.textbbox((0, 0), word + " ", font=font)
        return bb[2] - bb[0]

    word_widths = [_ww(word) for word in upper_words]

    # Agrupa palavras em linhas que caibam dentro de available_w
    lines: list[list[tuple[str, int, int]]] = []  # (word, width, original_idx)
    current_line: list[tuple[str, int, int]] = []
    current_w = 0
    for i, (word, ww) in enumerate(zip(upper_words, word_widths)):
        if current_w + ww > available_w and current_line:
            lines.append(current_line)
            current_line = [(word, ww, i)]
            current_w = ww
        else:
            current_line.append((word, ww, i))
            current_w += ww
    if current_line:
        lines.append(current_line)

    # Altura de uma linha
    line_h = draw.textbbox((0, 0), "A", font=font)[3]

    total_block_h = len(lines) * line_h + (len(lines) - 1) * CAPTION_LINE_GAP
    y = int(h * 0.75) - total_block_h // 2

    for line in lines:
        line_w = sum(ww for _, ww, _ in line)
        x = max(CAPTION_PADDING, (w - line_w) // 2)

        for word, ww, orig_idx in line:
            color = (255, 220, 0, 255) if orig_idx == state.active_idx else (255, 255, 255, 255)
            draw.text(
                (x, y),
                word,
                font=font,
                fill=color,
                stroke_width=CAPTION_STROKE,
                stroke_fill=(0, 0, 0, 255),
            )
            x += ww

        y += line_h + CAPTION_LINE_GAP

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
    video_file: str = "",       # caminho de vídeo já baixado (pula o download)
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

    # 1. Download (ou usa vídeo local se --video foi passado)
    print("\n[1/4] Baixando vídeo...")
    if on_progress:
        try: await on_progress("⬇️ Baixando vídeo do YouTube...")
        except Exception: pass

    if video_file and os.path.exists(video_file):
        video_path = video_file
        size_mb = os.path.getsize(video_path) / 1_048_576
        print(f"  Usando vídeo local: {os.path.basename(video_path)} ({size_mb:.1f} MB)")
    else:
        try:
            video_path = download_youtube_video(url, work_dir)
        except Exception as e:
            print(f"❌ Erro no download: {e}")
            return []

    # 2. Transcrição (com cache)
    print("\n[2/4] Transcrevendo áudio (Whisper)...")
    if on_progress:
        try: await on_progress("🎙️ Transcrevendo com Whisper...")
        except Exception: pass

    cached = _load_transcript_cache(url)
    if cached:
        words, whisper_segs = cached
    else:
        try:
            words, whisper_segs = transcribe_video(video_path)
        except Exception as e:
            print(f"❌ Erro na transcrição: {e}")
            return []

        if not words or not whisper_segs:
            print("❌ Nenhuma palavra transcrita. Abortando.")
            return []

        # Salva cache JSON para re-runs futuros (mesma URL = skip Whisper)
        _save_transcript_cache(url, words, whisper_segs)

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

    print(f"\n  {'-'*50}")
    for i, seg in enumerate(clip_segments, 1):
        dur = seg.end - seg.start
        print(f"  Clipe {i}: {seg.start:.1f}s → {seg.end:.1f}s  ({dur:.0f}s)")
        print(f"           {seg.reason[:80]}")
    print(f"  {'-'*50}")

    # 4. Renderizar
    print(f"\n[4/4] Renderizando {len(clip_segments)} clipes...")
    if on_progress:
        try: await on_progress(f"🎬 Renderizando {len(clip_segments)} clipes...")
        except Exception: pass

    if hashtags is None:
        hashtags = ["Shorts", "Clips", "Podcast", "Brasil"]

    results: list[str] = []   # caminhos locais ou video_ids

    for i, seg in enumerate(clip_segments, 1):
        print(f"\n  -- Clipe {i}/{len(clip_segments)} --")
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

    # Remove o vídeo baixado (não remove se foi fornecido via --video)
    if not video_file:
        try:
            os.remove(video_path)
        except Exception:
            pass

    print(f"\n{'-'*50}")
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
    parser.add_argument("--video",   type=str, default="", help="Caminho de vídeo local (pula download)")
    args, _ = parser.parse_known_args()

    asyncio.run(run_clipper(
        url=args.url,
        n_clips=args.clips,
        tema=args.tema,
        upload=args.upload,          # padrão False — teste seguro
        privacy="private" if args.privado else "public",
        video_file=args.video,
    ))
