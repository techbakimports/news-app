"""
Runner isolado para pipelines de clientes.
Lê NICHO_CONFIG (JSON em env) e gera Shorts com queries customizadas.
Uso: NICHO_CONFIG='{"queries":[...],...}' python engine/runner.py
"""
import asyncio
import json
import os
import sys

# garante que importa módulos da raiz do projeto
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def run(config: dict) -> None:
    from fetcher import fetch_by_custom_queries
    from shorts import generate_short_from_text
    from summarizer import summarize_batch

    queries = config.get("queries", [])
    max_shorts = min(int(config.get("max_shorts", 3)), 5)
    voice = config.get("voice", "pt-BR-AntonioNeural")
    color_hex = config.get("color_hex", "#1E90FF")
    name = config.get("name", "Nicho")

    # Canal do CLIENTE, não o do admin — sem isso o vídeo sobe no canal
    # principal em vez do canal que o cliente conectou no painel dele.
    token_file = os.getenv("YOUTUBE_TOKEN_FILE") or None

    print(f"[runner] Nicho: {name} | queries: {queries}")

    news = await fetch_by_custom_queries(queries, limit_per_query=3)
    if not news:
        print("[runner] Nenhuma notícia encontrada.")
        return

    news = news[:max_shorts]
    print(f"[runner] {len(news)} notícias para resumir")

    summaries = await summarize_batch(news)

    for item in summaries[:max_shorts]:
        if not item.get("resumo"):
            continue
        print(f"[runner] Gerando Short: {item.get('titulo', '?')}")
        try:
            result = await generate_short_from_text(
                title=item.get("titulo", name),
                narration=item["resumo"],
                category=name,
                source=item.get("fonte", ""),
                upload=True,
                privacy="public",
                hashtags=[name.replace(" ", ""), "shorts"],
                playlist_key=None,
                instagram_enabled=False,
                youtube_enabled=True,
                link=item.get("link", ""),
                voice=voice,
                display_text=None,
                token_file=token_file,
            )
        except RuntimeError as e:
            # _get_credentials (uploader.py) levanta RuntimeError quando o
            # token do cliente expirou/foi revogado e não há terminal
            # interativo pra reautenticar (sempre o caso aqui, é subprocess).
            # Mensagem curta e de uma linha só — vira o "last_line" que o
            # webserver mostra pro cliente, sem vazar stack trace.
            print(f"[runner] Token do YouTube inválido: reconecte seu canal no painel. ({e})")
            return
        # generate_short_from_text retorna o video_id do YouTube quando o upload
        # é bem-sucedido, ou o caminho local do arquivo quando falha/desativado.
        if result and os.sep not in result and not result.lower().endswith(".mp4"):
            print(f"VIDEO_ID:{result}")

    print("[runner] Concluído.")


if __name__ == "__main__":
    raw = os.getenv("NICHO_CONFIG", "{}")
    try:
        config = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"[runner] NICHO_CONFIG inválido: {e}")
        sys.exit(1)

    from dotenv import load_dotenv
    load_dotenv()

    asyncio.run(run(config))
