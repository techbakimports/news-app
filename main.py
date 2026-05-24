import argparse
import asyncio
import logging
import os
import shutil
import time
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

from fetcher import fetch_latest_news, extract_article_content, select_unique_news
from summarizer import summarize_news_batch
try:
    from notebooklm_summarizer import summarize_news_notebooklm
    _NOTEBOOKLM_AVAILABLE = True
except ImportError:
    _NOTEBOOKLM_AVAILABLE = False
from audio import generate_audio_segments
from video import generate_video
from uploader import upload_video, build_description, upload_thumbnail
from playlists import add_to_playlist
from config import DRIVE_SYNC_DIR, AUDIO_OUTPUT_DIR, CHANNEL_NAME

# Logging para arquivo — permite monitorar com: tail -f logs/noticias.log
_LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
os.makedirs(_LOG_DIR, exist_ok=True)
_log_file = os.path.join(_LOG_DIR, "noticias.log")
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

# True  → NotebookLM gera os resumos (sem limite de API)
# False → Gemini gera os resumos (limite: 20 req/dia no plano free)
USE_NOTEBOOKLM_SUMMARIZER = False

# Define True para publicar no YouTube após gerar o vídeo.
YOUTUBE_UPLOAD = True

# True  → publica imediatamente como público
# False → agenda para YOUTUBE_PUBLISH_HOUR (fica privado até lá)
YOUTUBE_PUBLISH_NOW = True

YOUTUBE_PUBLISH_HOUR = 5   # usado apenas quando YOUTUBE_PUBLISH_NOW = False

# True → gera e sobe um Short (primeiros 55s do vídeo convertido para vertical)
YOUTUBE_GENERATE_SHORT = True

# Configuração de Limpeza (apagar arquivos mais antigos que X horas)
CLEANUP_HOURS = 24

def cleanup_old_files():
    """Apaga arquivos antigos no Drive e na pasta de áudio."""
    now = time.time()
    cutoff = now - (CLEANUP_HOURS * 3600)
    
    for folder in [DRIVE_SYNC_DIR, AUDIO_OUTPUT_DIR]:
        if not os.path.exists(folder): continue
        for f in os.listdir(folder):
            path = os.path.join(folder, f)
            if os.path.getmtime(path) < cutoff:
                try:
                    os.remove(path)
                    print(f"Arquivo antigo removido: {f}")
                except Exception as e:
                    print(f"Erro ao remover {f}: {e}")

async def run_news_cycle():
    print(f"--- Iniciando Ciclo Consolidado: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')} ---")
    cleanup_old_files()

    # 1. Buscar notícias (3 candidatos por fonte/categoria para permitir dedup por título)
    raw_news = fetch_latest_news(limit=3)

    # Deduplicação por similaridade de título — evita repetir a mesma história
    # em duas fontes diferentes da mesma categoria
    print("\nSelecionando notícias únicas por categoria/fonte...")
    items_to_process = select_unique_news(raw_news)

    consolidated_script = ""
    total_words = 0
    max_words = 2200  # Aproximadamente 15 minutos de áudio

    print(f"\nTotal de notícias únicas selecionadas: {len(items_to_process)}")

    if not os.path.exists(DRIVE_SYNC_DIR): os.makedirs(DRIVE_SYNC_DIR)

    # Fase 1: resumos via NotebookLM (sem limite de API) ou Gemini (fallback)
    summaries = None
    if USE_NOTEBOOKLM_SUMMARIZER and _NOTEBOOKLM_AVAILABLE:
        summaries = await summarize_news_notebooklm(items_to_process)
        if summaries is None:
            print("NotebookLM falhou — usando Gemini como fallback.")
    elif USE_NOTEBOOKLM_SUMMARIZER and not _NOTEBOOKLM_AVAILABLE:
        print("notebooklm-py não instalado — usando Gemini como fallback.")

    if summaries is None:
        # Fallback: Gemini em lotes de 5 (limite: 20 req/dia)
        print("\nExtraindo conteúdo dos artigos para o Gemini...")
        for item in items_to_process:
            content = extract_article_content(item["link"])
            item["_content"] = content if content else item.get("summary", "")

        BATCH_SIZE = 5
        summaries = []
        total_batches = (len(items_to_process) + BATCH_SIZE - 1) // BATCH_SIZE
        print(f"Gerando resumos em {total_batches} lote(s) de até {BATCH_SIZE} notícias...")
        for batch_idx, batch_start in enumerate(range(0, len(items_to_process), BATCH_SIZE)):
            batch = items_to_process[batch_start:batch_start + BATCH_SIZE]
            batch_input = [
                {"category": i["category"], "title": i["title"], "content": i["_content"]}
                for i in batch
            ]
            print(f"[Lote {batch_idx + 1}/{total_batches}] {len(batch)} notícias...")
            summaries.extend(summarize_news_batch(batch_input))
            if batch_start + BATCH_SIZE < len(items_to_process):
                await asyncio.sleep(15)

    # Montar roteiro consolidado respeitando o limite de ~15 min
    processed_items = []
    for item, summary in zip(items_to_process, summaries):
        if total_words > max_words:
            print("Limite de 15 minutos atingido. Parando por aqui.")
            break
        if summary:
            item["ai_summary"] = summary
            consolidated_script += f"{summary}\n\n"
            total_words += len(summary.split())
        else:
            item["ai_summary"] = None
            consolidated_script += f"{item['title']} — Fonte: {item['source']}\n\n"
            total_words += len(item["title"].split()) + 2
        processed_items.append(item)

    items_to_process = processed_items
        
    # 5. Salvar Texto para NotebookLM
    timestamp = datetime.now().strftime('%Y%m%d_%H%M')
    filename_base = f"Resumo_Completo_{timestamp}"

    md_path = os.path.join(DRIVE_SYNC_DIR, f"{filename_base}.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# Resumo de Notícias - {datetime.now().strftime('%d/%m/%Y')}\n\n")
        f.write(consolidated_script)

    # 6. Gerar áudio por segmento com Edge TTS (sync exato por notícia)
    intro_text = "Bem-vindo ao seu resumo de notícias automatizado."
    outro_text = (
        "Essas foram as principais notícias de hoje. "
        "Para ler cada notícia completa, acesse os links na descrição do vídeo. "
        "Se gostou, se inscreva no canal e ative o sininho para não perder nenhum resumo. "
        "Até a próxima!"
    )
    segment_texts = [intro_text] + [
        item.get("ai_summary") or f"{item['title']} — Fonte: {item['source']}"
        for item in items_to_process
    ] + [outro_text]
    print("\nGerando áudio por segmento com Edge TTS...")
    audio_path, all_durations = await generate_audio_segments(
        segment_texts, AUDIO_OUTPUT_DIR, filename_base
    )
    intro_duration = all_durations[0]
    # Último segmento é o outro (CTA) — agregar ao último slide de notícia
    # para que o visual fique na última notícia enquanto o CTA é falado
    news_durations = list(all_durations[1:])
    if len(news_durations) > len(items_to_process):
        outro_dur = news_durations.pop()
        news_durations[-1] += outro_dur

    # 7. Copiar áudio para o Google Drive
    drive_audio_path = os.path.join(DRIVE_SYNC_DIR, f"{filename_base}.mp3")
    shutil.copy2(audio_path, drive_audio_path)
    print(f"Áudio copiado para o Drive: {drive_audio_path}")

    # 8. Gerar vídeo dinâmico com durações reais por segmento
    video_path = await asyncio.to_thread(
        generate_video,
        items_to_process,
        audio_path,
        CHANNEL_NAME,
        f"{filename_base}.mp4",
        news_durations,
        intro_duration,
    )

    print(f"\n--- Ciclo Finalizado! ---")
    print(f"Áudio local: {audio_path}")
    print(f"Áudio no Drive: {drive_audio_path}")
    print(f"Roteiro no Drive: {md_path}")

    # 9. Upload para o YouTube
    if YOUTUBE_UPLOAD:
        date_str = datetime.now().strftime("%d/%m/%Y")
        yt_title = f"Resumo de Notícias — {date_str}"
        yt_description = build_description(items_to_process, date_str)
        yt_tags = ["notícias", "brasil", "resumo", "jornalismo", "atualidades"]
        privacy = "public" if YOUTUBE_PUBLISH_NOW else "private"

        if YOUTUBE_PUBLISH_NOW:
            print("\nUpload imediato como público...")
        else:
            print(f"\nUpload agendado para às {YOUTUBE_PUBLISH_HOUR}h...")

        try:
            video_id = await asyncio.to_thread(
                upload_video,
                video_path,
                yt_title,
                yt_description,
                yt_tags,
                YOUTUBE_PUBLISH_HOUR,
                privacy,
            )
            print(f"YouTube: https://youtu.be/{video_id}")
            add_to_playlist(video_id, "noticias")

            # Thumbnail automática
            print("\nGerando thumbnail...")
            from thumbnail import generate_thumbnail
            thumb_path = os.path.join(AUDIO_OUTPUT_DIR, f"{filename_base}_thumb.jpg")
            try:
                generate_thumbnail(items_to_process, thumb_path)
                await asyncio.to_thread(upload_thumbnail, video_id, thumb_path)

                # Instagram — posta thumbnail como foto no feed
                from config import INSTAGRAM_UPLOAD
                if INSTAGRAM_UPLOAD:
                    from instagram_uploader import upload_photo, INSTAGRAM_ENABLED
                    if INSTAGRAM_ENABLED:
                        ig_caption = (
                            f"Resumo de Notícias — {date_str}\n\n"
                            + "\n".join(f"- {it.get('title', '')}" for it in items_to_process[:5])
                            + f"\n\nAssista completo no YouTube!\n\n"
                            f"#noticias #brasil #resumodenoticias #newsapp"
                        )
                        upload_photo(thumb_path, ig_caption)

                try:
                    os.remove(thumb_path)
                except Exception:
                    pass
            except Exception as e:
                print(f"  Thumbnail ignorada: {e}")

            # Short automático — corte vertical dos primeiros 55s
            if YOUTUBE_GENERATE_SHORT:
                print("\nGerando Short do clipe...")
                from shorts import generate_short_from_video
                date_str_short = datetime.now().strftime("%d/%m/%Y")
                await asyncio.to_thread(
                    generate_short_from_video,
                    video_path,
                    f"Notícias — {date_str_short}",
                    items_to_process,
                    True,
                    privacy,
                )

            # Apaga vídeo e áudio locais após upload bem-sucedido
            for path, label in [(video_path, "Vídeo"), (audio_path, "Áudio")]:
                try:
                    os.remove(path)
                    print(f"{label} local removido: {path}")
                except Exception as e:
                    print(f"Aviso: não foi possível remover {label.lower()} local: {e}")

            from telegram_notifier import notify
            notify(f"✅ <b>Notícias postadas!</b>\n{yt_title}\nhttps://youtu.be/{video_id}")
        except FileNotFoundError as e:
            print(f"\nUpload ignorado: {e}")
        except Exception as e:
            print(f"\nErro no upload YouTube: {e}")
            from telegram_notifier import notify
            notify(f"❌ <b>Erro no upload de Notícias:</b> {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog="main.py", add_help=False)
    parser.add_argument("--sem-upload", action="store_true")
    parser.add_argument("--privado",    action="store_true")
    args, _ = parser.parse_known_args()

    if args.sem_upload:
        YOUTUBE_UPLOAD = False
    if args.privado:
        YOUTUBE_PUBLISH_NOW = False

    asyncio.run(run_news_cycle())
