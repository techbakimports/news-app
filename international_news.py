"""
Pipeline de Notícias Internacionais — mesmo fluxo e categorias do pipeline
de Notícias BR (main.py), mas por país: Índia, Japão, França, Alemanha, Itália.
Cada país publica no canal do YouTube próprio, no seu idioma — os canais são
"filiais" do canal principal, com a mesma estrutura de conteúdo.

Fluxo (idêntico ao main.py, com fontes/idioma/canal parametrizados por locale):
  Google News por categoria -> trending topics (Twitter/Google/YouTube do país)
  -> dedup -> LLM escolhe a mais relevante por categoria -> narração densa
  (~350 palavras, ~3 min) no idioma do país -> generate_short_from_text ->
  YouTube (canal próprio do país).

Categorias (mesmas 4 do Brasil, traduzidas por locale em config.LOCALES):
Política, Entretenimento, Mercado Financeiro, Policial.

Uso:
    python international_news.py --locale japao
    python international_news.py --locale india --sem-upload
    python international_news.py --locale franca --privado
"""
from __future__ import annotations

import argparse
import asyncio
import logging
from logging.handlers import RotatingFileHandler
import os
import re
import sys
import time
from datetime import datetime
from dotenv import load_dotenv
from config import GROQ_MODEL, GEMINI_MODEL

os.chdir(os.path.dirname(os.path.abspath(__file__)))
load_dotenv()


# -- Logging -------------------------------------------------------------------
# Um arquivo de log por locale (logs/international_<locale>.log), decidido em
# tempo de execução (depende do --locale) — por isso a configuração roda via
# _init_logging(), chamada no entry point, em vez de no import do módulo.

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
            RotatingFileHandler(log_file, maxBytes=5*1024*1024, backupCount=2, encoding="utf-8"),
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


# -- Flags ---------------------------------------------------------------------

YOUTUBE_UPLOAD      = True
YOUTUBE_PUBLISH_NOW = True
MAX_CELEBS_PER_RUN  = 3   # só usado em modo --celebridades

_LANGUAGE_NAMES = {
    "en": "English",
    "ja": "Japanese",
    "fr": "French",
    "de": "German",
    "it": "Italian",
}


# -- Seleção da mais relevante por categoria (Groq → Gemini → fallback) -----------

def _select_most_relevant_intl(
    category_label: str,
    candidates: list[dict],
    trending: dict | None,
) -> dict:
    """
    Equivalente em inglês do summarizer.select_most_relevant() — mesmos
    critérios de priorização, sem assumir Brasil/Twitter BR.
    """
    if len(candidates) == 1:
        return candidates[0]

    groq_key   = os.getenv("GROQ_API_KEY", "")
    gemini_key = os.getenv("GEMINI_API_KEY", "")

    titles_block = "\n".join(
        f"{i+1}. [{item.get('source', '?')}] {item['title']}"
        for i, item in enumerate(candidates)
    )

    trending_ctx = ""
    if trending:
        tw  = trending.get("twitter", [])[:15]
        gg  = trending.get("google",  [])[:10]
        kws = trending.get("keywords", [])[:20]
        parts = []
        if tw:
            parts.append(f"Twitter trending: {', '.join(tw)}")
        if gg:
            parts.append(f"Google trending: {', '.join(gg)}")
        if kws:
            parts.append(f"Trending keywords: {', '.join(kws)}")
        if parts:
            trending_ctx = (
                "\n\nCONTEXT — what's trending on social media right now:\n"
                + "\n".join(parts)
                + "\n\nUse this context to PRIORITIZE news whose subject matches "
                "what's being discussed. If relevance is tied, prefer the trending one."
            )

    prompt = (
        f"You are a news editor. From the list below of news in the "
        f"'{category_label}' category, choose the MOST RELEVANT and IMPORTANT "
        f"one for the general public today.\n\n"
        f"Criteria (in priority order):\n"
        f"1. Subject trending on social media (see context below)\n"
        f"2. Direct impact on people's lives\n"
        f"3. Genuine novelty (breaking news, not a rehash of an old topic)\n"
        f"4. National/broad scope (not purely local)\n"
        f"5. Genuine journalistic interest (not clickbait)\n"
        f"{trending_ctx}\n\n"
        f"News:\n{titles_block}\n\n"
        f"Reply with ONLY the number of the chosen news item (e.g. 3). "
        f"No explanation, no extra text."
    )

    def _parse_index(text: str) -> int | None:
        text = text.strip()
        m = re.search(r"\b([1-9]\d*)\b", text)
        if m:
            idx = int(m.group(1))
            if 1 <= idx <= len(candidates):
                return idx - 1
        return None

    if groq_key and groq_key not in ("", "cole_sua_chave_aqui"):
        try:
            from groq import Groq
            client = Groq(api_key=groq_key)
            resp = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                max_tokens=10,
            )
            idx = _parse_index(resp.choices[0].message.content)
            if idx is not None:
                chosen = candidates[idx]
                print(f"  [Groq] Most relevant in '{category_label}': [{idx+1}] {chosen['title'][:60]}")
                return chosen
        except Exception as e:
            print(f"  [select_most_relevant_intl] Groq falhou: {e}. Tentando Gemini...")

    if gemini_key and gemini_key not in ("", "cole_sua_chave_aqui"):
        try:
            from google import genai as google_genai
            client = google_genai.Client(api_key=gemini_key)
            response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
            idx = _parse_index(response.text)
            if idx is not None:
                chosen = candidates[idx]
                print(f"  [Gemini] Most relevant in '{category_label}': [{idx+1}] {chosen['title'][:60]}")
                return chosen
        except Exception as e:
            print(f"  [select_most_relevant_intl] Gemini falhou: {e}.")

    print(f"  [select_most_relevant_intl] LLM indisponível — usando primeira candidata de '{category_label}'")
    return candidates[0]


# -- Narração densa (~3 min) no idioma do locale (Groq → Gemini) -----------------

def _generate_dense_narration(title: str, content: str, category_label: str, locale: dict) -> str | None:
    """
    Gera narração densa (~3 min de fala) NO IDIOMA do locale — mesmo formato
    do main.py (summarize_news_for_short), com prompt em inglês instruindo
    o idioma alvo (evita manter 5 variantes de prompt escritas à mão).
    Cadeia: Groq (primário) → Gemini (fallback) → None.
    """
    groq_key   = os.getenv("GROQ_API_KEY", "")
    gemini_key = os.getenv("GEMINI_API_KEY", "")

    lang_name = _LANGUAGE_NAMES.get(locale["language"], locale["language"])

    # Japonês não se mede bem por contagem de palavras (não usa espaços).
    if locale["language"] == "ja":
        length_rule = "- Length: approximately 500-650 Japanese characters (not word count)."
    else:
        length_rule = "- Between 320 and 380 words."

    prompt = (
        "You are a professional news anchor recording a voiceover for a YouTube Short.\n\n"
        f"Category: {category_label}\n"
        f"Headline: {title}\n"
        f"Source content (use as factual basis):\n{content[:3500]}\n\n"
        "MANDATORY RULES:\n"
        f"- Write the ENTIRE response in {lang_name}. Do not use any other language, "
        "not even for a single word.\n"
        "- Do NOT read the headline verbatim. Start directly with the most impactful fact.\n"
        f"{length_rule}\n"
        "- CONTEXT: the listener has read nothing about this — be self-contained: "
        "who is involved, what happened, and why it matters.\n"
        "- COHERENCE: pick ONE throughline and follow it start to finish, no topic jumps. "
        "Each sentence should flow naturally from the previous one, like a story with "
        "a beginning, middle, and end.\n"
        "- Structure: hook (1 sentence) → who/what (1-2 sentences) → why it matters "
        "(1-2 sentences) → closing reflection on impact or expected developments.\n"
        "- Natural, clear spoken tone, like a professional news narrator — not a formal report.\n"
        "- Do NOT use markdown, asterisks, hashtags, symbols, or lists.\n"
        "- Do NOT invent facts beyond what is in the source content.\n"
        "- Do NOT include an intro or subscribe call-to-action — those are added separately.\n\n"
        f"Reply with ONLY the narration text in {lang_name}, no title, no extra formatting."
    )

    if groq_key and groq_key not in ("", "cole_sua_chave_aqui"):
        try:
            from groq import Groq
            client = Groq(api_key=groq_key)
            resp = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
            )
            text = resp.choices[0].message.content.strip()
            if text:
                print(f"  [Groq] narração densa gerada ({lang_name})")
                return text
        except Exception as e:
            print(f"  Groq falhou: {e}. Tentando Gemini...")

    if gemini_key and gemini_key not in ("", "cole_sua_chave_aqui"):
        try:
            from google import genai as google_genai
            client = google_genai.Client(api_key=gemini_key)
            response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
            text = response.text.strip()
            if text:
                print(f"  [Gemini] narração densa gerada ({lang_name})")
                return text
        except Exception as e:
            print(f"  Gemini também falhou: {e}")

    print("  ❌ Nenhum LLM disponível para gerar narração internacional.")
    return None


# -- Narração de celebridade com classificação grave/leve (Groq → Gemini) --------

def _generate_celebrity_narration(
    title: str, content: str, category_label: str, locale: dict
) -> tuple[str, bool] | None:
    """
    Gera narração de celebridade (~110-125 palavras) NO IDIOMA do locale, com
    classificação de tom grave/leve — mesmo critério do celebridades.py (BR):
    evita soar como fofoca animada em cima de uma notícia de morte/tragédia.
    Cadeia: Groq (primário) → Gemini (fallback) → None.

    Retorna (narração, is_grave) ou None se nenhum LLM funcionar.
    """
    groq_key   = os.getenv("GROQ_API_KEY", "")
    gemini_key = os.getenv("GEMINI_API_KEY", "")

    lang_name = _LANGUAGE_NAMES.get(locale["language"], locale["language"])

    if locale["language"] == "ja":
        length_rule = "- Length: approximately 150-200 Japanese characters (not word count)."
    else:
        length_rule = "- Between 100 and 120 words."

    prompt = (
        "You are an entertainment show host recording a voiceover for a YouTube "
        "Short about a celebrity news story.\n\n"
        f"Headline: {title}\n"
        f"Source content (use as factual basis):\n{content[:3000]}\n\n"
        "MANDATORY RULES:\n\n"
        "1. FIRST LINE of your reply: classify the story.\n"
        "   Format: TONE: grave  OR  TONE: light\n"
        "   - 'grave': death, serious illness, accident, grief, serious hospitalization, "
        "tragedy, violence, mental health crisis, or any sad/serious fact.\n"
        "   - 'light': dating, feud, look, fame, silly controversy, success, ordinary gossip.\n\n"
        "2. THEN write the narration:\n"
        f"- Write the ENTIRE narration in {lang_name}. Do not use any other language.\n"
        "- Do NOT read the headline verbatim. Start directly with the most relevant fact.\n"
        f"{length_rule}\n"
        "- CONTEXT: the listener knows nothing — say who the person is, what happened, "
        "and why it matters.\n"
        "- COHERENCE: pick ONE throughline and follow it start to finish.\n"
        "- IF TONE = light: upbeat, playful, like gossip between friends, but never defamatory. "
        "Close with a light comment inviting the viewer to share their opinion in the comments.\n"
        "- IF TONE = grave: respectful, sober, empathetic — like a journalist delivering sad "
        "news. NO excitement, NO gossip-style language, NO exclamation marks. Close with a "
        "respectful/sympathetic line, NOT a playful comment-invitation.\n"
        "- Do NOT use markdown, asterisks, hashtags, symbols, or lists.\n"
        "- Do NOT invent facts beyond the source content.\n"
        "- Do NOT include a subscribe call-to-action — it will be added separately.\n\n"
        f"Reply with only the TONE line followed by the narration in {lang_name}, "
        "no title, no extra formatting."
    )

    def _parse(text: str) -> tuple[str, bool]:
        lines = text.strip().splitlines()
        is_grave = False
        start = 0
        if lines and lines[0].upper().startswith("TONE:"):
            is_grave = "grave" in lines[0].lower()
            start = 1
            while start < len(lines) and not lines[start].strip():
                start += 1
        narration = "\n".join(lines[start:]).strip()
        return narration, is_grave

    if groq_key and groq_key not in ("", "cole_sua_chave_aqui"):
        try:
            from groq import Groq
            client = Groq(api_key=groq_key)
            resp = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.8,
            )
            text = resp.choices[0].message.content.strip()
            if text:
                narration, is_grave = _parse(text)
                print(f"  [Groq] narração de celebridade gerada ({lang_name}, tom={'grave' if is_grave else 'light'})")
                return narration, is_grave
        except Exception as e:
            print(f"  Groq falhou: {e}. Tentando Gemini...")

    if gemini_key and gemini_key not in ("", "cole_sua_chave_aqui"):
        try:
            from google import genai as google_genai
            client = google_genai.Client(api_key=gemini_key)
            response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
            text = response.text.strip()
            if text:
                narration, is_grave = _parse(text)
                print(f"  [Gemini] narração de celebridade gerada ({lang_name}, tom={'grave' if is_grave else 'light'})")
                return narration, is_grave
        except Exception as e:
            print(f"  Gemini também falhou: {e}")

    print("  ❌ Nenhum LLM disponível para gerar narração de celebridade internacional.")
    return None


# -- Pipeline principal --------------------------------------------------------------

async def run_international(
    locale_key: str, on_progress=None, celebridades: bool = False
) -> list[tuple[str, str | None]]:
    """
    Pipeline de Notícias Internacionais pra um locale — mesmo fluxo do main.py:
    1 Short por categoria (Política/Entretenimento/Mercado Financeiro/Policial,
    traduzidas), com seleção por relevância usando trending topics do próprio país.

    celebridades=True muda pro modo Celebridades (espelha celebridades.py): uma
    única categoria de fofoca/entretenimento, até MAX_CELEBS_PER_RUN Shorts por
    execução (não 1), narração com classificação de tom grave/leve, playlist e
    voz próprias — mas o MESMO canal/token do país (não precisa canal novo).

    Retorna lista de (categoria_universal, video_id | None).
    """
    from config import LOCALES, DRIVE_SYNC_DIR
    from fetcher import fetch_query_by_locale, extract_article_content, select_unique_news
    from history import filter_not_posted, mark_as_posted
    from trends import get_trending_topics

    locale = LOCALES[locale_key]

    if celebridades:
        categories = {"Celebridades": locale["celebrity_category"]}
        fetch_limit = 15
        mode_label = f"{locale['label']} — Celebridades"
    else:
        categories = locale["categories"]  # {"Política": "Politics", ...}
        fetch_limit = 5
        mode_label = locale["label"]

    pipeline_start = time.time()

    def _elapsed():
        m, s = divmod(int(time.time() - pipeline_start), 60)
        return f"[T+{m:02d}:{s:02d}]"

    print(f"--- International News ({mode_label}): {datetime.now().strftime('%d/%m/%Y %H:%M:%S')} ---")
    print(f"Categorias: {', '.join(categories.values())}")

    privacy = "public" if YOUTUBE_PUBLISH_NOW else "private"

    # ---------- Fase 1: Fetch por categoria ----------
    print(f"\n{_elapsed()} [FASE 1] Buscando notícias das categorias selecionadas ({mode_label})...")
    phase_start = time.time()
    raw_news = []
    for cat_universal, cat_local in categories.items():
        items = fetch_query_by_locale(cat_local, locale["hl"], locale["gl"], locale["ceid"], limit=fetch_limit)
        for item in items:
            item["category"] = cat_universal  # normaliza pra chave universal (dedup/agrupamento)
        raw_news.extend(items)
    print(f"{_elapsed()} [FASE 1] OK — {len(raw_news)} candidatos em {int(time.time()-phase_start)}s")

    if not raw_news:
        print("Nenhuma notícia encontrada. Abortando.")
        try:
            from telegram_notifier import notify
            notify(f"❌ <b>International News ({mode_label}):</b> nenhuma notícia encontrada.")
        except Exception:
            pass
        return []

    # ---------- Fase 1.5: Trending topics do país ----------
    print(f"\n{_elapsed()} [FASE 1.5] Coletando trending topics ({mode_label})...")
    trending = None
    try:
        trending = get_trending_topics(
            use_cache=True,
            twitter_country=locale["twitter_country"],
            google_country=locale["google_country"],
            google_hl=locale["hl"],
            youtube_region=locale["youtube_region"],
            cache_key=locale_key,
            include_most_read=False,
        )
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

    items_unicos, n_skip = filter_not_posted(items_unicos)
    if n_skip:
        print(f"{_elapsed()} [FASE 2] {n_skip} item(s) ignorado(s) — já postados nas últimas 48h")

    pool_por_categoria: dict[str, list] = {}
    for item in items_unicos:
        cat = item.get("category", "")
        if cat in categories:
            pool_por_categoria.setdefault(cat, []).append(item)

    items_selecionados = []
    if celebridades:
        # Uma única categoria, mas até MAX_CELEBS_PER_RUN itens — seleção
        # sequencial (escolhe a mais relevante, remove do pool, repete).
        cat_local = categories["Celebridades"]
        remaining = list(pool_por_categoria.get("Celebridades", []))
        print(f"\n  → '{cat_local}': {len(remaining)} candidatos — selecionando até {MAX_CELEBS_PER_RUN} mais relevantes...")
        for _ in range(min(MAX_CELEBS_PER_RUN, len(remaining))):
            if not remaining:
                break
            escolhido = _select_most_relevant_intl(cat_local, remaining, trending)
            items_selecionados.append(escolhido)
            remaining = [c for c in remaining if c["link"] != escolhido["link"]]
        print(f"\n{_elapsed()} [FASE 2] {len(items_selecionados)} notícia(s) de celebridade selecionada(s)")
    else:
        for cat_universal in categories:
            candidatos = pool_por_categoria.get(cat_universal, [])
            if not candidatos:
                print(f"  ⚠️  Sem candidatos para '{categories[cat_universal]}'")
                continue
            cat_local = categories[cat_universal]
            print(f"\n  → '{cat_local}': {len(candidatos)} candidatos — selecionando mais relevante...")
            escolhido = _select_most_relevant_intl(cat_local, candidatos, trending)
            items_selecionados.append(escolhido)
        print(f"\n{_elapsed()} [FASE 2] {len(items_selecionados)}/{len(categories)} categorias com notícia selecionada")

    if not items_selecionados:
        print("Nenhuma categoria teve notícia válida. Abortando.")
        try:
            from telegram_notifier import notify
            notify(f"❌ <b>International News ({mode_label}):</b> nenhuma categoria teve notícia válida.")
        except Exception:
            pass
        return []

    # ---------- Fase 3: Extrai conteúdo + narração densa ----------
    print(f"\n{_elapsed()} [FASE 3] Extraindo conteúdo e gerando narrações densas...")
    phase_start = time.time()

    for item in items_selecionados:
        content = extract_article_content(item["link"])
        item["_content"] = content if content else item.get("summary", "")

    items_com_narracao = []
    for item in items_selecionados:
        cat_universal = item["category"]
        cat_local = categories[cat_universal]
        print(f"\n  → {cat_local}: resumindo...")
        if celebridades:
            result = _generate_celebrity_narration(item["title"], item["_content"], cat_local, locale)
            if result:
                narracao, is_grave = result
                item["narracao"] = narracao
                item["is_grave"] = is_grave
                items_com_narracao.append(item)
            else:
                print(f"  ⚠️ {cat_local}: sem narração (LLMs falharam) — pulando")
        else:
            narracao = _generate_dense_narration(item["title"], item["_content"], cat_local, locale)
            if narracao:
                item["narracao"] = narracao
                items_com_narracao.append(item)
            else:
                print(f"  ⚠️ {cat_local}: sem narração (LLMs falharam) — pulando")

    print(f"\n{_elapsed()} [FASE 3] OK em {int(time.time()-phase_start)}s — {len(items_com_narracao)} narrações geradas")

    if not items_com_narracao:
        msg = f"❌ <b>International News ({mode_label}):</b> pipeline abortado — nenhuma narração válida."
        print(msg)
        try:
            from telegram_notifier import notify
            notify(msg)
        except Exception:
            pass
        return []

    # Salva roteiro consolidado no Drive (rastreabilidade — mesmo padrão do main.py)
    os.makedirs(DRIVE_SYNC_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    date_str = datetime.now().strftime("%d/%m/%Y")
    file_suffix = f"{locale_key}_celeb" if celebridades else locale_key
    md_path = os.path.join(DRIVE_SYNC_DIR, f"International_{file_suffix}_{timestamp}.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# International News ({mode_label}) — {date_str}\n\n")
        for i, item in enumerate(items_com_narracao, 1):
            cat_local = categories[item["category"]]
            f.write(f"## {i}. [{cat_local}] {item['title']}\n\n")
            f.write(f"{item['narracao']}\n\n")
            f.write(f"Fonte: {item.get('source', '')}\nLink: {item.get('link', '')}\n\n---\n\n")
    print(f"Roteiro salvo: {md_path}")

    # ---------- Fase 4: Gera 1 Short por categoria ----------
    print(f"\n{_elapsed()} [FASE 4] Gerando {len(items_com_narracao)} Shorts...")
    phase_start = time.time()

    from shorts import generate_short_from_text

    resultados: list[tuple[str, str | None]] = []
    for i, item in enumerate(items_com_narracao, 1):
        cat_universal = item["category"]
        cat_local = categories[cat_universal]
        print(f"\n  ── Short {i}/{len(items_com_narracao)} — {cat_local} ({locale['label']}) ──")

        if celebridades:
            cta = locale["celebrity_cta_grave"] if item.get("is_grave") else locale["celebrity_cta"]
            narracao_final = item["narracao"].rstrip() + cta  # sem "intro" — celebridades.py também não usa
            voice = locale["celebrity_voice"]
            playlist_key = locale["celebrity_playlist_key"]
            cat_hashtags = list(locale["celebrity_hashtags"])
            category_key = "Celebridades"  # cor/voz-fallback consistentes com o BR (rosa)
        else:
            narracao_final = locale["intro"] + " " + item["narracao"].rstrip() + locale["cta"]
            voice = locale["voice"]
            playlist_key = locale["playlist_key"]
            cat_hashtags = list(locale["hashtags"]) + [cat_local.replace(" ", "")]
            category_key = cat_universal

        common_kwargs = dict(
            title=item["title"],
            narration=narracao_final,
            category=category_key,           # chave universal → cor/voz corretas (mesma paleta do BR)
            category_label=cat_local,        # texto exibido na tela/tags no idioma local
            source=item.get("source", ""),
            hashtags=cat_hashtags,
            playlist_key=playlist_key,
            instagram_enabled=False,
            youtube_enabled=True,
            link=item.get("link"),
            display_text=item["narracao"],
            voice=voice,
            language=locale["language"],
            token_file=locale["token_file"],
            link_label=locale["link_label"],
            source_label=locale["source_label"],
            channel_name=locale["channel_name"],
            cjk_font=locale.get("cjk_font", False),
        )

        if not YOUTUBE_UPLOAD:
            try:
                path = await generate_short_from_text(**common_kwargs, upload=False, privacy=privacy)
                print(f"  Vídeo local: {path}")
                resultados.append((cat_universal, None))
            except Exception as e:
                print(f"  Erro: {e}")
            continue

        try:
            video_id = await generate_short_from_text(**common_kwargs, upload=True, privacy=privacy)
            if video_id:
                pipeline_name = f"international_{locale_key}_celeb" if celebridades else f"international_{locale_key}"
                mark_as_posted(item["title"], pipeline=pipeline_name)
            resultados.append((cat_universal, video_id))
        except Exception as e:
            print(f"  ❌ Erro no Short {i} ({cat_local}): {e}")
            resultados.append((cat_universal, None))

        # Espaçamento entre Shorts para não canibalizar o alcance no algoritmo
        if i < len(items_com_narracao):
            print(f"\n  ⏳ Aguardando 10 min antes do próximo Short ({i+1}/{len(items_com_narracao)})...")
            await asyncio.sleep(600)

    print(f"\n{_elapsed()} [FASE 4] OK em {int(time.time()-phase_start)}s")

    # ---------- Resumo final + notificação ----------
    total_min, total_sec = divmod(int(time.time() - pipeline_start), 60)
    print(f"\n{_elapsed()} === PIPELINE CONCLUÍDO ({mode_label}) === ({total_min}m{total_sec:02d}s totais)")

    yt_ok = sum(1 for _, vid in resultados if vid)
    print(f"  YouTube: ✅ {yt_ok}/{len(resultados)}")

    if YOUTUBE_UPLOAD:
        try:
            from telegram_notifier import notify
            linhas = [f"✅ <b>International News ({mode_label}) postado!</b> ({total_min}m{total_sec:02d}s)"]
            linhas.append(f"📺 YouTube: {yt_ok}/{len(resultados)}")
            linhas.append("")
            for cat_universal, vid in resultados:
                linha = f"• {categories.get(cat_universal, cat_universal)}: {'✅' if vid else '❌'}"
                if vid:
                    linha += f" — https://youtu.be/{vid}"
                linhas.append(linha)
            notify("\n".join(linhas))
        except Exception:
            pass

    return resultados


# -- Entry point -----------------------------------------------------------------------

if __name__ == "__main__":
    from config import LOCALES

    parser = argparse.ArgumentParser(prog="international_news.py", add_help=True)
    parser.add_argument("--locale", required=True, choices=list(LOCALES.keys()), help="país/locale a rodar")
    parser.add_argument("--celebridades", action="store_true", help="modo Celebridades em vez das 4 categorias de notícia")
    parser.add_argument("--sem-upload", action="store_true", help="só gera, sem upload")
    parser.add_argument("--privado",    action="store_true", help="publica como privado")
    args, _ = parser.parse_known_args()

    log_suffix = f"{args.locale}_celeb" if args.celebridades else args.locale
    _init_logging(log_suffix)

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
            try:
                from telegram_notifier import notify
                notify(f"⚠️ <b>Internacional ({args.locale}):</b> token do YouTube expirado/inválido — pipeline abortado.\n{msg}")
            except Exception:
                pass
            sys.exit(1)

    asyncio.run(run_international(args.locale, celebridades=args.celebridades))
