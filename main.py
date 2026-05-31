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

# Cadeia de resumos: Groq (primário) → Gemini (fallback) → título.
# NotebookLM removido completamente — cookies expiravam constantemente.

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
    pipeline_start = time.time()
    def _elapsed():
        m, s = divmod(int(time.time() - pipeline_start), 60)
        return f"[T+{m:02d}:{s:02d}]"

    print(f"--- Iniciando Ciclo Consolidado: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')} ---")
    cleanup_old_files()

    # 1. Buscar notícias (3 candidatos por fonte/categoria para permitir dedup por título)
    print(f"\n{_elapsed()} [FASE 1] Buscando notícias no Google News...")
    phase_start = time.time()
    raw_news = fetch_latest_news(limit=3)
    print(f"{_elapsed()} [FASE 1] OK — {len(raw_news)} candidatos em {int(time.time()-phase_start)}s")

    # Deduplicação por similaridade de título — evita repetir a mesma história
    # em duas fontes diferentes da mesma categoria
    print(f"\n{_elapsed()} [FASE 2] Deduplicando notícias...")
    phase_start = time.time()
    items_to_process = select_unique_news(raw_news)
    print(f"{_elapsed()} [FASE 2] OK — {len(items_to_process)} únicas em {int(time.time()-phase_start)}s")

    consolidated_script = ""
    total_words = 0
    max_words = 2200  # Aproximadamente 15 minutos de áudio

    print(f"\nTotal de notícias únicas selecionadas: {len(items_to_process)}")

    if not os.path.exists(DRIVE_SYNC_DIR): os.makedirs(DRIVE_SYNC_DIR)

    # Fase 3: resumos via Groq (primário) → Gemini (fallback)
    print(f"\n{_elapsed()} [FASE 3] Gerando resumos (Groq → Gemini)...")
    phase_start = time.time()

    print(f"{_elapsed()} [FASE 3] Extraindo conteúdo dos artigos...")
    for item in items_to_process:
        content = extract_article_content(item["link"])
        item["_content"] = content if content else item.get("summary", "")

    BATCH_SIZE = 5
    summaries = []
    total_batches = (len(items_to_process) + BATCH_SIZE - 1) // BATCH_SIZE
    print(f"{_elapsed()} [FASE 3] Gerando em {total_batches} lote(s) de até {BATCH_SIZE} notícias...")
    for batch_idx, batch_start in enumerate(range(0, len(items_to_process), BATCH_SIZE)):
        batch = items_to_process[batch_start:batch_start + BATCH_SIZE]
        batch_input = [
            {"category": i["category"], "title": i["title"], "content": i["_content"]}
            for i in batch
        ]
        print(f"[Lote {batch_idx + 1}/{total_batches}] {len(batch)} notícias...")
        summaries.extend(summarize_news_batch(batch_input))
        # Groq aguenta 30 req/min — sem precisar de sleep grande
        if batch_start + BATCH_SIZE < len(items_to_process):
            await asyncio.sleep(2)
    print(f"{_elapsed()} [FASE 3] OK em {int(time.time()-phase_start)}s")

    # Monta roteiro consolidado — SO inclui notícias com resumo de LLM real.
    # Notícias sem resumo são descartadas (não geramos vídeo lendo só o título).
    processed_items = []
    skipped_no_summary = 0
    for item, summary in zip(items_to_process, summaries):
        if total_words > max_words:
            print(f"{_elapsed()} Limite de 15 minutos atingido. Parando por aqui.")
            break
        if not summary:
            skipped_no_summary += 1
            continue  # PULA — não lemos só o título
        item["ai_summary"] = summary
        consolidated_script += f"{summary}\n\n"
        total_words += len(summary.split())
        processed_items.append(item)

    items_to_process = processed_items

    print(f"{_elapsed()} Notícias com resumo válido: {len(items_to_process)} "
          f"| pulados sem resumo: {skipped_no_summary}")

    # Aborta se NENHUMA notícia teve resumo — não geramos vídeo só com títulos
    MIN_NEWS_FOR_VIDEO = 3
    if len(items_to_process) < MIN_NEWS_FOR_VIDEO:
        msg = (
            f"❌ Apenas {len(items_to_process)} notícia(s) com resumo de LLM "
            f"(mínimo: {MIN_NEWS_FOR_VIDEO}). Provavelmente Groq E Gemini falharam. "
            f"Pipeline ABORTADO — não geramos vídeo só com títulos."
        )
        print(f"\n{msg}")
        try:
            from telegram_notifier import notify
            notify(
                f"❌ <b>Notícias:</b> pipeline abortado.\n"
                f"Apenas {len(items_to_process)}/{len(summaries)} notícias com resumo válido.\n"
                f"Verifique Groq e Gemini."
            )
        except Exception:
            pass
        return
        
    # 5. Salvar roteiro consolidado no Drive
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
    # Todos os items aqui já têm ai_summary garantido (filtro acima)
    segment_texts = [intro_text] + [
        item["ai_summary"] for item in items_to_process
    ] + [outro_text]
    print(f"\n{_elapsed()} [FASE 4] Gerando áudio Edge TTS ({len(segment_texts)} segmentos)...")
    phase_start = time.time()
    audio_path, all_durations = await generate_audio_segments(
        segment_texts, AUDIO_OUTPUT_DIR, filename_base
    )
    print(f"{_elapsed()} [FASE 4] OK em {int(time.time()-phase_start)}s — áudio: {sum(all_durations):.0f}s")
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
    print(f"\n{_elapsed()} [FASE 5] Renderizando vídeo MP4...")
    phase_start = time.time()
    video_path = await asyncio.to_thread(
        generate_video,
        items_to_process,
        audio_path,
        CHANNEL_NAME,
        f"{filename_base}.mp4",
        news_durations,
        intro_duration,
    )
    print(f"{_elapsed()} [FASE 5] OK em {int(time.time()-phase_start)}s")

    print(f"\n{_elapsed()} --- Ciclo Finalizado! ---")
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

        print(f"{_elapsed()} [FASE 6] Upload do vídeo longo no YouTube...")
        phase_start = time.time()
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
            print(f"{_elapsed()} [FASE 6] OK em {int(time.time()-phase_start)}s — https://youtu.be/{video_id}")
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

            # Shorts por categoria — corta o vídeo longo em segmentos verticais
            # (1 Short por categoria, exceto "Esporte")
            if YOUTUBE_GENERATE_SHORT:
                print(f"\n{_elapsed()} [FASE 7] Gerando Shorts por categoria...")
                phase_start = time.time()
                from shorts import generate_shorts_per_category
                try:
                    await asyncio.to_thread(
                        generate_shorts_per_category,
                        video_path,
                        items_to_process,
                        news_durations,
                        intro_duration,
                        ["Esporte"],   # categorias excluídas
                        True,          # upload
                        privacy,
                    )
                except Exception as e:
                    print(f"Erro ao gerar Shorts por categoria: {e}")
                print(f"{_elapsed()} [FASE 7] OK em {int(time.time()-phase_start)}s")

            # Apaga vídeo e áudio locais SOMENTE após todos os Shorts processados
            for path, label in [(video_path, "Vídeo"), (audio_path, "Áudio")]:
                try:
                    os.remove(path)
                    print(f"{label} local removido: {path}")
                except Exception as e:
                    print(f"Aviso: não foi possível remover {label.lower()} local: {e}")

            total_min, total_sec = divmod(int(time.time() - pipeline_start), 60)
            print(f"\n{_elapsed()} === PIPELINE CONCLUÍDO === ({total_min}m{total_sec:02d}s totais)")
            from telegram_notifier import notify
            notify(
                f"✅ <b>Notícias postadas!</b> ({total_min}m{total_sec:02d}s)\n"
                f"{yt_title}\nhttps://youtu.be/{video_id}"
            )
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
