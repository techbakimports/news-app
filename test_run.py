import asyncio
import os
from fetcher import fetch_latest_news, extract_article_content
from summarizer import summarize_news
from audio import generate_audio
from config import DRIVE_SYNC_DIR

async def test_single_news():
    print("Iniciando teste simplificado...")
    
    # 1. Busca apenas 1 notícia do G1
    print("Buscando notícia do G1...")
    feed = fetch_latest_news(limit=1)
    # Filtra apenas G1 para o teste ser rápido
    g1_news = [n for n in feed if n['source'] == "G1"]
    
    if not g1_news:
        print("Não foi possível encontrar notícias do G1 no feed.")
        return

    item = g1_news[0]
    print(f"Notícia encontrada: {item['title']}")

    # 2. Extrai conteúdo
    print("Extraindo conteúdo...")
    content = extract_article_content(item['link'])
    if not content:
        content = item['summary']
    
    # 3. Resume (Gemini)
    print("Gerando resumo com Gemini...")
    summary = summarize_news(item['title'], content)
    print(f"Resumo: {summary[:100]}...")

    # 4. Gera Áudio
    print("Gerando áudio...")
    audio_path = await generate_audio(summary, "teste_final.mp3")
    print(f"Sucesso! Áudio gerado em: {audio_path}")
    
    # 5. Salva texto
    if not os.path.exists(DRIVE_SYNC_DIR):
        os.makedirs(DRIVE_SYNC_DIR)
    
    with open(os.path.join(DRIVE_SYNC_DIR, "teste_notebook.md"), "w", encoding="utf-8") as f:
        f.write(f"# {item['title']}\n\n{summary}")
    print("Arquivo de texto para NotebookLM criado.")

if __name__ == "__main__":
    asyncio.run(test_single_news())
