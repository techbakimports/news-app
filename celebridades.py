"""
Pipeline de Celebridades — gera Shorts de fofoca/entretenimento via Google News.

Fluxo: Google News RSS (sites de fofoca BR) -> top N notícias -> Groq/Gemini resume
       (tom gossip/entretenimento) -> generate_short_from_text -> YouTube

Uso:
    python celebridades.py
    python celebridades.py --sem-upload
    python celebridades.py --privado
    python celebridades.py --max 3
"""
from __future__ import annotations

import argparse
import asyncio
import logging
from logging.handlers import RotatingFileHandler
import os
import sys
import urllib.parse
from datetime import datetime
from dotenv import load_dotenv

os.chdir(os.path.dirname(os.path.abspath(__file__)))
load_dotenv()

from config import (
    AUDIO_OUTPUT_DIR,
    DRIVE_SYNC_DIR,
    SITES_CELEBRIDADES,
)
from fetcher import (
    _resolve_google_news_url,
    _hostname_of,
    _is_today,
    extract_article_content,
    _BROWSER_HEADERS,
)
from summarizer import select_top_n_relevant


# -- Logging -------------------------------------------------------------------

_LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
os.makedirs(_LOG_DIR, exist_ok=True)
_log_file = os.path.join(_LOG_DIR, "celebridades.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        RotatingFileHandler(_log_file, maxBytes=5*1024*1024, backupCount=0, encoding="utf-8"),
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


# -- Flags ---------------------------------------------------------------------

YOUTUBE_UPLOAD      = True
YOUTUBE_PUBLISH_NOW = True
MAX_CELEBS_PER_RUN  = 5   # máx de Shorts por execução

POST_YOUTUBE = True


# -- CTA -----------------------------------------------------------------------

_CTA = (
    " Gostou dessa novidade? Deixa o seu like, "
    "compartilha com aquela amiga que ama uma fofoca, "
    "e se inscreve no canal pra não perder nada dos famosos."
)


# -- Fetch notícias de celebridades via Google News RSS -----------------------

def _fetch_celebridades(limit_per_site: int = 5) -> list[dict]:
    """
    Busca notícias dos portais de fofoca/celebridades via Google News RSS.
    Retorna lista de dicts {title, link, source, summary, category}.
    """
    import feedparser
    import requests

    items = []
    for site in SITES_CELEBRIDADES:
        query = f"site:{site} when:1d"
        url = (
            f"https://news.google.com/rss/search?q={urllib.parse.quote(query)}"
            f"&hl=pt-BR&gl=BR&ceid=BR:pt-419"
        )
        try:
            r = requests.get(url, timeout=15, headers=_BROWSER_HEADERS)
            feed = feedparser.parse(r.content)
        except Exception as e:
            print(f"  Erro ao buscar {site}: {e}")
            continue

        count = 0
        for entry in feed.entries:
            if count >= limit_per_site:
                break
            if not _is_today(entry):
                continue
            raw_link = entry.link
            real_link = _resolve_google_news_url(raw_link)
            host = _hostname_of(real_link)
            items.append({
                "title":             entry.title,
                "link":              real_link,
                "source":            host if host and "google" not in host else site,
                "summary":           getattr(entry, "summary", ""),
                "category":          "Celebridades",
                "_published_parsed": getattr(entry, "published_parsed", None),
            })
            count += 1

    # Dedup por título (palavras significativas, mesmo critério de tech_news)
    seen_titles: list[set] = []
    unique = []
    for item in items:
        title_words = {w for w in item["title"].lower().split() if len(w) > 4}
        is_dup = any(
            len(title_words & sw) / max(1, min(len(title_words), len(sw))) >= 0.5
            for sw in seen_titles if sw
        )
        if not is_dup:
            seen_titles.append(title_words)
            unique.append(item)

    # Ordena por mais recente
    unique.sort(key=lambda x: x.get("_published_parsed") or (0,), reverse=True)

    # Pré-filtro: remove resultados esportivos (placar, partida, jogo)
    _SPORTS_BLACKLIST = {
        "vence", "empata", "derrota", "placar", "gol", "gols", "partida",
        "amistoso", "eliminado", "eliminação", "semifinal", "quartas",
        "classificação", "escalação", "lesão", "convocação", "convocado",
        "campeonato", "torneio", "liga", "rodada", "tabela",
    }
    antes = len(unique)
    unique = [
        item for item in unique
        if not any(w in item["title"].lower() for w in _SPORTS_BLACKLIST)
    ]
    if len(unique) < antes:
        print(f"  {antes - len(unique)} item(s) descartado(s) pelo filtro esportivo")

    print(f"  {len(items)} brutas → {len(unique)} únicas após dedup + filtro")
    return unique


# -- Resumo com tom de entretenimento/gossip ----------------------------------

def _summarize_celebridade(title: str, content: str) -> str | None:
    """
    Gera narração estilo gossip/entretenimento (~110-125 palavras, ~60s com CTA).
    Cadeia: Groq (primário) → Gemini (fallback) → None.
    """
    groq_key   = os.getenv("GROQ_API_KEY", "")
    gemini_key = os.getenv("GEMINI_API_KEY", "")

    prompt = (
        "Você é uma apresentadora animada de programa de entretenimento brasileiro, "
        "narrando uma notícia de famosos em formato Short para YouTube.\n\n"
        f"Título da notícia: {title}\n"
        f"Conteúdo (use como base factual):\n{content[:3000]}\n\n"
        "REGRAS OBRIGATÓRIAS:\n"
        "- NÃO leia o título. Comece direto com o fato mais suculento — sem apresentação\n"
        "- Texto entre 110 e 125 palavras (o CTA será adicionado depois, totalizando ~60s)\n"
        "- CONTEXTO: quem está ouvindo não sabe nada — diga quem é a pessoa, o que aconteceu e por que é relevante.\n"
        "- COERÊNCIA: escolha UM fio condutor (o fato principal) e siga-o do início ao fim sem desvios.\n"
        "  Cada frase deve decorrer naturalmente da anterior — como uma fofoca bem contada.\n"
        "- Tom: animado, leve, divertido — como fofoca entre amigas, mas sem difamar\n"
        "- Use linguagem coloquial brasileira natural (pode usar 'olha', 'gente', 'imagina')\n"
        "- NÃO use markdown, asteriscos, hashtags, símbolos ou listas\n"
        "- NÃO invente fatos — use apenas o que está no conteúdo fornecido\n"
        "- Termine com comentário leve que estimule o espectador a opinar nos comentários\n"
        "  (ex: 'E você, o que acha disso tudo? Comenta aqui embaixo!')\n"
        "- NÃO inclua o CTA de inscrição — ele será adicionado automaticamente\n\n"
        "Responda APENAS com o texto da narração, sem título nem formatação."
    )

    # 1) Groq primário
    if groq_key and groq_key not in ("", "cole_sua_chave_aqui"):
        try:
            from groq import Groq
            client = Groq(api_key=groq_key)
            resp = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.8,
            )
            text = resp.choices[0].message.content.strip()
            if text:
                print(f"  [Groq] narração gerada ({len(text.split())} palavras)")
                return text
        except Exception as e:
            print(f"  Groq falhou: {e}. Tentando Gemini...")

    # 2) Gemini fallback — usa google-genai (mesmo pacote do summarizer.py)
    if gemini_key and gemini_key not in ("", "cole_sua_chave_aqui"):
        try:
            from google import genai as google_genai
            client = google_genai.Client(api_key=gemini_key)
            response = client.models.generate_content(
                model="gemini-2.0-flash", contents=prompt
            )
            text = response.text.strip()
            if text:
                print(f"  [Gemini] narração gerada ({len(text.split())} palavras)")
                return text
        except Exception as e:
            print(f"  Gemini também falhou: {e}")

    print("  ❌ Nenhum LLM disponível para gerar narração de celebridade.")
    return None


# -- Pipeline principal --------------------------------------------------------

async def run_celebridades(on_progress=None, max_shorts: int | None = None) -> list[str]:
    """
    Pipeline Celebridades — Google News (portais de fofoca BR) -> Groq/Gemini -> Shorts.
    Retorna lista de video_ids postados.
    """
    print(f"--- Celebridades Pipeline: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')} ---")

    privacy  = "public" if YOUTUBE_PUBLISH_NOW else "private"
    date_str = datetime.now().strftime("%d/%m/%Y")
    limite   = max_shorts or MAX_CELEBS_PER_RUN

    # 1. Buscar notícias de celebridades
    print("\n[1/3] Buscando notícias de celebridades (Google News)...")
    if on_progress:
        try: await on_progress("Buscando notícias de celebridades...")
        except Exception: pass

    raw_items = _fetch_celebridades(limit_per_site=5)
    if not raw_items:
        print("Nenhuma notícia encontrada. Abortando.")
        try:
            from telegram_notifier import notify
            notify("⚠️ <b>Celebridades:</b> nenhuma notícia encontrada.")
        except Exception:
            pass
        return []

    # Filtra itens já postados nas últimas 48h
    from history import filter_not_posted, mark_as_posted
    raw_items, n_skip = filter_not_posted(raw_items)
    if n_skip:
        print(f"  {n_skip} item(s) ignorado(s) — já postados nas últimas 48h")

    if not raw_items:
        print("Todas as notícias já foram postadas recentemente. Abortando.")
        return []

    # 1.5. Trending topics + seleção por relevância
    print("\n[1.5/3] Coletando trending topics e selecionando mais relevantes...")
    trending = None
    try:
        from trends import get_trending_topics
        trending = get_trending_topics(use_cache=True)
        print(f"  Trending OK — {len(trending.get('twitter', []))} Twitter | {len(trending.get('google', []))} Google")
    except Exception as e:
        print(f"  Trending falhou (não crítico): {e}")

    items = select_top_n_relevant(
        "Celebridades — fofoca e entretenimento: atores, cantores, artistas, influencers e famosos BR. "
        "INCLUIR: romance, separação, polêmica, flagra, affair, reality show, novela, vida pessoal de famosos. "
        "EXCLUIR: resultados de partidas, placar, escalação, gol, classificação esportiva — "
        "esportistas só entram se for fofoca da vida pessoal deles.",
        raw_items, limite, trending=trending,
    )
    print(f"  {len(raw_items)} candidatas → {len(items)} selecionadas por relevância (limite={limite})")

    # 2. Extrair conteúdo + resumir
    print(f"\n[2/3] Extraindo conteúdo e resumindo ({len(items)} notícias)...")
    if on_progress:
        try: await on_progress(f"Resumindo {len(items)} notícias...")
        except Exception: pass

    items_com_narracao = []
    for i, item in enumerate(items, 1):
        print(f"  [{i}/{len(items)}] {item['title'][:70]}")
        content = extract_article_content(item["link"])
        item["_content"] = content if content else item.get("summary", "")

        narracao = _summarize_celebridade(item["title"], item["_content"])
        if narracao:
            item["narracao"] = narracao + _CTA
            items_com_narracao.append(item)
        else:
            print(f"    ⚠️  Sem narração — pulando")

    print(f"  Com narração: {len(items_com_narracao)} | pulados: {len(items) - len(items_com_narracao)}")

    if not items_com_narracao:
        msg = "❌ <b>Celebridades:</b> pipeline abortado — nenhuma narração gerada (Groq + Gemini falharam)."
        print(msg)
        try:
            from telegram_notifier import notify
            notify(msg)
        except Exception:
            pass
        return []

    # Salva roteiro no Drive
    os.makedirs(DRIVE_SYNC_DIR, exist_ok=True)
    timestamp   = datetime.now().strftime("%Y%m%d_%H%M")
    md_path     = os.path.join(DRIVE_SYNC_DIR, f"Celebridades_{timestamp}.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# Celebridades Shorts — {date_str}\n\n")
        for i, item in enumerate(items_com_narracao, 1):
            f.write(f"## {i}. {item['title']}\n\n")
            f.write(f"{item['narracao']}\n\n")
            f.write(f"Fonte: {item['source']}\nLink: {item['link']}\n\n---\n\n")
    print(f"Roteiro salvo: {md_path}")

    # 3. Gerar Shorts
    print(f"\n[3/3] Gerando {len(items_com_narracao)} Shorts...")
    if on_progress:
        try: await on_progress(f"Gerando {len(items_com_narracao)} Shorts...")
        except Exception: pass

    from shorts import generate_short_from_text

    celeb_hashtags = ["Shorts", "Celebridades", "Famosos", "Fofoca", "Entretenimento", "Brasil"]
    uploaded_ids: list[str] = []

    for i, item in enumerate(items_com_narracao, 1):
        print(f"\n  ── Short {i}/{len(items_com_narracao)} ──")
        title    = item["title"]
        narracao = item["narracao"]
        source   = item.get("source", "")

        if not YOUTUBE_UPLOAD:
            try:
                path = await generate_short_from_text(
                    title=title,
                    narration=narracao,
                    category="Celebridades",
                    source=source,
                    upload=False,
                    privacy=privacy,
                    hashtags=celeb_hashtags,
                    playlist_key="celebridades",
                    instagram_enabled=False,
                    link=item.get("link"),
                    voice="pt-BR-ThalitaNeural",
                )
                print(f"  Vídeo local: {path}")
            except Exception as e:
                print(f"  Erro: {e}")
            continue

        try:
            video_id = await generate_short_from_text(
                title=title,
                narration=narracao,
                category="Celebridades",
                source=source,
                upload=True,
                privacy=privacy,
                hashtags=celeb_hashtags,
                playlist_key="celebridades",
                instagram_enabled=False,
                link=item.get("link"),
                voice="pt-BR-ThalitaNeural",
            )
            if video_id:
                uploaded_ids.append(video_id)
                print(f"  ✅ https://youtu.be/{video_id}")
                mark_as_posted(title, pipeline="celebridades")
        except Exception as e:
            print(f"  Erro no Short {i}: {e}")

        # Espaçamento entre Shorts para não canibalizar o alcance no algoritmo
        if i < len(items_com_narracao):
            print(f"\n  ⏳ Aguardando 10 min antes do próximo Short ({i+1}/{len(items_com_narracao)})...")
            await asyncio.sleep(600)

    # Notificação final
    try:
        from telegram_notifier import notify
        if uploaded_ids:
            notify(
                f"✅ <b>Celebridades postado!</b>\n"
                f"{len(uploaded_ids)} Short(s) no ar.\n"
                f"Primeiro: https://youtu.be/{uploaded_ids[0]}"
            )
        elif YOUTUBE_UPLOAD:
            notify("⚠️ <b>Celebridades:</b> nenhum Short foi enviado ao YouTube.")
    except Exception:
        pass

    return uploaded_ids


# -- Entry point ---------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog="celebridades.py", add_help=True)
    parser.add_argument("--sem-upload", action="store_true", help="só gera, sem upload")
    parser.add_argument("--privado",    action="store_true", help="publica como privado")
    parser.add_argument("--max", type=int, default=None, help=f"máx Shorts (padrão {MAX_CELEBS_PER_RUN})")
    args, _ = parser.parse_known_args()

    if args.sem_upload:
        YOUTUBE_UPLOAD = False
    if args.privado:
        YOUTUBE_PUBLISH_NOW = False

    if YOUTUBE_UPLOAD:
        from uploader import check_youtube_token
        ok, msg = check_youtube_token()
        if not ok:
            print(f"❌ Token YouTube inválido: {msg}")
            sys.exit(1)

    asyncio.run(run_celebridades(max_shorts=args.max))
