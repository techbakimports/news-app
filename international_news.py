"""
Pipeline de Notícias Internacionais — gera Shorts de manchetes de outros países,
cada um publicado no canal do YouTube próprio daquele país/idioma.

Fluxo: Google News top-headlines por locale (hl/gl/ceid) -> Groq/Gemini gera
       narração NO IDIOMA do país -> generate_short_from_text (voz + canal
       próprios) -> YouTube.

Locales configurados em config.py (LOCALES): india, japao, franca, alemanha, italia.

Uso:
    python international_news.py --locale japao
    python international_news.py --locale india --sem-upload
    python international_news.py --locale franca --privado
    python international_news.py --locale italia --max 2
"""
from __future__ import annotations

import argparse
import asyncio
import logging
from logging.handlers import RotatingFileHandler
import os
import sys
from datetime import datetime
from dotenv import load_dotenv

os.chdir(os.path.dirname(os.path.abspath(__file__)))
load_dotenv()


# -- Logging ---------------------------------------------------------------------
# Um arquivo de log por locale (logs/international_<locale>.log), decidido em
# tempo de execução (depende do --locale), diferente do padrão fixo dos outros
# pipelines — por isso a configuração roda via _init_logging(), chamada no
# entry point, em vez de no import do módulo.

_orig_print = print
log: logging.Logger | None = None


def _init_logging(locale: str) -> None:
    global log
    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, f"international_{locale}.log")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(message)s",
        datefmt="%H:%M:%S",
        handlers=[
            RotatingFileHandler(log_file, maxBytes=5*1024*1024, backupCount=0, encoding="utf-8"),
            logging.StreamHandler(),
        ],
        force=True,
    )
    log = logging.getLogger(__name__)


def print(*args, **kwargs):  # noqa: A001
    _orig_print(*args, **kwargs)
    msg = " ".join(str(a) for a in args)
    if msg.strip() and log:
        log.info(msg)


if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


# -- Flags -------------------------------------------------------------------------

YOUTUBE_UPLOAD      = True
YOUTUBE_PUBLISH_NOW = True
MAX_SHORTS_PER_RUN  = 3

_LANGUAGE_NAMES = {
    "en": "English",
    "ja": "Japanese",
    "fr": "French",
    "de": "German",
    "it": "Italian",
}


# -- Narração no idioma do locale (Groq → Gemini) ----------------------------------

def _generate_international_narration(title: str, content: str, locale: dict) -> str | None:
    """
    Gera narração de notícia (~50-60s) NO IDIOMA do locale.
    Cadeia: Groq (primário) → Gemini (fallback) → None.
    """
    groq_key   = os.getenv("GROQ_API_KEY", "")
    gemini_key = os.getenv("GEMINI_API_KEY", "")

    lang_name = _LANGUAGE_NAMES.get(locale["language"], locale["language"])

    # Japonês não se mede bem por contagem de palavras (não usa espaços) —
    # instrução de tamanho por caracteres nesse caso.
    if locale["language"] == "ja":
        length_rule = "- Length: approximately 150-200 Japanese characters (not word count)."
    else:
        length_rule = "- Between 90 and 110 words."

    prompt = (
        "You are a professional news anchor recording a voiceover for a YouTube Short.\n\n"
        f"Headline: {title}\n"
        f"Source content (use as factual basis):\n{content[:3000]}\n\n"
        "MANDATORY RULES:\n"
        f"- Write the ENTIRE response in {lang_name}. Do not use any other language, "
        "not even for a single word.\n"
        "- Do NOT read the headline verbatim. Start directly with the most important fact.\n"
        f"{length_rule}\n"
        "- CONTEXT: the listener has read nothing about this — be self-contained: "
        "who is involved, what happened, and why it matters.\n"
        "- COHERENCE: pick ONE throughline and follow it start to finish, no topic jumps.\n"
        "- Natural, clear spoken tone, like a professional news narrator — not a formal report.\n"
        "- Do NOT use markdown, asterisks, hashtags, symbols, or lists.\n"
        "- Do NOT invent facts beyond what is in the source content.\n"
        "- Do NOT include a subscribe call-to-action — it will be added separately.\n\n"
        f"Reply with ONLY the narration text in {lang_name}, no title, no extra formatting."
    )

    if groq_key and groq_key not in ("", "cole_sua_chave_aqui"):
        try:
            from groq import Groq
            client = Groq(api_key=groq_key)
            resp = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
            )
            text = resp.choices[0].message.content.strip()
            if text:
                print(f"  [Groq] narração gerada ({len(text.split())} tokens, {lang_name})")
                return text
        except Exception as e:
            print(f"  Groq falhou: {e}. Tentando Gemini...")

    if gemini_key and gemini_key not in ("", "cole_sua_chave_aqui"):
        try:
            from google import genai as google_genai
            client = google_genai.Client(api_key=gemini_key)
            response = client.models.generate_content(model="gemini-2.0-flash", contents=prompt)
            text = response.text.strip()
            if text:
                print(f"  [Gemini] narração gerada ({len(text.split())} tokens, {lang_name})")
                return text
        except Exception as e:
            print(f"  Gemini também falhou: {e}")

    print("  ❌ Nenhum LLM disponível para gerar narração internacional.")
    return None


# -- Pipeline principal --------------------------------------------------------------

async def run_international(locale_key: str, on_progress=None, max_shorts: int | None = None) -> list[str]:
    """
    Pipeline de Notícias Internacionais pra um locale específico.
    Retorna lista de video_ids postados no canal daquele país.
    """
    from config import LOCALES
    from fetcher import fetch_topheadlines_by_locale, extract_article_content

    locale = LOCALES[locale_key]
    print(f"--- International News ({locale['label']}): {datetime.now().strftime('%d/%m/%Y %H:%M:%S')} ---")

    privacy = "public" if YOUTUBE_PUBLISH_NOW else "private"
    limite  = max_shorts or MAX_SHORTS_PER_RUN

    # 1. Buscar manchetes
    print(f"\n[1/3] Buscando manchetes ({locale['label']})...")
    if on_progress:
        try: await on_progress(f"Buscando manchetes de {locale['label']}...")
        except Exception: pass

    raw_items = fetch_topheadlines_by_locale(locale["hl"], locale["gl"], locale["ceid"], limit=15)
    if not raw_items:
        print("Nenhuma notícia encontrada. Abortando.")
        try:
            from telegram_notifier import notify
            notify(f"⚠️ <b>International News ({locale['label']}):</b> nenhuma notícia encontrada.")
        except Exception:
            pass
        return []

    # Filtra itens já postados nas últimas 48h (dedupe por título)
    from history import filter_not_posted, mark_as_posted
    raw_items, n_skip = filter_not_posted(raw_items)
    if n_skip:
        print(f"  {n_skip} item(s) ignorado(s) — já postados nas últimas 48h")

    if not raw_items:
        print("Todas as notícias já foram postadas recentemente. Abortando.")
        return []

    items = raw_items[:limite]
    print(f"  {len(raw_items)} candidatas → {len(items)} selecionadas (limite={limite})")

    # 2. Extrair conteúdo + gerar narração
    print(f"\n[2/3] Extraindo conteúdo e gerando narração ({len(items)} notícias, idioma: {locale['language']})...")
    if on_progress:
        try: await on_progress(f"Gerando narração em {locale['label']}...")
        except Exception: pass

    items_com_narracao = []
    for i, item in enumerate(items, 1):
        print(f"  [{i}/{len(items)}] {item['title'][:70]}")
        content = extract_article_content(item["link"])
        item["_content"] = content if content else item.get("summary", "")

        narracao = _generate_international_narration(item["title"], item["_content"], locale)
        if narracao:
            item["narracao"] = narracao
            items_com_narracao.append(item)
        else:
            print(f"    ⚠️  Sem narração — pulando")

    print(f"  Com narração: {len(items_com_narracao)} | pulados: {len(items) - len(items_com_narracao)}")

    if not items_com_narracao:
        msg = f"❌ <b>International News ({locale['label']}):</b> pipeline abortado — nenhuma narração gerada."
        print(msg)
        try:
            from telegram_notifier import notify
            notify(msg)
        except Exception:
            pass
        return []

    # 3. Gerar Shorts
    print(f"\n[3/3] Gerando {len(items_com_narracao)} Shorts...")
    if on_progress:
        try: await on_progress(f"Gerando {len(items_com_narracao)} Shorts...")
        except Exception: pass

    from shorts import generate_short_from_text

    uploaded_ids: list[str] = []

    for i, item in enumerate(items_com_narracao, 1):
        print(f"\n  ── Short {i}/{len(items_com_narracao)} ({locale['label']}) ──")
        title    = item["title"]
        narracao = item["narracao"]
        source   = item.get("source", "")

        common_kwargs = dict(
            title=title,
            narration=narracao,
            category=locale["badge_label"],
            source=source,
            hashtags=locale["hashtags"],
            playlist_key=locale["playlist_key"],
            instagram_enabled=False,
            youtube_enabled=True,
            link=item.get("link"),
            voice=locale["voice"],
            language=locale["language"],
            token_file=locale["token_file"],
            link_label=locale["link_label"],
            source_label=locale["source_label"],
            cjk_font=locale.get("cjk_font", False),
        )

        if not YOUTUBE_UPLOAD:
            try:
                path = await generate_short_from_text(**common_kwargs, upload=False, privacy=privacy)
                print(f"  Vídeo local: {path}")
            except Exception as e:
                print(f"  Erro: {e}")
            continue

        try:
            video_id = await generate_short_from_text(**common_kwargs, upload=True, privacy=privacy)
            if video_id:
                uploaded_ids.append(video_id)
                print(f"  ✅ https://youtu.be/{video_id}")
                mark_as_posted(title, pipeline=f"international_{locale_key}")
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
                f"✅ <b>International News ({locale['label']}) postado!</b>\n"
                f"{len(uploaded_ids)} Short(s) no ar.\n"
                f"Primeiro: https://youtu.be/{uploaded_ids[0]}"
            )
        elif YOUTUBE_UPLOAD:
            notify(f"⚠️ <b>International News ({locale['label']}):</b> nenhum Short foi enviado ao YouTube.")
    except Exception:
        pass

    return uploaded_ids


# -- Entry point -----------------------------------------------------------------------

if __name__ == "__main__":
    from config import LOCALES

    parser = argparse.ArgumentParser(prog="international_news.py", add_help=True)
    parser.add_argument("--locale", required=True, choices=list(LOCALES.keys()), help="país/locale a rodar")
    parser.add_argument("--sem-upload", action="store_true", help="só gera, sem upload")
    parser.add_argument("--privado",    action="store_true", help="publica como privado")
    parser.add_argument("--max", type=int, default=None, help=f"máx Shorts (padrão {MAX_SHORTS_PER_RUN})")
    args, _ = parser.parse_known_args()

    _init_logging(args.locale)

    if args.sem_upload:
        YOUTUBE_UPLOAD = False
    if args.privado:
        YOUTUBE_PUBLISH_NOW = False

    if YOUTUBE_UPLOAD:
        from uploader import check_youtube_token
        token_file = LOCALES[args.locale]["token_file"]
        ok, msg = check_youtube_token(token_file=token_file)
        if not ok:
            print(f"❌ Token YouTube inválido ({args.locale}): {msg}")
            sys.exit(1)

    asyncio.run(run_international(args.locale, max_shorts=args.max))
