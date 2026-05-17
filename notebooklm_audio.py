import asyncio
import os
from datetime import datetime
from notebooklm import NotebookLMClient
from notebooklm._artifacts import AudioFormat, AudioLength


async def generate_audio_notebooklm(consolidated_script, output_path, date_str=None):
    """
    Gera áudio de podcast via NotebookLM Audio Overview.

    Cria um notebook temporário, adiciona o roteiro como fonte de texto,
    dispara a geração do Audio Overview em português e baixa o arquivo.
    O notebook é deletado ao final para não acumular lixo.

    Retorna o caminho do arquivo de áudio gerado, ou None em caso de erro.
    """
    if not date_str:
        date_str = datetime.now().strftime("%d/%m/%Y")

    notebook_title = f"Notícias {date_str}"

    print("\nConectando ao NotebookLM...")
    try:
        async with NotebookLMClient.from_storage() as client:
            # 1. Criar notebook temporário
            print(f"  Criando notebook: {notebook_title}")
            notebook = await client.notebooks.create(title=notebook_title)
            notebook_id = notebook.id
            print(f"  Notebook criado: {notebook_id}")

            try:
                # 2. Adicionar roteiro como fonte de texto
                print("  Adicionando roteiro como fonte...")
                source_title = f"Resumo de Notícias — {date_str}"
                await client.sources.add_text(
                    notebook_id,
                    title=source_title,
                    content=consolidated_script,
                    wait=True,
                    wait_timeout=120.0,
                )
                print("  Fonte adicionada e processada.")

                # 3. Disparar geração do Audio Overview
                print("  Iniciando geração do Audio Overview (pt-BR, formato DEEP_DIVE, duração LONG)...")
                status = await client.artifacts.generate_audio(
                    notebook_id,
                    language="pt-BR",
                    instructions=(
                        "Você é uma dupla de apresentadores de um programa de notícias brasileiro. "
                        "Apresente as notícias do dia de forma dinâmica e envolvente em português do Brasil, "
                        "cobrindo cada categoria: Política, Esporte, Entretenimento, Mercado Financeiro, "
                        "Tecnologia e Policial. Seja direto e informativo."
                    ),
                    audio_format=AudioFormat.DEEP_DIVE,
                    audio_length=AudioLength.LONG,
                )

                # 4. Aguardar conclusão (pode demorar vários minutos)
                print(f"  Aguardando conclusão (task_id: {status.task_id})...")
                print("  Isso pode levar de 5 a 15 minutos...")
                await client.artifacts.wait_for_completion(
                    notebook_id,
                    status.task_id,
                    timeout=1200.0,  # até 20 min
                    poll_interval=15.0,
                )
                print("  Audio Overview concluído!")

                # 5. Baixar o áudio
                os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else ".", exist_ok=True)
                print(f"  Baixando áudio para: {output_path}")
                await client.artifacts.download_audio(notebook_id, output_path)
                print(f"  Download concluído: {output_path}")

            finally:
                # 6. Deletar notebook temporário
                print(f"  Deletando notebook temporário {notebook_id}...")
                try:
                    await client.notebooks.delete(notebook_id)
                    print("  Notebook deletado.")
                except Exception as e:
                    print(f"  Aviso: não foi possível deletar o notebook: {e}")

        return output_path

    except FileNotFoundError:
        print("  Erro: autenticação do NotebookLM não encontrada. Execute 'notebooklm login' no terminal.")
        return None
    except Exception as e:
        print(f"  Erro ao gerar áudio via NotebookLM: {e}")
        return None
