import asyncio
import os
import shutil
import time
from datetime import datetime, timedelta
from fetcher import fetch_latest_news, extract_article_content, select_unique_news
from summarizer import summarize_news_batch
from audio import generate_audio_segments
from video import generate_video
from uploader import upload_video, build_description
from config import DRIVE_SYNC_DIR, AUDIO_OUTPUT_DIR, CHANNEL_NAME

# Define True para publicar no YouTube após gerar o vídeo.
# Na primeira execução abrirá o browser para autorização OAuth.
YOUTUBE_UPLOAD = True
YOUTUBE_PUBLISH_HOUR = 5   # hora local em que o vídeo será publicado
YOUTUBE_PRIVACY = "private"  # mantido como "private" até publishAt — torna público às 5h automaticamente

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

    # Fase 1: extração de conteúdo (sem custo de API)
    print("\nExtraindo conteúdo dos artigos...")
    for item in items_to_process:
        content = extract_article_content(item['link'])
        item['_content'] = content if content else item.get('summary', '')

    # Fase 2: resumos em lotes — 5 notícias por chamada Gemini
    # Plano gratuito: 20 req/dia e 5 req/min.
    # Com BATCH_SIZE=5: máx. 4 chamadas/dia para 20 itens. Sleep de 15s entre lotes.
    BATCH_SIZE = 5
    processed_items = []
    total_batches = (len(items_to_process) + BATCH_SIZE - 1) // BATCH_SIZE
    print(f"\nGerando resumos em {total_batches} lote(s) de até {BATCH_SIZE} notícias...")

    for batch_idx, batch_start in enumerate(range(0, len(items_to_process), BATCH_SIZE)):
        if total_words > max_words:
            print("Limite de 15 minutos atingido. Parando por aqui.")
            break

        batch = items_to_process[batch_start:batch_start + BATCH_SIZE]
        batch_input = [
            {'category': i['category'], 'title': i['title'], 'content': i['_content']}
            for i in batch
        ]

        print(f"\n[Lote {batch_idx + 1}/{total_batches}] {len(batch)} notícias...")
        summaries = summarize_news_batch(batch_input)

        for item, summary in zip(batch, summaries):
            if total_words > max_words:
                break
            if summary:
                item['ai_summary'] = summary
                consolidated_script += f"{summary}\n\n"
                total_words += len(summary.split())
            else:
                item['ai_summary'] = None
                consolidated_script += f"{item['title']} — Fonte: {item['source']}\n\n"
                total_words += len(item['title'].split()) + 2
            processed_items.append(item)

        # Sleep entre lotes (não após o último)
        if batch_start + BATCH_SIZE < len(items_to_process) and total_words <= max_words:
            await asyncio.sleep(15)

    items_to_process = processed_items
        
    # 5. Salvar Texto para NotebookLM
    timestamp = datetime.now().strftime('%Y%m%d_%H%M')
    filename_base = f"Resumo_Completo_{timestamp}"

    md_path = os.path.join(DRIVE_SYNC_DIR, f"{filename_base}.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# Resumo de Notícias - {datetime.now().strftime('%d/%m/%Y')}\n\n")
        f.write(consolidated_script)

    # 6. Gerar áudio por segmento (duração exata para sync do vídeo)
    # O primeiro segmento é a vinheta de abertura; os demais são as notícias.
    intro_text = "Bem-vindo ao seu resumo de notícias automatizado."
    segment_texts = [intro_text] + [
        item.get("ai_summary") or f"{item['title']} — Fonte: {item['source']}"
        for item in items_to_process
    ]

    print("\nGerando áudio por segmento (isso pode demorar um pouco)...")
    audio_path, all_durations = await generate_audio_segments(
        segment_texts, AUDIO_OUTPUT_DIR, filename_base
    )

    # Vinheta de abertura → slide separado; cada notícia fica exatamente com sua duração real
    intro_duration = all_durations[0]
    news_durations = list(all_durations[1:])

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
    print(f"Vídeo local: {video_path}")
    print(f"Áudio no Drive: {drive_audio_path}")
    print(f"Roteiro no Drive: {md_path}")

    # 9. Upload para o YouTube
    if YOUTUBE_UPLOAD:
        date_str = datetime.now().strftime("%d/%m/%Y")
        yt_title = f"Resumo de Notícias — {date_str}"
        yt_description = build_description(items_to_process, date_str)
        yt_tags = ["notícias", "brasil", "resumo", "jornalismo", "atualidades"]

        try:
            video_id = await asyncio.to_thread(
                upload_video,
                video_path,
                yt_title,
                yt_description,
                yt_tags,
                YOUTUBE_PUBLISH_HOUR,
                YOUTUBE_PRIVACY,
            )
            print(f"YouTube: https://youtu.be/{video_id}")
        except FileNotFoundError as e:
            print(f"\nUpload ignorado: {e}")
        except Exception as e:
            print(f"\nErro no upload YouTube: {e}")

if __name__ == "__main__":
    asyncio.run(run_news_cycle())
