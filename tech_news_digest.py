"""Tech Digest via NotebookLM.

Cria notebook temporário com os 10 melhores sites de tecnologia,
pede um resumo das principais notícias do dia, retorna o texto
e deleta o notebook para permitir reutilização.
"""
import asyncio
from datetime import datetime

from notebooklm import NotebookLMClient

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


async def generate_tech_digest(on_progress=None) -> str:
    """Gera resumo das principais notícias de tecnologia do dia.

    on_progress: callback async(str) para atualizações de status.
    Retorna o texto do resumo ou mensagem de erro.
    """
    date_str = datetime.now().strftime("%d/%m/%Y")

    async def _progress(msg: str):
        print(msg)
        if on_progress:
            await on_progress(msg)

    try:
        await _progress("Conectando ao NotebookLM...")
        client = await NotebookLMClient.from_storage()
    except FileNotFoundError:
        return "Autenticacao NotebookLM nao encontrada.\nExecute: notebooklm login"

    async with client:
        notebook = await client.notebooks.create(title=f"Tech Digest {date_str}")
        notebook_id = notebook.id
        await _progress("Notebook criado")

        try:
            await _progress(f"Adicionando {len(TECH_SITES)} sites...")
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
                return "Nenhum site pode ser adicionado como fonte."

            await _progress(f"Aguardando processamento de {len(sources)} fontes...")
            await client.sources.wait_for_sources(
                notebook_id,
                [s.id for s in sources],
                timeout=180.0,
            )
            await _progress("Fontes processadas!")

            await _progress("Gerando resumo das noticias...")
            prompt = (
                f"Com base em todas as fontes adicionadas, liste as principais "
                f"noticias de tecnologia de hoje ({date_str}).\n\n"
                f"Organize por importancia/relevancia. Para cada noticia:\n"
                f"- Titulo curto e chamativo\n"
                f"- Resumo de 2-3 frases explicando o que aconteceu\n\n"
                f"Liste pelo menos 8 noticias, se disponiveis. "
                f"Responda em portugues do Brasil. Sem markdown — use texto simples."
            )
            result = await client.chat.ask(notebook_id, prompt)
            await _progress("Resumo gerado!")
            return result.answer

        finally:
            try:
                await client.notebooks.delete(notebook_id)
                await _progress("Notebook deletado.")
            except Exception:
                pass


async def main():
    result = await generate_tech_digest()
    print("\n" + "=" * 60)
    print(result)
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
