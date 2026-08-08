"""
Gera YouTube Shorts verticais (1080×1920) a partir das principais notícias do dia.
Duração: até 3 min por Short — limite máximo do YouTube Shorts (a partir de 2024).
"""
from __future__ import annotations
import argparse
import asyncio
import io
import os
import re
import shutil
import sys
import numpy as np
import requests
from datetime import datetime
from typing import Callable
from PIL import Image, ImageDraw
from moviepy.editor import AudioFileClip, ImageClip
from dotenv import load_dotenv


def _ffmpeg_binary() -> str:
    """
    Retorna o caminho do ffmpeg disponível.
    Prioridade:
      1. ffmpeg do PATH do sistema (se houver)
      2. ffmpeg embutido pelo imageio-ffmpeg (sempre presente — moviepy depende)
    """
    sys_ffmpeg = shutil.which("ffmpeg")
    if sys_ffmpeg:
        return sys_ffmpeg
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"  # último recurso — vai falhar com mensagem clara

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
MAX_WORDS_SHORT = 400      # ~170s de fala — usa o limite máximo de 3 min do Shorts
MAX_SHORTS_PER_RUN = 3
SHORTS_OUTPUT_DIR = "./shorts_videos"


# ---------------------------------------------------------------------------
# Gerador de tags contextuais para descrição do YouTube
# ---------------------------------------------------------------------------

def _generate_tags(title: str, category: str, summary: str = "") -> list[str]:
    """
    Gera hashtags relevantes para a notícia usando LLM (Groq → Gemini → fallback estático).
    Retorna lista de strings SEM o '#' (ex: ['Trump', 'Brasil', 'Tarifas']).
    """
    prompt = (
        f"Gere de 8 a 12 hashtags relevantes para este vídeo de notícias no YouTube.\n"
        f"As tags devem ajudar na descoberta do vídeo (SEO). Inclua:\n"
        f"- Nomes de pessoas/organizações mencionadas\n"
        f"- Temas centrais da notícia\n"
        f"- Termos populares de busca relacionados\n"
        f"- A categoria da notícia\n"
        f"Retorne APENAS as hashtags separadas por vírgula, sem '#' e sem explicação.\n"
        f"Exemplo: Trump, Brasil, Economia, Tarifas, Política Internacional\n\n"
        f"Categoria: {category}\n"
        f"Título: {title}\n"
        f"Resumo: {summary[:500]}\n"
    )

    fallback = [category, "Notícias", "Brasil", "Shorts", "NewsApp"]

    groq_key = os.getenv("GROQ_API_KEY")
    if groq_key and groq_key != "cole_sua_chave_aqui":
        try:
            from groq import Groq
            client = Groq(api_key=groq_key)
            resp = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.5,
            )
            raw = resp.choices[0].message.content.strip()
            tags = [t.strip().lstrip("#") for t in raw.split(",") if t.strip()]
            if tags:
                return tags[:15]
        except Exception as e:
            print(f"  Tags via Groq falhou: {e}")

    gemini_key = os.getenv("GEMINI_API_KEY", "")
    if gemini_key:
        try:
            from google import genai
            client = genai.Client(api_key=gemini_key)
            resp = client.models.generate_content(model="gemini-2.0-flash", contents=prompt)
            raw = resp.text.strip()
            tags = [t.strip().lstrip("#") for t in raw.split(",") if t.strip()]
            if tags:
                return tags[:15]
        except Exception as e:
            print(f"  Tags via Gemini falhou: {e}")

    return fallback


# ---------------------------------------------------------------------------
# Summarizer dedicado para Shorts (prompt mais conciso)
# ---------------------------------------------------------------------------

_VALID_CATEGORIES = {"Política", "Esporte", "Entretenimento", "Mercado Financeiro", "Tecnologia", "Policial", "Celebridades"}

def _summarize_for_short(title: str, category: str, content: str) -> tuple[str, str] | None:
    """
    Gera texto de até 400 palavras pra Shorts (até 3 min de fala).
    Cadeia: Groq (primário) → Gemini (fallback) → None.

    Retorna (summary, categoria_corrigida) ou None se nenhum LLM funcionar.
    """
    cats_str = ", ".join(sorted(_VALID_CATEGORIES))
    prompt = (
        f"Você é um apresentador de notícias no estilo YouTube Shorts — direto, impactante e sem rodeios.\n"
        f"Faça DUAS coisas:\n\n"
        f"1. CATEGORIA CORRETA: analise o conteúdo e escolha a categoria que MELHOR descreve esta notícia dentre: {cats_str}.\n"
        f"   A categoria sugerida foi '{category}', mas pode estar ERRADA. Corrija se necessário.\n"
        f"   Responda a categoria na PRIMEIRA LINHA, no formato: CATEGORIA: <nome>\n\n"
        f"2. RESUMO: escreva UM parágrafo de até 400 palavras cobrindo os principais fatos.\n"
        f"   NÃO use markdown, asteriscos ou símbolos. Apenas texto simples em português.\n"
        f"   Comece com a frase mais impactante — prenda a atenção imediatamente.\n"
        f"   Encerre com uma síntese ou desdobramento esperado.\n\n"
        f"Título: {title}\n"
        f"Conteúdo: {content[:2500]}\n"
    )

    def _parse_response(text: str) -> tuple[str, str]:
        lines = text.strip().splitlines()
        corrected_cat = category
        summary_start = 0
        if lines and lines[0].upper().startswith("CATEGORIA:"):
            raw_cat = lines[0].split(":", 1)[1].strip()
            for valid in _VALID_CATEGORIES:
                if raw_cat.lower() == valid.lower():
                    corrected_cat = valid
                    break
            summary_start = 1
            while summary_start < len(lines) and not lines[summary_start].strip():
                summary_start += 1
        summary_text = " ".join(lines[summary_start:])
        words = clean_text(summary_text).split()
        return " ".join(words[:MAX_WORDS_SHORT]), corrected_cat

    # 1) Groq (primário)
    groq_key = os.getenv("GROQ_API_KEY")
    if groq_key and groq_key != "cole_sua_chave_aqui":
        try:
            from groq import Groq
            client = Groq(api_key=groq_key)
            resp = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
            )
            return _parse_response(resp.choices[0].message.content)
        except Exception as e:
            print(f"  Groq Shorts falhou: {e}. Tentando Gemini...")

    # 2) Gemini (fallback)
    gemini_key = os.getenv("GEMINI_API_KEY", "")
    if gemini_key:
        try:
            from google import genai
            client = genai.Client(api_key=gemini_key)
            resp = client.models.generate_content(model="gemini-2.0-flash", contents=prompt)
            return _parse_response(resp.text)
        except Exception as e:
            print(f"  Gemini Shorts também falhou: {e}")

    print(f"  ❌ Nenhum LLM gerou resumo. Short será PULADO (não vamos ler só o título).")
    return None


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
    """
    Escurecimento concentrado nas zonas de texto (topo/base) em vez de uniforme
    na imagem inteira — a foto de fundo fica bem mais visível, o que aumenta o
    impacto visual da capa/thumbnail (o Short inteiro é um frame estático, então
    esse frame É a miniatura que aparece no feed do YouTube).
    """
    arr = np.array(image).astype(float) * 0.55  # antes 0.35 — foto mais visível
    base = Image.fromarray(arr.astype(np.uint8)).convert("RGBA")

    grad = Image.new("RGBA", (SHORTS_W, SHORTS_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(grad)
    # Topo — faixa curta, só o suficiente pro badge/canal
    for y in range(int(SHORTS_H * 0.20)):
        alpha = int(130 * (1 - y / (SHORTS_H * 0.20)))
        draw.line([(0, y), (SHORTS_W, y)], fill=(0, 0, 0, alpha))
    # Zona do título — leve, mais forte nas bordas da faixa do que no centro
    mid_start, mid_end = int(SHORTS_H * 0.28), int(SHORTS_H * 0.56)
    for y in range(mid_start, mid_end):
        t = (y - mid_start) / (mid_end - mid_start)
        alpha = int(70 + 60 * abs(0.5 - t) * 2)
        draw.line([(0, y), (SHORTS_W, y)], fill=(0, 0, 0, alpha))
    # Base — zona do gancho/rodapé, mais forte, mas área menor que antes (32% vs 50%)
    start = int(SHORTS_H * 0.68)
    for y in range(start, SHORTS_H):
        alpha = int(200 * (y - start) / (SHORTS_H - start))
        draw.line([(0, y), (SHORTS_W, y)], fill=(0, 0, 0, alpha))

    return np.array(Image.alpha_composite(base, grad).convert("RGB"))


def _light_overlay(image: Image.Image) -> np.ndarray:
    """
    Escurecimento leve — só o suficiente pra legibilidade do texto nas bordas,
    preserva o brilho do produto no centro. Usado quando a imagem é um produto
    (image_url), onde _dark_overlay deixaria a foto escura demais pra vender bem.
    """
    base = image.convert("RGBA")

    grad = Image.new("RGBA", (SHORTS_W, SHORTS_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(grad)
    # Gradiente superior mais curto e suave (top 22%)
    for y in range(int(SHORTS_H * 0.22)):
        alpha = int(140 * (1 - y / (SHORTS_H * 0.22)))
        draw.line([(0, y), (SHORTS_W, y)], fill=(0, 0, 0, alpha))
    # Gradiente inferior (bottom 40%) — onde ficam preço e rodapé
    start = int(SHORTS_H * 0.60)
    for y in range(start, SHORTS_H):
        alpha = int(190 * (y - start) / (SHORTS_H - start))
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


def _wrap_hook_lines(draw, text: str, font, max_width: int, max_lines: int = 2) -> list[str]:
    """
    Quebra o gancho em até max_lines linhas. Se sobrar texto além disso, trunca
    a última linha com "…" em vez de simplesmente cortar a frase no meio sem
    aviso (o que podia esconder justamente o dado mais forte, ex: a porcentagem).
    """
    all_lines = _wrap_text(draw, text, font, max_width)
    if len(all_lines) <= max_lines:
        return all_lines

    lines = all_lines[:max_lines - 1]
    used_words = sum((l.split() for l in lines), [])
    remaining = text.split()[len(used_words):]
    last = " ".join(remaining)
    while last and draw.textbbox((0, 0), last + "…", font=font)[2] > max_width:
        last = last.rsplit(" ", 1)[0] if " " in last else last[:-1]
    lines.append((last + "…") if last else "…")
    return lines


_HOOK_SENTENCE_END = re.compile(r'[.!?](?:\s|$)')


def _extract_hook(text: str, max_chars: int = 120) -> str:
    """
    Extrai a frase de abertura do resumo pra usar como "gancho" na tela (1-2 linhas
    em destaque), sem chamar a IA de novo — reaproveita o texto já gerado pelo
    Groq/Gemini. Mostrar o resumo inteiro (4-6 linhas) deixava a tela um textão;
    o resto da informação continua na narração falada, só não fica na tela.
    """
    if not text:
        return ""
    text = text.strip()
    match = _HOOK_SENTENCE_END.search(text)
    hook = text[:match.end()].strip() if match else text
    # Frase de abertura muito curta (ex: só um vocativo) — inclui a próxima também
    if len(hook) < 40:
        rest = text[len(hook):].strip()
        match2 = _HOOK_SENTENCE_END.search(rest)
        if match2:
            hook = (hook + " " + rest[:match2.end()]).strip()
    if len(hook) > max_chars:
        hook = hook[:max_chars].rsplit(" ", 1)[0].rstrip(",;") + "…"
    return hook


_HIGHLIGHT_TOKEN = re.compile(
    r'\b\d+(?:[.,]\d+)?%'                 # 60%, 2,5%
    r'|\b\d+(?:[.,]\d+)?[xX](?!\w)'       # 10x, 2X
    r'|R\$\s?\d+(?:[.,]\d+)?'             # R$50, R$ 1.200
    r'|\b\d+(?:[.,]\d+)?\b'               # número solto: 60, 2026, 3
)


def _pick_highlight_token(title: str) -> str | None:
    """
    Encontra o primeiro trecho do título com número/porcentagem (ex: "10x", "60%",
    "R$50") pra grifar com a cor da categoria — números chamam atenção na miniatura.
    Retorna None se não achar nada (o título é exibido normalmente, sem grifo).

    O \\b antes do dígito exige que ele seja um número "solto" (isolado por
    espaço/pontuação) — evita grifar por engano códigos como "G1" ou "R7"
    (sufixo de fonte que vem colado no título cru do RSS, ex: "Manchete - G1"),
    onde o dígito está grudado numa letra e não é uma estatística de verdade.
    """
    match = _HIGHLIGHT_TOKEN.search(title)
    return match.group(0) if match else None


def _render_short_frame(
    bg_arr: np.ndarray,
    title: str,
    summary: str,
    category: str,
    source: str,
    price_text: str | None = None,
    cjk_font: bool = False,
    channel_name: str | None = None,
    category_label: str | None = None,
) -> np.ndarray:
    """Renderiza frame vertical com título, resumo e elementos de UI.

    cjk_font: use fonte com suporte a japonês/chinês/coreano (necessário pro
              pipeline internacional em japonês — senão os glifos viram
              caixinhas vazias mesmo com a fonte CJK instalada no sistema).

    channel_name: nome exibido no canto superior direito. Se None, usa o
                  CHANNEL_NAME global (canal principal). Pipelines de outros
                  países/canais devem passar o nome do canal correspondente.

    price_text: se fornecido, desenha um badge de preço/desconto logo após o
    separador colorido, antes do bloco de resumo (ex: vídeos de produto/afiliado).
    """
    color = CATEGORY_COLORS.get(category, DEFAULT_COLOR)
    r, g, b = color

    base = Image.fromarray(bg_arr).convert("RGBA")
    overlay = Image.new("RGBA", (SHORTS_W, SHORTS_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    padding = 60
    max_w = SHORTS_W - padding * 2

    # --- Badge de categoria (topo) ---
    f_badge = _get_font(46, bold=True, cjk=cjk_font)
    badge_text = f" {(category_label or category).upper()} "
    bbox = draw.textbbox((0, 0), badge_text, font=f_badge)
    bw, bh = bbox[2] + 36, bbox[3] + 22
    badge_y = 80
    draw.rounded_rectangle(
        [(padding, badge_y), (padding + bw, badge_y + bh)],
        radius=16, fill=(r, g, b, 245),
    )
    draw.text((padding + 18, badge_y + 11), badge_text.strip(), font=f_badge, fill=(255, 255, 255, 255))

    # --- Nome do canal (topo direito) ---
    ch_name = channel_name or CHANNEL_NAME
    f_channel = _get_font(36, cjk=cjk_font)
    ch_bbox = draw.textbbox((0, 0), ch_name, font=f_channel)
    cx = SHORTS_W - ch_bbox[2] - padding
    draw.text((cx, badge_y + 14), ch_name, font=f_channel, fill=(230, 230, 230, 170))

    # --- Título (zona central, com grifo na cor da categoria em número/% do
    #     título, quando houver — chama mais atenção na miniatura) ---
    f_title = _get_font(78, bold=True, cjk=cjk_font)
    title_lines = _wrap_text(draw, title, f_title, max_w)[:4]
    line_h = 92
    title_block_h = len(title_lines) * line_h
    title_y = int(SHORTS_H * 0.40) - title_block_h // 2

    # CJK (japonês/chinês/coreano) não usa espaço entre palavras — o regex de
    # destaque pode "grudar" num trecho gigante do título. Desliga o grifo
    # nesse caso em vez de arriscar destacar quase a frase inteira.
    highlight = None if cjk_font else _pick_highlight_token(title)
    for i, line in enumerate(title_lines):
        y = title_y + i * line_h
        if highlight and highlight in line:
            pre_w = draw.textbbox((0, 0), line.split(highlight, 1)[0], font=f_title)[2]
            hl_w = draw.textbbox((0, 0), highlight, font=f_title)[2]
            draw.rectangle(
                [(padding + pre_w - 6, y + 12), (padding + pre_w + hl_w + 10, y + line_h - 10)],
                fill=(r, g, b, 210),
            )
            highlight = None  # grifa só a primeira ocorrência
        # Sombra de texto
        draw.text((padding + 3, y + 3), line, font=f_title, fill=(0, 0, 0, 190))
        draw.text((padding, y), line, font=f_title, fill=(255, 255, 255, 255))

    # --- Separador colorido ---
    sep_y = title_y + title_block_h + 26
    draw.rectangle([(padding, sep_y), (padding + 130, sep_y + 7)], fill=(r, g, b, 255))
    content_y = sep_y + 34

    # --- Badge de preço/desconto (opcional — vídeos de produto/afiliado) ---
    if price_text:
        f_price = _get_font(52, bold=True, cjk=cjk_font)
        pbbox = draw.textbbox((0, 0), price_text, font=f_price)
        pw, ph = pbbox[2] + 40, pbbox[3] + 24
        draw.rounded_rectangle(
            [(padding, content_y), (padding + pw, content_y + ph)],
            radius=14, fill=(20, 160, 80, 235),
        )
        draw.text((padding + 20, content_y + 12), price_text, font=f_price, fill=(255, 255, 255, 255))
        content_y += ph + 24

    # --- Gancho (frase de abertura do resumo, 1-2 linhas em destaque) ---
    # Antes mostrava o resumo inteiro (até 6 linhas) — virava um textão que
    # competia com o título. O resto da informação continua na narração falada.
    hook = _extract_hook(summary)
    f_summary = _get_font(50, bold=True, cjk=cjk_font)
    summary_lines = _wrap_hook_lines(draw, hook, f_summary, max_w)
    sum_y = content_y
    for i, line in enumerate(summary_lines):
        draw.text((padding + 2, sum_y + i * 62 + 2), line, font=f_summary, fill=(0, 0, 0, 180))
        draw.text((padding, sum_y + i * 62), line, font=f_summary, fill=(255, 255, 255, 235))

    # --- Fonte (rodapé) ---
    f_source = _get_font(34, cjk=cjk_font)
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

async def generate_short_from_text(
    title: str,
    narration: str,
    category: str = "Notícias",
    source: str = "",
    upload: bool = True,
    privacy: str = "public",
    hashtags: list[str] | None = None,
    playlist_key: str = "noticias",
    instagram_enabled: bool = True,
    youtube_enabled: bool = True,
    link: str | None = None,
    voice: str | None = None,
    display_text: str | None = None,
    image_url: str | None = None,
    price_text: str | None = None,
    link_label: str | None = None,
    extra_description: str | None = None,
    keep_local: bool = False,
    on_local_path: Callable[[str], None] | None = None,
    language: str = "pt",
    token_file: str | None = None,
    source_label: str | None = None,
    cjk_font: bool = False,
    channel_name: str | None = None,
    category_label: str | None = None,
) -> str | None:
    """
    Gera um Short vertical 1080×1920 a partir de TEXTO PRONTO (sem chamar Gemini).

    Retorna video_id do YouTube ou caminho local quando upload=False.
    Faz TTS + busca imagem Pexels + renderiza frame + upload YouTube/Instagram.

    Args:
        title: título da notícia (exibido em destaque)
        narration: texto que será falado pelo TTS (deve estar pronto, máx ~400 palavras)
        category: categoria pra cor/badge (usa CATEGORY_COLORS)
        source: fonte exibida no rodapé
        hashtags: lista de hashtags YouTube (default = pacote genérico)
        playlist_key: chave da playlist no playlists.py ("noticias", "tech", etc)
        instagram_enabled: se False, NÃO posta no Instagram mesmo com config ativa
        voice: voz Edge TTS explícita. Se None, resolve automaticamente por categoria
               via CATEGORY_VOICES (config.py). Ex: "pt-BR-ThalitaNeural"
        display_text: texto exibido sobre a imagem de fundo. Se None, usa o próprio
                       `narration`. Use para excluir intro/CTA do texto na tela quando
                       `narration` tiver esses trechos só para a fala.
        image_url: URL de imagem direta (ex: foto de produto). Se fornecida, usa essa
                   imagem em vez de buscar no Pexels. Cai no fluxo Pexels se o download falhar.
        price_text: texto de preço/desconto exibido como badge na tela (ex: "R$ 49,90 → R$ 29,90 (-40%)").
                    Se None, nenhum badge de preço é desenhado.
        link_label: rótulo do bloco de link na descrição do YouTube. Se None, usa
                    "📎 Leia a notícia completa:" (padrão de notícia). Pipelines de
                    afiliado/produto devem passar algo como "🛒 Compre aqui (link de afiliado):".
        extra_description: texto extra inserido no TOPO da descrição do YouTube, antes
                            do link. Use para avisos obrigatórios (ex: divulgação de publicidade
                            em conteúdo de afiliados, conforme guia CONAR).
        keep_local: se True, NÃO apaga o arquivo de vídeo local após o upload. Use quando
                    o mesmo arquivo renderizado será reaproveitado por outra plataforma
                    (ex: publicar no TikTok logo depois, sem renderizar o vídeo de novo).
        on_local_path: callback opcional chamado com o caminho do arquivo local assim que
                       o vídeo termina de ser montado (antes do upload) — permite capturar
                       o caminho pra reutilizar mesmo quando upload=True apaga o arquivo
                       depois (a menos que keep_local=True).
        language: código de idioma do vídeo no YouTube (defaultLanguage/defaultAudioLanguage).
                  "pt" por padrão; pipelines de outros países passam "ja", "fr", "de", "it", etc.
        token_file: caminho de um token.json alternativo — usado por pipelines com canal
                    próprio (ex: credentials/token_japao.json). None usa o canal principal.
        source_label: rótulo antes da fonte na descrição do YouTube. Se None, usa "Fonte:".
                      Pipelines em outro idioma devem passar a tradução (ex: "Source:").
        cjk_font: True usa fonte com suporte a japonês/chinês/coreano na renderização
                  do texto na tela. Necessário pro pipeline internacional em japonês.
        channel_name: nome do canal exibido na tela e na descrição do YouTube. Se
                      None, usa CHANNEL_NAME (config.py, canal principal). Pipelines
                      de outros países/canais devem passar o nome daquele canal.
        category_label: texto exibido no badge da tela e usado nas tags do YouTube.
                        Se None, usa o próprio `category`. Use pra mostrar a categoria
                        traduzida (ex: category="Política" pra cor/voz corretas,
                        category_label="政治" pro que aparece na tela/tags).

    Retorna o video_id do YouTube ou None se falhar.
    Retorna None ANTES de gerar nada se narration estiver vazia.
    """
    from audio import voice_for_category

    # Bail-out: sem narração real, não geramos nada (não lemos só o título)
    if not narration or not narration.strip():
        print(f"  ⚠️  Narração vazia. Pulando Short '{title[:50]}'.")
        return None

    os.makedirs(SHORTS_OUTPUT_DIR, exist_ok=True)
    os.makedirs(AUDIO_OUTPUT_DIR, exist_ok=True)

    # Resolve voz: parâmetro explícito > mapeamento por categoria > fallback global
    selected_voice = voice or voice_for_category(category)

    # Limita narração (falada) ao máximo de palavras pro Short (~3 min)
    words = clean_text(narration).split()
    narration_full = " ".join(words[:MAX_WORDS_SHORT])

    # Texto exibido sobre a imagem de fundo — usa display_text (sem intro/CTA)
    # quando fornecido, senão cai no próprio narration
    summary_words = clean_text(display_text if display_text else narration).split()
    summary = " ".join(summary_words[:MAX_WORDS_SHORT])

    print(f"\n  Short: {title[:60]}...")
    print(f"  Voz: {selected_voice}")

    # 1. Áudio TTS
    print("  [1/3] Gerando áudio TTS...")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    audio_filename = f"short_{ts}.mp3"
    audio_path = os.path.join(AUDIO_OUTPUT_DIR, audio_filename)
    data = await _stream_to_bytes(narration_full, voice=selected_voice)
    if not data:
        print("  TTS falhou — pulando.")
        return None
    with open(audio_path, "wb") as f:
        f.write(data)

    audio_clip = AudioFileClip(audio_path)
    duration = min(audio_clip.duration, 178.0)  # margem de 2s do limite (180s)

    # 2. Imagem — direta (image_url) ou busca Pexels
    pil_img = None
    is_product_image = False
    if image_url:
        print("  [2/3] Baixando imagem do produto...")
        try:
            resp = requests.get(image_url, timeout=15)
            resp.raise_for_status()
            pil_img = Image.open(io.BytesIO(resp.content)).convert("RGB")
            is_product_image = True
        except Exception as e:
            print(f"  Falha ao baixar image_url ({e}) — caindo pro Pexels.")
            pil_img = None

    if pil_img is None:
        print("  [2/3] Buscando imagem Pexels...")
        query = _build_pexels_query(title, category)
        pil_img, _ = _search_pexels(query, orientation="portrait")
        if pil_img is None:
            pil_img, _ = _search_pexels(query, orientation="landscape")

    if pil_img:
        portrait = _crop_portrait(pil_img)
        # Foto de produto usa overlay leve (preserva brilho) — fundo editorial
        # do Pexels usa o escurecimento forte de sempre.
        bg_arr = _light_overlay(portrait) if is_product_image else _dark_overlay(portrait)
    else:
        color = CATEGORY_COLORS.get(category, DEFAULT_COLOR)
        bg_solid = Image.new("RGB", (SHORTS_W, SHORTS_H), tuple(int(c * 0.25) for c in color))
        bg_arr = np.array(bg_solid)

    # 3. Render + monta vídeo
    print("  [3/3] Montando vídeo vertical...")
    frame = _render_short_frame(bg_arr, title, summary, category, source, price_text=price_text,
                                 cjk_font=cjk_font, channel_name=channel_name, category_label=category_label)

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
    try:
        os.remove(audio_path)
    except Exception:
        pass

    print(f"  Vídeo salvo: {output_path} ({duration:.1f}s)")

    if on_local_path:
        try:
            on_local_path(output_path)
        except Exception:
            pass

    if not upload:
        return output_path

    # Upload — plataformas independentes
    from uploader import upload_video as yt_upload
    from playlists import add_to_playlist

    if hashtags is None:
        hashtags = ["Shorts", "Notícias", "Brasil"]

    cat_display = category_label or category
    context_tags = _generate_tags(title, cat_display, summary)
    all_tags = list(dict.fromkeys(hashtags + context_tags))
    hash_line = " ".join(f"#{t}" for t in all_tags)

    yt_title = f"{title[:80]} #Shorts"
    link_bloco = ""
    if link:
        label = link_label or "📎 Leia a notícia completa:"
        link_bloco = f"{label}\n{link}\n\n"
    extra_bloco = f"{extra_description}\n\n" if extra_description else ""
    src_label = source_label or "Fonte:"
    ch_name = channel_name or CHANNEL_NAME
    yt_desc = (
        f"{extra_bloco}"
        f"{link_bloco}"
        f"{src_label} {source}\n"
        f"📰 {ch_name}\n\n"
        f"{hash_line}"
    )
    yt_tags = [t.lower() for t in all_tags] + [cat_display.lower()]

    video_id = None

    # YouTube
    if youtube_enabled:
        try:
            video_id = yt_upload(output_path, yt_title, yt_desc, yt_tags, privacy=privacy,
                                  language=language, token_file=token_file)
            print(f"    YouTube: https://youtu.be/{video_id}")
            try:
                add_to_playlist(video_id, playlist_key, token_file=token_file)
            except Exception as e:
                print(f"    add_to_playlist falhou: {e}")
        except Exception as e:
            print(f"    Erro YouTube: {e}")
    else:
        print(f"    YouTube: PULADO (youtube_enabled=False)")

    # Instagram
    if instagram_enabled:
        try:
            from config import INSTAGRAM_UPLOAD
            if INSTAGRAM_UPLOAD:
                from instagram_uploader import upload_reel, INSTAGRAM_ENABLED
                if INSTAGRAM_ENABLED:
                    ig_caption = (
                        f"{title}\n\nFonte: {source}\n\n"
                        f"{hash_line.lower()}"
                    )
                    upload_reel(output_path, ig_caption)
                    print(f"    Instagram Reel: OK")
        except Exception as e:
            print(f"    Instagram Reel falhou: {e}")

    # Cleanup — pulado quando keep_local=True (arquivo será reaproveitado por outra plataforma)
    if not keep_local:
        try:
            os.remove(output_path)
        except Exception:
            pass

    return video_id


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

    # 1. Resumo curto + validação de categoria
    print("  [1/4] Resumindo para Shorts...")
    result = _summarize_for_short(title, category, content)
    if result is None:
        print(f"  ⚠️  Sem resumo de LLM. PULANDO este Short (não geramos vídeo só com título).")
        return None
    summary, corrected_category = result
    if corrected_category != category:
        print(f"  📌 Categoria corrigida: {category} → {corrected_category}")
        category = corrected_category
        item["category"] = corrected_category
    narration = f"{title}. {summary}. Gostou da notícia? Curta e compartilhe!"

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
        context_tags = _generate_tags(title, category, summary)
        all_tags = list(dict.fromkeys(["Shorts", "Notícias", "Brasil"] + context_tags))
        hash_line = " ".join(f"#{t}" for t in all_tags)
        yt_desc = (
            f"Fonte: {source}\n"
            f"📰 {CHANNEL_NAME}\n\n"
            f"{hash_line}"
        )
        yt_tags = [t.lower() for t in all_tags]
        try:
            video_id = upload_video(output_path, yt_title, yt_desc, yt_tags, privacy=privacy)
            print(f"  YouTube Shorts: https://youtu.be/{video_id}")
            add_to_playlist(video_id, "noticias")

            # Instagram Reel — reaproveita o mesmo vídeo vertical
            from config import INSTAGRAM_UPLOAD
            if INSTAGRAM_UPLOAD:
                from instagram_uploader import upload_reel, INSTAGRAM_ENABLED
                if INSTAGRAM_ENABLED:
                    ig_caption = (
                        f"{title}\n\n"
                        f"Fonte: {source}\n\n"
                        f"{hash_line.lower()}"
                    )
                    upload_reel(output_path, ig_caption)

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
    duration_s: float = 180.0,
) -> str | None:
    """
    Corta os primeiros `duration_s` segundos do vídeo landscape e converte
    para 1080×1920 portrait com blur background (estilo Shorts/Reels).
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
        _ffmpeg_binary(), "-y",
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
    except FileNotFoundError as e:
        print(f"  ffmpeg não encontrado: {e}")
        print(f"  Tentado: {_ffmpeg_binary()}")
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
    items_summary = " ".join(it.get("title", "") for it in items[:3])
    context_tags = _generate_tags(title, category, items_summary)
    all_tags = list(dict.fromkeys(["Shorts", "Notícias", "Brasil"] + context_tags))
    hash_line = " ".join(f"#{t}" for t in all_tags)

    yt_title = f"{title[:80]} #Shorts"
    yt_desc = (
        f"📰 {CHANNEL_NAME} — {datetime.now().strftime('%d/%m/%Y')}\n\n"
        f"{hash_line}"
    )
    yt_tags = [t.lower() for t in all_tags]

    try:
        video_id = upload_video(output_path, yt_title, yt_desc, yt_tags, privacy=privacy)
        print(f"  YouTube Short: https://youtu.be/{video_id}")
        add_to_playlist(video_id, "noticias")

        # Instagram Reel — corte vertical do vídeo de notícias
        from config import INSTAGRAM_UPLOAD
        if INSTAGRAM_UPLOAD:
            from instagram_uploader import upload_reel, INSTAGRAM_ENABLED
            if INSTAGRAM_ENABLED:
                ig_caption = (
                    f"{title}\n\n{hash_line.lower()}"
                )
                upload_reel(output_path, ig_caption)

        try:
            os.remove(output_path)
        except Exception:
            pass
        return video_id
    except Exception as e:
        print(f"  Erro no upload do Short: {e}")
        return None


# ---------------------------------------------------------------------------
# Shorts por categoria — corta segmentos do vídeo longo
# ---------------------------------------------------------------------------

def _cut_vertical_segment(
    src_video: str,
    start_s: float,
    duration_s: float,
    output_path: str,
) -> bool:
    """Corta um segmento [start, start+duration] do video e converte pra vertical com blur."""
    import subprocess

    filter_graph = (
        f"[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
        f"crop=1080:1920,boxblur=25:5[bg];"
        f"[0:v]scale=1080:-2[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2"
    )
    cmd = [
        _ffmpeg_binary(), "-y",
        "-ss", f"{start_s:.3f}",
        "-i", src_video,
        "-t", f"{duration_s:.3f}",
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
            return False
        return True
    except FileNotFoundError as e:
        print(f"  ffmpeg não encontrado: {e}")
        print(f"  Tentado: {_ffmpeg_binary()}")
        return False
    except Exception as e:
        print(f"  Erro ffmpeg: {e}")
        return False


def generate_shorts_per_category(
    video_path: str,
    items: list[dict],
    news_durations: list[float],
    intro_duration: float,
    excluded_categories: list[str] | None = None,
    upload: bool = True,
    privacy: str = "public",
    max_short_duration: float = 180.0,
) -> list[str]:
    """
    Gera um Short por categoria a partir do vídeo longo, pulando categorias excluídas.
    Pega a PRIMEIRA notícia de cada categoria, calcula timestamps no vídeo e corta.

    Args:
        video_path: caminho do vídeo MP4 longo (1280×720)
        items: items_to_process (com category, title, ai_summary, link)
        news_durations: lista de durações de cada notícia (segundos)
        intro_duration: duração da intro (segundos)
        excluded_categories: lista de categorias para pular (case-insensitive)
        max_short_duration: limite por Short — corta no meio se notícia for maior

    Retorna lista de video_ids dos Shorts enviados.
    """
    if not os.path.exists(video_path):
        print(f"[Shorts/categoria] Vídeo não encontrado: {video_path}")
        return []

    if len(news_durations) != len(items):
        print(f"[Shorts/categoria] Mismatch: {len(news_durations)} durações vs {len(items)} itens. Abortando.")
        return []

    excluded = {c.strip().lower() for c in (excluded_categories or [])}

    # Pega a primeira ocorrência de cada categoria (não-excluída)
    seen_categories: set[str] = set()
    selected: list[tuple[int, dict]] = []  # (índice no items, item)
    for i, item in enumerate(items):
        cat = (item.get("category") or "").strip()
        cat_lower = cat.lower()
        if not cat or cat_lower in excluded or cat_lower in seen_categories:
            continue
        seen_categories.add(cat_lower)
        selected.append((i, item))

    if not selected:
        print("[Shorts/categoria] Nenhuma notícia selecionável encontrada.")
        return []

    print(f"\n[Shorts/categoria] Gerando {len(selected)} Short(s): "
          f"{', '.join(it['category'] for _, it in selected)}")
    if excluded:
        print(f"  (excluídas: {', '.join(sorted(excluded))})")

    os.makedirs(SHORTS_OUTPUT_DIR, exist_ok=True)
    ts_base = datetime.now().strftime("%Y%m%d_%H%M%S")
    date_str = datetime.now().strftime("%d/%m/%Y")

    # Imports lazy pra evitar custo se nenhum Short for gerado
    from uploader import upload_video
    from playlists import add_to_playlist

    uploaded_ids: list[str] = []
    # Rastreio detalhado por plataforma pra resumo final
    stats = {
        "youtube_ok": 0, "youtube_fail": 0,
        "instagram_ok": 0, "instagram_fail": 0,
        "ffmpeg_fail": 0,
    }
    falhas_detalhadas: list[str] = []  # ["cat: motivo", ...]

    for n, (idx, item) in enumerate(selected, 1):
        category = item["category"]
        title = item.get("title", "Notícia")

        # Calcula offset: intro + soma das durações das notícias anteriores
        start_s = intro_duration + sum(news_durations[:idx])
        dur_s = min(news_durations[idx], max_short_duration)

        output_path = os.path.join(
            SHORTS_OUTPUT_DIR,
            f"Short_{ts_base}_{n:02d}_{category[:15].replace(' ', '_')}.mp4",
        )

        print(f"\n  [{n}/{len(selected)}] {category}: cortando {dur_s:.1f}s @ {start_s:.1f}s")
        if not _cut_vertical_segment(video_path, start_s, dur_s, output_path):
            stats["ffmpeg_fail"] += 1
            falhas_detalhadas.append(f"{category}: ffmpeg falhou ao cortar segmento")
            continue

        size_mb = os.path.getsize(output_path) / 1024 / 1024
        print(f"    Salvo: {output_path} ({size_mb:.1f} MB)")

        if not upload:
            continue

        # Gera tags contextuais para este Short
        item_summary = item.get("ai_summary", "") or item.get("_content", "") or title
        context_tags = _generate_tags(title, category, item_summary)
        all_tags = list(dict.fromkeys(["Shorts", "Notícias", "Brasil", category.replace(" ", "")] + context_tags))
        hash_line = " ".join(f"#{t}" for t in all_tags)

        # Upload nas 3 plataformas
        yt_title = f"{category}: {title[:70]} #Shorts"
        yt_desc = (
            f"📰 {CHANNEL_NAME} — {date_str}\n\n"
            f"{hash_line}"
        )
        yt_tags = [t.lower() for t in all_tags]

        # YouTube — falha aqui NÃO cancela Instagram
        try:
            video_id = upload_video(output_path, yt_title, yt_desc, yt_tags, privacy=privacy)
            uploaded_ids.append(video_id)
            stats["youtube_ok"] += 1
            print(f"    YouTube: ✅ https://youtu.be/{video_id}")
            try:
                add_to_playlist(video_id, "noticias")
            except Exception as e:
                print(f"    ⚠️ add_to_playlist falhou: {e}")
        except Exception as e:
            stats["youtube_fail"] += 1
            falhas_detalhadas.append(f"{category}/YouTube: {type(e).__name__}: {str(e)[:120]}")
            print(f"    ❌ YouTube falhou: {type(e).__name__}: {e}")

        # Instagram Reel — independente
        try:
            from config import INSTAGRAM_UPLOAD
            if INSTAGRAM_UPLOAD:
                from instagram_uploader import upload_reel, INSTAGRAM_ENABLED
                if INSTAGRAM_ENABLED:
                    ig_caption = (
                        f"{title}\n\n{hash_line.lower()}"
                    )
                    upload_reel(output_path, ig_caption)
                    stats["instagram_ok"] += 1
                    print(f"    Instagram Reel: ✅ OK")
        except Exception as e:
            stats["instagram_fail"] += 1
            falhas_detalhadas.append(f"{category}/Instagram: {type(e).__name__}: {str(e)[:120]}")
            print(f"    ❌ Instagram Reel falhou: {type(e).__name__}: {e}")

        # Limpa arquivo local do Short
        try:
            os.remove(output_path)
        except Exception:
            pass

    # Resumo final detalhado
    print(f"\n{'='*60}")
    print(f"[Shorts/categoria] RESUMO:")
    print(f"  • Tentados:    {len(selected)} Shorts")
    print(f"  • YouTube:     ✅ {stats['youtube_ok']} ok / ❌ {stats['youtube_fail']} falhas")
    if stats["instagram_ok"] + stats["instagram_fail"] > 0:
        print(f"  • Instagram:   ✅ {stats['instagram_ok']} ok / ❌ {stats['instagram_fail']} falhas")
    if stats["ffmpeg_fail"]:
        print(f"  • ffmpeg:      ❌ {stats['ffmpeg_fail']} falhas no corte")
    if falhas_detalhadas:
        print(f"\n  FALHAS DETALHADAS:")
        for f in falhas_detalhadas:
            print(f"    • {f}")
    print(f"{'='*60}")
    return uploaded_ids


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
                # Exclui Shorts e episódios de podcast (Resumo de Notícias) — sem conteúdo útil para Short
                if "#Shorts" in title or "Short" in title or "Resumo de Notícias" in title:
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
