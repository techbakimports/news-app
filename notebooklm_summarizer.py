import asyncio
from datetime import datetime
from notebooklm import NotebookLMClient


async def summarize_news_notebooklm(news_items):
    """
    Substitui o Gemini na etapa de resumo.

    Cria um notebook temporário, adiciona as URLs dos artigos como fontes,
    aguarda o processamento e gera um resumo por notícia via chat.
    Deleta o notebook ao final.

    Retorna lista de resumos na mesma ordem de news_items.
    Retorna None em caso de falha (main.py usa Gemini como fallback).
    """
    date_str = datetime.now().strftime("%d/%m/%Y")

    print("\nIniciando resumo via NotebookLM...")
    try:
        client = await NotebookLMClient.from_storage()
        async with client:
            # 1. Criar notebook temporário
            notebook = await client.notebooks.create(title=f"Notícias {date_str}")
            notebook_id = notebook.id
            print(f"  Notebook criado: {notebook_id}")

            try:
                # 2. Adicionar URLs como fontes (sem esperar — processa em paralelo)
                print(f"  Adicionando {len(news_items)} fontes...")
                sources = []
                for item in news_items:
                    source = await client.sources.add_url(notebook_id, item["link"])
                    sources.append(source)

                # 3. Aguardar todas as fontes serem processadas
                print("  Aguardando processamento das fontes...")
                await client.sources.wait_for_sources(
                    notebook_id,
                    [s.id for s in sources],
                    timeout=120.0,
                )
                print("  Fontes prontas.")

                # 4. Gerar resumo de cada notícia via chat
                summaries = []
                for i, item in enumerate(news_items, 1):
                    print(f"  [{i}/{len(news_items)}] Resumindo: {item['title'][:55]}...")
                    prompt = (
                        f"Resuma a notícia '{item['title']}' em até 150 palavras, "
                        f"no estilo de locutor de podcast em português do Brasil. "
                        f"Seja direto e informativo. Sem markdown, sem bullet points, "
                        f"sem asteriscos. Apenas o texto corrido."
                    )
                    result = await client.chat.ask(notebook_id, prompt)
                    summaries.append(result.answer)

                print(f"  {len(summaries)} resumos gerados pelo NotebookLM.")
                return summaries

            finally:
                try:
                    await client.notebooks.delete(notebook_id)
                    print(f"  Notebook {notebook_id} deletado.")
                except Exception as e:
                    print(f"  Aviso: não foi possível deletar o notebook: {e}")

    except FileNotFoundError:
        print("  Erro: autenticação NotebookLM não encontrada. Execute 'notebooklm login'.")
        return None
    except Exception as e:
        print(f"  Erro no NotebookLM: {e}")
        return None
