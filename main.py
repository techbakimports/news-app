"""
Pipeline de Notícias — gera APENAS Shorts (sem vídeo longo).

Fluxo: Google News -> 1 notícia por categoria selecionada -> resumo Groq/Gemini (~400 palavras)
       -> generate_short_from_text -> YouTube

Categorias configuradas em config.NEWS_SHORTS_CATEGORIES.
Cada notícia vira 1 Short denso (~3 min) com CTA, igual ao formato Curiosidades.

Uso:
    python main.py             # YouTube público
    python main.py --privado   # YouTube privado
    python main.py --sem-upload # só gera local
"""
import argparse
import asyncio
import logging
from logging.handlers import RotatingFileHandler
import os
import time
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

from fetcher import fetch_latest_news, extract_article_content, select_unique_news
from summarizer import summarize_news_for_short, select_most_relevant
from config import DRIVE_SYNC_DIR, AUDIO_OUTPUT_DIR, NEWS_SHORTS_CATEGORIES

# Logging — monitorar com: tail -f logs/noticias.log
_LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
os.makedirs(_LOG_DIR, exist_ok=True)
_log_file = os.path.join(_LOG_DIR, "noticias.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        RotatingFileHandler(_log_file, maxBytes=5*1024*1024, backupCount=2, encoding="utf-8"),
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

POST_YOUTUBE = True

# Configuração de limpeza
CLEANUP_HOURS = 24


def cleanup_old_files():
    """Apaga arquivos antigos no Drive e na pasta de áudio."""
    now = time.time()
    cutoff = now - (CLEANUP_HOURS * 3600)

    for folder in [DRIVE_SYNC_DIR, AUDIO_OUTPUT_DIR]:
        if not os.path.exists(folder):
            continue
        for f in os.listdir(folder):
            path = os.path.join(folder, f)
            if os.path.getmtime(path) < cutoff:
                try:
                    os.remove(path)
                    print(f"Arquivo antigo removido: {f}")
                except Exception as e:
                    print(f"Erro ao remover {f}: {e}")


# -- Pipeline principal --------------------------------------------------------

async def run_news_cycle(on_progress=None):
    """
    Pipeline Notícias — 1 Short por categoria selecionada.
    Retorna lista de (categoria, video_id) das execuções.
    """
    pipeline_start = time.time()

    def _elapsed():
        m, s = divmod(int(time.time() - pipeline_start), 60)
        return f"[T+{m:02d}:{s:02d}]"

    print(f"--- Pipeline Notícias (Shorts): {datetime.now().strftime('%d/%m/%Y %H:%M:%S')} ---")
    print(f"Categorias: {', '.join(NEWS_SHORTS_CATEGORIES)}")
    cleanup_old_files()

    privacy = "public" if YOUTUBE_PUBLISH_NOW else "private"

    print(f"Plataforma: YouTube")

    # ---------- Fase 1: Fetch ----------
    # limit=5 por (categoria × site) — pool maior garante candidatos suficientes
    # para o LLM escolher a mais relevante em vez de pegar só a primeira.
    print(f"\n{_elapsed()} [FASE 1] Buscando notícias das categorias selecionadas...")
    phase_start = time.time()
    raw_news = fetch_latest_news(limit=5, categories=NEWS_SHORTS_CATEGORIES)
    print(f"{_elapsed()} [FASE 1] OK — {len(raw_news)} candidatos em {int(time.time()-phase_start)}s")

    if not raw_news:
        print("Nenhuma notícia encontrada. Abortando.")
        try:
            from telegram_notifier import notify
            notify("❌ <b>Notícias:</b> nenhuma notícia encontrada nas categorias selecionadas.")
        except Exception:
            pass
        return []

    # ---------- Fase 1.5: Trending topics (sinal de engajamento) ----------
    print(f"\n{_elapsed()} [FASE 1.5] Coletando trending topics...")
    trending = None
    try:
        from trends import get_trending_topics
        trending = get_trending_topics(use_cache=True)
        tw_count = len(trending.get("twitter", []))
        gg_count = len(trending.get("google",  []))
        print(f"{_elapsed()} [FASE 1.5] OK — {tw_count} Twitter | {gg_count} Google")
    except Exception as e:
        print(f"{_elapsed()} [FASE 1.5] Falhou (não crítico): {e}")
        trending = None

    # ---------- Fase 2: Dedup + seleção por relevância ----------
    print(f"\n{_elapsed()} [FASE 2] Deduplicando e selecionando mais relevante por categoria...")
    items_unicos = select_unique_news(raw_news)
    print(f"{_elapsed()} [FASE 2] {len(items_unicos)} únicas após dedup")

    # Filtra itens já postados nas últimas 48h
    from history import filter_not_posted, mark_as_posted
    items_unicos, n_skip = filter_not_posted(items_unicos)
    if n_skip:
        print(f"{_elapsed()} [FASE 2] {n_skip} item(s) ignorado(s) — já postados nas últimas 48h")

    # Agrupa TODOS os candidatos por categoria
    pool_por_categoria: dict[str, list] = {}
    for item in items_unicos:
        cat = item.get("category", "")
        if cat in NEWS_SHORTS_CATEGORIES:
            pool_por_categoria.setdefault(cat, []).append(item)

    # Para cada categoria, usa LLM pra escolher a mais relevante do pool
    # passando os trending topics como contexto de engajamento
    items_selecionados = []
    for cat in NEWS_SHORTS_CATEGORIES:
        candidatos = pool_por_categoria.get(cat, [])
        if not candidatos:
            print(f"  ⚠️  Sem candidatos para '{cat}'")
            continue
        print(f"\n  → '{cat}': {len(candidatos)} candidatos — selecionando mais relevante...")
        escolhido = select_most_relevant(cat, candidatos, trending=trending)
        items_selecionados.append(escolhido)

    print(f"\n{_elapsed()} [FASE 2] {len(items_selecionados)}/{len(NEWS_SHORTS_CATEGORIES)} categorias com notícia selecionada")

    if not items_selecionados:
        print("Nenhuma categoria teve notícia válida. Abortando.")
        try:
            from telegram_notifier import notify
            notify("❌ <b>Notícias:</b> nenhuma categoria teve notícia válida.")
        except Exception:
            pass
        return []

    # ---------- Fase 3: Extrai conteúdo + Resumo longo via Groq/Gemini ----------
    print(f"\n{_elapsed()} [FASE 3] Extraindo conteúdo e gerando resumos longos...")
    phase_start = time.time()

    for item in items_selecionados:
        content = extract_article_content(item["link"])
        item["_content"] = content if content else item.get("summary", "")

    items_com_narracao = []
    for item in items_selecionados:
        cat = item["category"]
        print(f"\n  → {cat}: resumindo...")
        result = summarize_news_for_short(
            category=cat,
            title=item["title"],
            content=item["_content"],
        )
        if result:
            narracao, corrected_cat = result
            if corrected_cat != cat:
                print(f"  📌 Categoria corrigida: {cat} → {corrected_cat}")
                item["category"] = corrected_cat
            item["narracao"] = narracao
            items_com_narracao.append(item)
        else:
            print(f"  ⚠️ {cat}: sem narração (LLMs falharam) — pulando")

    print(f"\n{_elapsed()} [FASE 3] OK em {int(time.time()-phase_start)}s — "
          f"{len(items_com_narracao)} narrações geradas")

    if not items_com_narracao:
        print("Nenhuma categoria teve resumo válido. Pipeline ABORTADO.")
        try:
            from telegram_notifier import notify
            notify(
                f"❌ <b>Notícias:</b> pipeline abortado.\n"
                f"Nenhuma categoria teve resumo válido (Groq + Gemini falharam)."
            )
        except Exception:
            pass
        return []

    # Salva roteiro consolidado no Drive (rastreabilidade)
    os.makedirs(DRIVE_SYNC_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    md_path = os.path.join(DRIVE_SYNC_DIR, f"Noticias_Shorts_{timestamp}.md")
    date_str = datetime.now().strftime("%d/%m/%Y")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# Notícias Shorts — {date_str}\n\n")
        for i, item in enumerate(items_com_narracao, 1):
            f.write(f"## {i}. [{item['category']}] {item['title']}\n\n")
            f.write(f"{item['narracao']}\n\n")
            f.write(f"Fonte: {item.get('source', '')}\nLink: {item.get('link', '')}\n\n---\n\n")
    print(f"Roteiro salvo: {md_path}")

    # ---------- Fase 4: Gera 1 Short por notícia ----------
    print(f"\n{_elapsed()} [FASE 4] Gerando {len(items_com_narracao)} Shorts...")
    phase_start = time.time()

    from shorts import generate_short_from_text

    resultados = []  # [(categoria, video_id), ...]
    for i, item in enumerate(items_com_narracao, 1):
        cat = item["category"]
        print(f"\n  ── Short {i}/{len(items_com_narracao)} — {cat} ──")

        cta = (
            " Curtiu essa notícia? Então deixa o like, "
            "compartilha com quem precisa saber, e se inscreve no canal "
            "pra receber as notícias do dia em formato Short."
        )
        narracao_final = item["narracao"].rstrip() + cta

        cat_hashtags = ["Shorts", "Notícias", "Brasil", cat.replace(" ", "")]

        if not YOUTUBE_UPLOAD:
            try:
                path = await generate_short_from_text(
                    title=item["title"],
                    narration=narracao_final,
                    category=cat,
                    source=item.get("source", ""),
                    upload=False,
                    privacy=privacy,
                    hashtags=cat_hashtags,
                    playlist_key="noticias",
                    instagram_enabled=False,
                    link=item.get("link"),
                )
                print(f"  Vídeo local: {path}")
                resultados.append((cat, None))
            except Exception as e:
                print(f"  Erro: {e}")
            continue

        try:
            video_id = await generate_short_from_text(
                title=item["title"],
                narration=narracao_final,
                category=cat,
                source=item.get("source", ""),
                upload=True,
                privacy=privacy,
                hashtags=cat_hashtags,
                playlist_key="noticias",
                instagram_enabled=False,
                link=item.get("link"),
            )
            if video_id:
                mark_as_posted(item["title"], pipeline="noticias")
            resultados.append((cat, video_id))
        except Exception as e:
            print(f"  ❌ Erro no Short {i} ({cat}): {e}")
            resultados.append((cat, None))

        # Espaçamento entre Shorts para não canibalizar o alcance no algoritmo
        if i < len(items_com_narracao):
            print(f"\n  ⏳ Aguardando 10 min antes do próximo Short ({i+1}/{len(items_com_narracao)})...")
            await asyncio.sleep(600)

    print(f"\n{_elapsed()} [FASE 4] OK em {int(time.time()-phase_start)}s")

    # ---------- Resumo final + notificação ----------
    total_min, total_sec = divmod(int(time.time() - pipeline_start), 60)
    print(f"\n{_elapsed()} === PIPELINE CONCLUÍDO === ({total_min}m{total_sec:02d}s totais)")

    yt_ok = sum(1 for _, vid in resultados if vid)
    print(f"  YouTube: ✅ {yt_ok}/{len(resultados)}")

    if YOUTUBE_UPLOAD:
        try:
            from telegram_notifier import notify
            linhas = [f"✅ <b>Notícias postadas!</b> ({total_min}m{total_sec:02d}s)"]
            linhas.append(f"📺 YouTube: {yt_ok}/{len(resultados)}")
            linhas.append("")
            for cat, vid in resultados:
                linha = f"• {cat}: {'✅' if vid else '❌'}"
                if vid:
                    linha += f" — https://youtu.be/{vid}"
                linhas.append(linha)
            notify("\n".join(linhas))
        except Exception:
            pass

    return resultados


# -- Entry point ---------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog="main.py", add_help=True)
    parser.add_argument("--sem-upload", action="store_true", help="só gera local, sem upload em nenhuma plataforma")
    parser.add_argument("--privado", action="store_true", help="publica como privado no YouTube")
    args, _ = parser.parse_known_args()

    if args.sem_upload:
        YOUTUBE_UPLOAD = False
    if args.privado:
        YOUTUBE_PUBLISH_NOW = False

    asyncio.run(run_news_cycle())
