"""
Pipeline de Tech Shorts — produz APENAS Shorts (sem vídeo longo).

Fluxo: NotebookLM (10 sites tech) -> top N tópicos -> 1 Short por tópico -> YouTube + TikTok

Uso:
    python tech_news.py
    python tech_news.py --sem-upload
    python tech_news.py --privado
"""
import argparse
import asyncio
import json
import logging
import os
import re
import shutil
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

from config import DRIVE_SYNC_DIR, AUDIO_OUTPUT_DIR, CHANNEL_NAME

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
# Tech News agora produz APENAS Shorts (sem vídeo longo).
# Quantos Shorts gerar por ciclo (top N notícias do NotebookLM):
MAX_TECH_SHORTS_PER_RUN = 5


# -- Sites de tecnologia ------------------------------------------------------

TECH_SITES = [
    "https://www.tecmundo.com.br",
    "https://tecnoblog.net",
    "https://olhardigital.com.br",
    "https://canaltech.com.br",
    "https://www.tudocelular.com",
    "https://gizmodo.uol.com.br",
    "https://www.theverge.com",
    "https://techcrunch.com",
    "https://arstechnica.com",
    "https://www.wired.com",
]


# -- NotebookLM: busca e estrutura as noticias --------------------------------

_NOTEBOOKLM_STORAGE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "credentials", "notebooklm_storage_state.json"
)


def _notify_session_expired():
    """Envia notificacao Telegram quando a sessao NotebookLM expira."""
    try:
        from telegram_notifier import notify
        notify(
            "🔑 <b>Sessao NotebookLM expirada!</b>\n\n"
            "Auto-refresh falhou.\n"
            "1. Rode <code>notebooklm login</code> no PC\n"
            "2. Envie o <code>storage_state.json</code> aqui neste chat"
        )
    except Exception:
        pass


async def fetch_tech_news_notebooklm(on_progress=None):
    """
    Usa NotebookLM para buscar e estruturar noticias de tecnologia.
    Retorna lista de dicts: [{title, summary, source, category}]
    """
    from notebooklm import NotebookLMClient

    date_str = datetime.now().strftime("%d/%m/%Y")

    async def _progress(msg: str):
        print(msg)
        if on_progress:
            await on_progress(msg)

    # Tenta credentials/ local, fallback para path padrão do sistema
    storage_path = _NOTEBOOKLM_STORAGE if os.path.exists(_NOTEBOOKLM_STORAGE) else None
    try:
        client = await NotebookLMClient.from_storage(path=storage_path)
    except (FileNotFoundError, ValueError) as e:
        await _progress(f"Sessao expirada: {e}")
        await _progress("Tentando auto-refresh...")
        # Tenta renovar via browser profile persistente
        try:
            from notebooklm_session import refresh
            if refresh(verbose=True):
                await _progress("Sessao renovada! Reconectando...")
                storage_path = _NOTEBOOKLM_STORAGE if os.path.exists(_NOTEBOOKLM_STORAGE) else None
                client = await NotebookLMClient.from_storage(path=storage_path)
            else:
                print("Auto-refresh falhou. Execute: notebooklm login")
                _notify_session_expired()
                return None
        except Exception as refresh_err:
            print(f"Erro no auto-refresh: {refresh_err}")
            print("Execute: notebooklm login")
            _notify_session_expired()
            return None

    async with client:
        notebook = await client.notebooks.create(title=f"Tech News {date_str}")
        notebook_id = notebook.id
        await _progress("Notebook criado para Tech News")

        try:
            await _progress(f"Adicionando {len(TECH_SITES)} sites de tecnologia...")
            sources = []
            for i, url in enumerate(TECH_SITES, 1):
                domain = url.split("//")[1].split("/")[0]
                try:
                    source = await client.sources.add_url(notebook_id, url)
                    sources.append(source)
                    await _progress(f"  [{i}/{len(TECH_SITES)}] {domain}")
                except Exception:
                    await _progress(f"  [{i}/{len(TECH_SITES)}] {domain} (falhou)")

            if not sources:
                return None

            await _progress(f"Aguardando processamento de {len(sources)} fontes...")
            await client.sources.wait_for_sources(
                notebook_id,
                [s.id for s in sources],
                timeout=180.0,
            )
            await _progress("Fontes processadas!")

            # Pedir noticias em formato estruturado (JSON)
            await _progress("Gerando noticias estruturadas...")
            prompt = (
                f"Com base em todas as fontes, liste as principais noticias de "
                f"tecnologia de hoje ({date_str}).\n\n"
                f"Responda EXCLUSIVAMENTE em JSON valido, sem markdown, sem ```json. "
                f"O formato deve ser uma lista de objetos:\n"
                f'[{{"title": "titulo curto", "summary": "resumo de 2-3 frases", '
                f'"source": "nome do site fonte"}}]\n\n'
                f"Liste entre 8 e 12 noticias, ordenadas por relevancia/importancia. "
                f"Resumos em portugues do Brasil. Cada resumo deve ter no maximo 150 palavras."
            )
            result = await client.chat.ask(notebook_id, prompt)
            await _progress("Resposta recebida do NotebookLM!")

            # Parse do JSON
            items = _parse_news_json(result.answer)
            if items:
                await _progress(f"{len(items)} noticias extraidas com sucesso.")
            else:
                await _progress("Falha ao parsear JSON. Tentando formato livre...")
                items = _parse_news_freetext(result.answer)
                if items:
                    await _progress(f"{len(items)} noticias extraidas (formato livre).")

            return items

        finally:
            try:
                await client.notebooks.delete(notebook_id)
                await _progress("Notebook deletado.")
            except Exception:
                pass


def _parse_news_json(text: str):
    """Tenta extrair lista de noticias de um JSON."""
    # Limpar possivel markdown
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

    try:
        data = json.loads(text)
        if isinstance(data, list):
            items = []
            for entry in data:
                if isinstance(entry, dict) and "title" in entry:
                    items.append({
                        "title": entry.get("title", ""),
                        "ai_summary": entry.get("summary", entry.get("title", "")),
                        "source": entry.get("source", "Tech"),
                        "category": "Tecnologia",
                        "link": "",
                    })
            return items if items else None
    except (json.JSONDecodeError, ValueError):
        pass

    # Tentar encontrar array JSON dentro do texto
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group())
            if isinstance(data, list):
                items = []
                for entry in data:
                    if isinstance(entry, dict) and "title" in entry:
                        items.append({
                            "title": entry.get("title", ""),
                            "ai_summary": entry.get("summary", entry.get("title", "")),
                            "source": entry.get("source", "Tech"),
                            "category": "Tecnologia",
                            "link": "",
                        })
                return items if items else None
        except (json.JSONDecodeError, ValueError):
            pass

    return None


def _parse_news_freetext(text: str):
    """Fallback: extrai noticias de texto livre (linhas com titulo/resumo)."""
    items = []
    # Tenta detectar padrao "N. Titulo\nResumo" ou "- Titulo: Resumo"
    # Padrao 1: numerado
    blocks = re.split(r"\n(?=\d+[\.\)]\s)", text)
    if len(blocks) >= 2:
        for block in blocks:
            block = block.strip()
            if not block:
                continue
            lines = block.split("\n", 1)
            title = re.sub(r"^\d+[\.\)]\s*", "", lines[0]).strip()
            summary = lines[1].strip() if len(lines) > 1 else title
            if title:
                items.append({
                    "title": title,
                    "ai_summary": summary,
                    "source": "Tech",
                    "category": "Tecnologia",
                    "link": "",
                })
        return items if items else None

    # Padrao 2: bullets
    for line in text.split("\n"):
        line = line.strip()
        if line.startswith(("-", "*", "•")):
            content = line.lstrip("-*• ").strip()
            if ":" in content:
                title, summary = content.split(":", 1)
                items.append({
                    "title": title.strip(),
                    "ai_summary": summary.strip(),
                    "source": "Tech",
                    "category": "Tecnologia",
                    "link": "",
                })
            elif content:
                items.append({
                    "title": content,
                    "ai_summary": content,
                    "source": "Tech",
                    "category": "Tecnologia",
                    "link": "",
                })

    return items if items else None


# -- Pipeline principal --------------------------------------------------------

async def run_tech_news(on_progress=None):
    """
    Pipeline Tech News — produz APENAS Shorts (sem vídeo longo).
    Fluxo: NotebookLM busca top N tópicos -> 1 Short por tópico -> YouTube + TikTok.
    """
    print(f"--- Tech Shorts Pipeline: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')} ---")

    privacy = "public" if YOUTUBE_PUBLISH_NOW else "private"
    date_str = datetime.now().strftime("%d/%m/%Y")

    # 1. Buscar notícias via NotebookLM
    print("\n[1/2] Buscando notícias de tecnologia via NotebookLM...")
    items = await fetch_tech_news_notebooklm(on_progress=on_progress)

    if not items:
        print("Nenhuma notícia obtida. Abortando.")
        return None

    # Top N pra virar Shorts (ordem de relevância do NotebookLM)
    items = items[:MAX_TECH_SHORTS_PER_RUN]
    print(f"\n{len(items)} Shorts de tecnologia serão gerados.")

    # Salva roteiro consolidado no Drive (rastreabilidade)
    os.makedirs(DRIVE_SYNC_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    filename_base = f"Tech_Shorts_{timestamp}"
    md_path = os.path.join(DRIVE_SYNC_DIR, f"{filename_base}.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# Tech Shorts - {date_str}\n\n")
        for i, item in enumerate(items, 1):
            f.write(f"## {i}. {item.get('title', '')}\n\n")
            f.write(f"{item.get('ai_summary', '')}\n\n")
            f.write(f"Fonte: {item.get('source', '')}\n\n---\n\n")
    print(f"Roteiro salvo: {md_path}")

    # 2. Gerar e enviar 1 Short por notícia
    print(f"\n[2/2] Gerando {len(items)} Shorts (YouTube + TikTok)...")
    from shorts import generate_short_from_text

    uploaded_ids = []
    tech_hashtags = ["Shorts", "Tecnologia", "Tech", "IA", "Inovação"]

    for i, item in enumerate(items, 1):
        print(f"\n  ── Short {i}/{len(items)} ──")
        title = item.get("title", "")
        narration = item.get("ai_summary") or title
        source = item.get("source", "Tech")

        if not YOUTUBE_UPLOAD:
            try:
                path = await generate_short_from_text(
                    title=title, narration=narration,
                    category="Tecnologia", source=source,
                    upload=False, privacy=privacy,
                    hashtags=tech_hashtags, playlist_key="tech",
                    instagram_enabled=False,
                )
                print(f"  Vídeo local: {path}")
            except Exception as e:
                print(f"  Erro: {e}")
            continue

        try:
            video_id = await generate_short_from_text(
                title=title, narration=narration,
                category="Tecnologia", source=source,
                upload=True, privacy=privacy,
                hashtags=tech_hashtags, playlist_key="tech",
                instagram_enabled=False,
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


def _sanitize_yt(text: str) -> str:
    """Remove caracteres que o YouTube rejeita na descrição."""
    import re
    text = re.sub(r"<[^>]*>", "", text)
    return text.replace("<", "").replace(">", "").strip()


def _build_tech_description(items, date_str):
    """Monta descricao do video com lista de noticias."""
    lines = [
        f"Resumo diario das principais noticias de tecnologia - {date_str}",
        "",
        "Noticias de hoje:",
    ]
    for i, item in enumerate(items, 1):
        title = _sanitize_yt(item.get("title", ""))
        source = _sanitize_yt(item.get("source", ""))
        lines.append(f"{i}. {title} ({source})")

    lines.extend([
        "",
        "Fontes: TecMundo, Tecnoblog, Olhar Digital, Canaltech, TudoCelular, "
        "Gizmodo, The Verge, TechCrunch, Ars Technica, Wired",
        "",
        "#tecnologia #tech #noticias #inovacao #IA #programacao",
    ])
    desc = "\n".join(lines)
    if len(desc) > 4900:
        desc = desc[:4900] + "\n..."
    return desc


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
