"""
Pipeline de Tech Shorts — produz APENAS Shorts (sem vídeo longo).

Fluxo: Google News RSS (10 sites tech) -> top N tópicos -> Groq/Gemini resume -> 1 Short por tópico -> YouTube + TikTok

NotebookLM removido — fonte agora é o mesmo Google News RSS do main.py,
filtrado por sites tech.

Uso:
    python tech_news.py
    python tech_news.py --sem-upload
    python tech_news.py --privado
"""
import argparse
import asyncio
import logging
import os
import urllib.parse
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

from config import DRIVE_SYNC_DIR, AUDIO_OUTPUT_DIR, CHANNEL_NAME
from fetcher import (
    _resolve_google_news_url,
    _hostname_of,
    _is_today,
    extract_article_content,
    _BROWSER_HEADERS,
)
from summarizer import summarize_news_batch

# Logging
_LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
os.makedirs(_LOG_DIR, exist_ok=True)
_log_file = os.path.join(_LOG_DIR, "tech_news.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.FileHandler(_log_file, encoding="utf-8"),
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


# -- Flags ---------------------------------------------------------------------

YOUTUBE_UPLOAD = True
YOUTUBE_PUBLISH_NOW = True
MAX_TECH_SHORTS_PER_RUN = 5


# -- Sites tech (filtros de busca no Google News) ------------------------------

TECH_SITES = [
    "tecmundo.com.br",
    "tecnoblog.net",
    "olhardigital.com.br",
    "canaltech.com.br",
    "tudocelular.com",
    "gizmodo.uol.com.br",
    "theverge.com",
    "techcrunch.com",
    "arstechnica.com",
    "wired.com",
]


# -- Fetch tech news via Google News RSS --------------------------------------

def _fetch_tech_via_google_news(limit_per_site: int = 5) -> list[dict]:
    """
    Busca notícias dos sites tech via Google News RSS.
    Retorna lista de dicts {title, link (resolvido), source, summary}.
    """
    import feedparser
    import requests

    items = []
    for site in TECH_SITES:
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
                "title": entry.title,
                "link": real_link,
                "source": host if host and "google" not in host else site,
                "summary": getattr(entry, "summary", ""),
                "category": "Tecnologia",
                "_published_parsed": getattr(entry, "published_parsed", None),
            })
            count += 1

    # Dedup por título (palavras significativas)
    seen_titles = []
    unique = []
    for item in items:
        title_words = {
            w for w in item["title"].lower().split()
            if len(w) > 4
        }
        is_dup = any(len(title_words & sw) / max(1, min(len(title_words), len(sw))) >= 0.5
                     for sw in seen_titles if sw)
        if not is_dup:
            seen_titles.append(title_words)
            unique.append(item)

    # Ordena por mais recente
    unique.sort(
        key=lambda x: x.get("_published_parsed") or (0,),
        reverse=True,
    )
    return unique


# -- Pipeline principal --------------------------------------------------------

async def run_tech_news(on_progress=None):
    """
    Pipeline Tech Shorts — Google News (10 sites tech) -> Groq -> Shorts.
    """
    print(f"--- Tech Shorts Pipeline: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')} ---")

    privacy = "public" if YOUTUBE_PUBLISH_NOW else "private"
    date_str = datetime.now().strftime("%d/%m/%Y")

    # 1. Buscar notícias tech via Google News RSS
    print("\n[1/3] Buscando notícias de tecnologia (Google News)...")
    if on_progress:
        try: await on_progress("Buscando notícias tech...")
        except Exception: pass

    raw_items = _fetch_tech_via_google_news(limit_per_site=5)
    if not raw_items:
        print("Nenhuma notícia encontrada. Abortando.")
        return None

    items = raw_items[:MAX_TECH_SHORTS_PER_RUN]
    print(f"  {len(raw_items)} candidatas → {len(items)} selecionadas")

    # 2. Extrair conteúdo + resumir via Groq → Gemini
    print(f"\n[2/3] Extraindo conteúdo e resumindo ({len(items)} notícias)...")
    if on_progress:
        try: await on_progress(f"Resumindo {len(items)} tópicos...")
        except Exception: pass

    for item in items:
        content = extract_article_content(item["link"])
        item["_content"] = content if content else item.get("summary", "")

    batch_input = [
        {"category": i["category"], "title": i["title"], "content": i["_content"]}
        for i in items
    ]
    summaries = summarize_news_batch(batch_input)

    # Filtra itens SEM resumo de LLM — não geramos Short só com título
    items_com_resumo = []
    pulados = 0
    for item, summary in zip(items, summaries):
        if summary:
            item["ai_summary"] = summary
            items_com_resumo.append(item)
        else:
            pulados += 1

    print(f"  Tópicos com resumo: {len(items_com_resumo)} | pulados sem resumo: {pulados}")

    if not items_com_resumo:
        msg = "❌ Nenhum tópico teve resumo de LLM. Pipeline ABORTADO (Groq + Gemini falharam)."
        print(f"\n{msg}")
        try:
            from telegram_notifier import notify
            notify(
                f"❌ <b>Tech Shorts:</b> pipeline abortado.\n"
                f"Nenhum tópico com resumo válido.\nVerifique Groq e Gemini."
            )
        except Exception:
            pass
        return None

    items = items_com_resumo

    # Salva roteiro consolidado no Drive
    os.makedirs(DRIVE_SYNC_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    filename_base = f"Tech_Shorts_{timestamp}"
    md_path = os.path.join(DRIVE_SYNC_DIR, f"{filename_base}.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# Tech Shorts - {date_str}\n\n")
        for i, item in enumerate(items, 1):
            f.write(f"## {i}. {item.get('title', '')}\n\n")
            f.write(f"{item.get('ai_summary', '')}\n\n")
            f.write(f"Fonte: {item.get('source', '')}\nLink: {item.get('link', '')}\n\n---\n\n")
    print(f"Roteiro salvo: {md_path}")

    # 3. Gerar Shorts (1 por notícia)
    print(f"\n[3/3] Gerando {len(items)} Shorts (YouTube + TikTok)...")
    if on_progress:
        try: await on_progress(f"Gerando {len(items)} Shorts...")
        except Exception: pass

    from shorts import generate_short_from_text

    uploaded_ids = []
    tech_hashtags = ["Shorts", "Tecnologia", "Tech", "IA", "Inovação"]

    for i, item in enumerate(items, 1):
        print(f"\n  ── Short {i}/{len(items)} ──")
        title = item.get("title", "")
        narration = item["ai_summary"]  # garantido (filtramos acima)
        source = item.get("source", "Tech")

        if not YOUTUBE_UPLOAD:
            try:
                path, _ = await generate_short_from_text(
                    title=title, narration=narration,
                    category="Tecnologia", source=source,
                    upload=False, privacy=privacy,
                    hashtags=tech_hashtags, playlist_key="tech",
                    instagram_enabled=False,
                    link=item.get("link"),
                )
                print(f"  Vídeo local: {path}")
            except Exception as e:
                print(f"  Erro: {e}")
            continue

        try:
            video_id, _tk_ok = await generate_short_from_text(
                title=title, narration=narration,
                category="Tecnologia", source=source,
                upload=True, privacy=privacy,
                hashtags=tech_hashtags, playlist_key="tech",
                instagram_enabled=False,
                link=item.get("link"),
            )
            if video_id:
                uploaded_ids.append(video_id)
        except Exception as e:
            print(f"  Erro no Short {i}: {e}")

    # Notificação final
    from telegram_notifier import notify
    if uploaded_ids:
        first = uploaded_ids[0]
        notify(
            f"✅ <b>Tech Shorts postados!</b>\n"
            f"{len(uploaded_ids)} Short(s) no ar.\n"
            f"Primeiro: https://youtu.be/{first}"
        )
    elif YOUTUBE_UPLOAD:
        notify(f"⚠️ <b>Tech Shorts:</b> nenhum Short foi enviado.")

    return uploaded_ids


# -- Entry point ---------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog="tech_news.py", add_help=False)
    parser.add_argument("--sem-upload", action="store_true")
    parser.add_argument("--privado", action="store_true")
    args, _ = parser.parse_known_args()

    if args.sem_upload:
        YOUTUBE_UPLOAD = False
    if args.privado:
        YOUTUBE_PUBLISH_NOW = False

    asyncio.run(run_tech_news())
