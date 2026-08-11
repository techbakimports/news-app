"""
PROTÓTIPO — "Resumo do Dia" completo no formato whiteboard.

Reaproveita o fluxo real do pipeline de Notícias (fetch_latest_news +
select_unique_news + extract_article_content, igual ao main.py Fase 1-3)
e o motor whiteboard (whiteboard_engine.py) — mas em vez de 1 notícia
isolada, processa todas as categorias de NEWS_SHORTS_CATEGORIES e
concatena tudo em 1 vídeo longo só, com um cartão de capítulo (cor da
categoria) entre cada notícia.

Status: PROTÓTIPO, não integrado ao pipeline de produção. Ver README.md
nesta pasta pra contexto de decisões e gaps conhecidos.

100% local — nenhum arquivo do projeto é modificado, nada é publicado.
Os Shorts de produção continuam gerados exatamente como hoje (esse
script não mexe nesse fluxo, só reaproveita a MESMA seleção de notícia).

Uso: python experiments/whiteboard/resumo_do_dia.py
"""
import sys, os, asyncio

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
sys.path.insert(0, _REPO_ROOT)
sys.path.insert(0, _HERE)
os.chdir(_REPO_ROOT)

import numpy as np
from bs4 import BeautifulSoup
from moviepy.editor import concatenate_videoclips

from fetcher import fetch_latest_news, select_unique_news, extract_article_content
from config import NEWS_SHORTS_CATEGORIES, CHANNEL_NAME
from video import CATEGORY_COLORS, DEFAULT_COLOR, _get_font

import whiteboard_engine as wb  # reusa todo o motor (ícones, cenas, TTS sincronizado)

OUTPUT_DIR = os.path.join(_HERE, "output")
os.makedirs(OUTPUT_DIR, exist_ok=True)


async def build_chapter(item: dict, chapter_seed: int):
    """Gera o capítulo completo (título + cenas) de UMA notícia. Retorna lista de clips."""
    title = wb.strip_source_suffix(item["title"])
    source = item.get("source", "")
    cat = item.get("category", "Notícias")
    color = CATEGORY_COLORS.get(cat, DEFAULT_COLOR)

    print(f"\n--- Capítulo: [{cat}] {title[:60]} ---")
    conteudo = extract_article_content(item.get("link", ""))
    if not conteudo or len(conteudo) < 200:
        resumo_html = item.get("summary", "").strip()
        conteudo = BeautifulSoup(resumo_html, "html.parser").get_text(" ", strip=True) if resumo_html else title
    print(f"  conteúdo: {len(conteudo)} chars")

    cenas = wb.gerar_roteiro_cenas(title, conteudo[:2500])
    if not cenas:
        print("  Groq falhou nesse item — pulando capítulo.")
        return [], None

    for c in cenas:
        imgs = []
        for name in c["icones"]:
            svg = wb.fetch_icon_svg(name)
            if not svg:
                continue
            png = wb.svg_to_rgba(svg)
            if png is None:
                continue
            imgs.append(wb.sketchify(png, seed=hash(name) & 0xFF))
        c["imgs"] = imgs
        c["texto_limpo"] = wb.clean_text(c["texto"])

    full_text = " ".join(c["texto_limpo"] for c in cenas)
    audio_bytes, words = await wb.tts_with_boundaries(full_text, "pt-BR-FranciscaNeural")
    if not audio_bytes:
        print("  TTS falhou nesse item — pulando capítulo.")
        return [], None

    from moviepy.editor import AudioFileClip
    apath = os.path.join(OUTPUT_DIR, f"dia_cap{chapter_seed}.mp3")
    with open(apath, "wb") as f:
        f.write(audio_bytes)
    full_audio = wb.trim_trailing_silence(AudioFileClip(apath))
    total = full_audio.duration

    cenas_para_words = [{"texto": c["texto_limpo"]} for c in cenas]
    starts = wb.scene_starts_from_words(cenas_para_words, words)
    for i in range(len(starts)):
        if starts[i] is None:
            starts[i] = starts[i - 1] + 2.0 if i > 0 else 0.0
    ends = starts[1:] + [total]
    durations = [max(0.6, e - s) for s, e in zip(starts, ends)]
    print(f"  áudio: {total:.1f}s | cenas: {' | '.join(f'{d:.1f}s' for d in durations)}")

    theme_icon = next((c["imgs"][0] for c in cenas if c.get("imgs")), None)

    clips = [wb.make_title_clip(title, cat, color, durations[0], theme_icon=theme_icon, source=source)]
    for i, (c, dur) in enumerate(zip(cenas[1:], durations[1:]), 2):
        clips.append(wb.make_scene_clip(c["texto"], c.get("imgs", []), cat, color, dur,
                                        scene_seed=chapter_seed * 100 + i,
                                        relacao=c.get("relacao", "fluxo")))

    WHITE = [255, 255, 255]
    MIN_FADE = 0.4
    faded = [
        c.fadein(0.15, initial_color=WHITE).fadeout(0.12, final_color=WHITE)
        if c.duration > MIN_FADE else c
        for c in clips
    ]
    chapter_video = concatenate_videoclips(faded, method="compose")
    chapter_video = chapter_video.set_audio(full_audio.subclip(0, min(total, chapter_video.duration)))
    return [chapter_video], full_audio


def make_opening_card(duration=3.0):
    """Cartão de abertura do 'Resumo do Dia' — usa o mesmo grid/estilo do whiteboard."""
    from PIL import Image, ImageDraw
    from moviepy.editor import VideoClip
    f_title = _get_font(64, bold=True)
    f_sub = _get_font(28)

    def make_frame(t):
        frame = Image.fromarray(wb.BG.copy()).convert("RGBA")
        draw = ImageDraw.Draw(frame)
        alpha = min(1.0, t / 0.5)
        txt = "Resumo do Dia"
        tb = draw.textbbox((0, 0), txt, font=f_title)
        tx, ty = (wb.W - tb[2]) // 2, wb.H // 2 - 60
        layer = Image.new("RGBA", frame.size, (0, 0, 0, 0))
        ImageDraw.Draw(layer).text((tx, ty), txt, font=f_title, fill=(25, 30, 45, int(255 * alpha)))
        frame = Image.alpha_composite(frame, layer)
        draw = ImageDraw.Draw(frame)

        from datetime import datetime
        sub = f"{CHANNEL_NAME} · {datetime.now().strftime('%d/%m/%Y')}"
        sb = draw.textbbox((0, 0), sub, font=f_sub)
        draw.text(((wb.W - sb[2]) // 2, ty + 90), sub, font=f_sub, fill=(120, 125, 140, int(220 * alpha)))
        return np.array(frame.convert("RGB"))

    return VideoClip(make_frame, duration=duration).set_fps(wb.FPS)


async def main():
    print(f"=== Buscando 1 notícia real por categoria: {NEWS_SHORTS_CATEGORIES} ===")
    itens = []
    for cat in NEWS_SHORTS_CATEGORIES:
        raw = fetch_latest_news(limit=5, categories=[cat])
        raw = select_unique_news(raw) if raw else []
        cat_items = [it for it in raw if it.get("category") == cat]
        if cat_items:
            itens.append(cat_items[0])
            print(f"  [{cat}] {cat_items[0]['title'][:70]}")
        else:
            print(f"  [{cat}] nenhuma notícia encontrada — pulando categoria")

    if not itens:
        print("Nenhuma notícia. Abortando.")
        return

    all_clips = [make_opening_card()]
    audio_refs = []
    for i, item in enumerate(itens, 1):
        chapter_clips, audio_ref = await build_chapter(item, chapter_seed=i)
        if chapter_clips:
            all_clips.extend(chapter_clips)
            audio_refs.append(audio_ref)

    if len(all_clips) <= 1:
        print("Nenhum capítulo gerado. Abortando.")
        return

    print(f"\n=== Concatenando {len(all_clips)} blocos ({len(itens)} categorias) ===")
    final = concatenate_videoclips(all_clips, method="compose")
    out = os.path.join(OUTPUT_DIR, "resumo_do_dia.mp4")
    final.write_videofile(out, fps=wb.FPS, codec="libx264", audio_codec="aac",
                          preset="fast", verbose=False, logger=None)
    dur_total = final.duration
    final.close()
    for a in audio_refs:
        a.close()

    print(f"\nVIDEO_LOCAL_PATH={out}")
    print(f"DURACAO_TOTAL={dur_total:.1f}s ({dur_total/60:.1f} min) CAPITULOS={len(itens)}")


if __name__ == "__main__":
    asyncio.run(main())
