import asyncio
import os
import shutil
import time
from datetime import datetime, timedelta
from fetcher import fetch_latest_news, extract_article_content
from summarizer import summarize_news
from audio import generate_audio
from video import generate_video
from config import DRIVE_SYNC_DIR, AUDIO_OUTPUT_DIR, CHANNEL_NAME

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

    # 1. Buscar notícias
    raw_news = fetch_latest_news(limit=1) 
    
    processed_links = set()
    consolidated_script = "Bem-vindo ao seu resumo de notícias automatizado.\n\n"
    total_words = 0
    max_words = 2200 # Aproximadamente 15 minutos de áudio
    
    items_to_process = []
    
    # Deduplicação e filtragem
    for item in raw_news:
        if item['link'] not in processed_links:
            processed_links.add(item['link'])
            items_to_process.append(item)

    print(f"Total de notícias únicas encontradas: {len(items_to_process)}")

    if not os.path.exists(DRIVE_SYNC_DIR): os.makedirs(DRIVE_SYNC_DIR)

    for item in items_to_process:
        if total_words > max_words:
            print("Limite de 15 minutos atingido. Parando por aqui.")
            break

        print(f"\nProcessando {item['category']}: {item['title']}")
        
        content = extract_article_content(item['link'])
        if not content: content = item['summary']
            
        summary = summarize_news(item['category'], item['title'], content)

        # Pequena pausa para evitar erro de limite da API (Quota)
        await asyncio.sleep(2)

        if summary:
            item["ai_summary"] = summary
            consolidated_script += f"{summary}\n\n"
            total_words += len(summary.split())
        else:
            item["ai_summary"] = None
            consolidated_script += f"{item['title']} — Fonte: {item['source']}\n\n"
            total_words += len(item['title'].split()) + 2
        
    # 5. Salvar Texto Único para NotebookLM
    timestamp = datetime.now().strftime('%Y%m%d_%H%M')
    filename_base = f"Resumo_Completo_{timestamp}"
    
    md_path = os.path.join(DRIVE_SYNC_DIR, f"{filename_base}.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# Resumo de Notícias - {datetime.now().strftime('%d/%m/%Y')}\n\n")
        f.write(consolidated_script)
            
    # 6. Gerar Áudio Único
    print("\nGerando áudio consolidado (isso pode demorar um pouco)...")
    audio_path = await generate_audio(consolidated_script, f"{filename_base}.mp3")

    # 7. Copiar áudio para o Google Drive
    drive_audio_path = os.path.join(DRIVE_SYNC_DIR, f"{filename_base}.mp3")
    shutil.copy2(audio_path, drive_audio_path)
    print(f"Áudio copiado para o Drive: {drive_audio_path}")

    # 8. Gerar vídeo dinâmico
    video_path = await asyncio.to_thread(
        generate_video,
        items_to_process,
        audio_path,
        CHANNEL_NAME,
        f"{filename_base}.mp4",
    )

    print(f"\n--- Ciclo Finalizado! ---")
    print(f"Áudio local: {audio_path}")
    print(f"Vídeo local: {video_path}")
    print(f"Áudio no Drive: {drive_audio_path}")
    print(f"Roteiro no Drive: {md_path}")

if __name__ == "__main__":
    asyncio.run(run_news_cycle())
