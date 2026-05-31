"""Tech Digest via Google News + Groq.

Busca notícias recentes dos 10 melhores sites de tecnologia,
pede um resumo das principais notícias do dia via Groq (fallback Gemini),
retorna o texto formatado pra exibir no chat Telegram.

NotebookLM removido — fonte é Google News RSS filtrado por sites tech.
"""
import asyncio
import os
from datetime import datetime

from tech_news import TECH_SITES, _fetch_tech_via_google_news


async def generate_tech_digest(on_progress=None) -> str:
    """Gera resumo textual das principais notícias de tecnologia do dia.

    on_progress: callback async(str) para atualizações de status.
    Retorna o texto do resumo ou mensagem de erro.
    """
    date_str = datetime.now().strftime("%d/%m/%Y")

    async def _progress(msg: str):
        print(msg)
        if on_progress:
            await on_progress(msg)

    await _progress("Buscando notícias dos sites tech...")
    try:
        items = _fetch_tech_via_google_news(limit_per_site=4)
    except Exception as e:
        return f"Erro ao buscar notícias: {e}"

    if not items:
        return "Nenhuma notícia tech encontrada hoje."

    # Pega top 12 mais recentes pra fazer o digest
    items = items[:12]
    await _progress(f"{len(items)} notícias encontradas. Gerando resumo...")

    # Monta lista compacta pro LLM
    news_block = ""
    for i, item in enumerate(items, 1):
        news_block += f"\n{i}. {item['title']} ({item['source']})"
        # Adiciona um trecho do summary se houver
        if item.get("summary"):
            from re import sub as _re_sub
            clean_summary = _re_sub(r"<[^>]+>", "", item["summary"])[:200].strip()
            if clean_summary:
                news_block += f"\n   Resumo: {clean_summary}"

    prompt = (
        f"Você é um curador de notícias de tecnologia. Com base na lista abaixo, "
        f"produza um resumo organizado das principais novidades de tecnologia de hoje ({date_str}).\n\n"
        f"REGRAS:\n"
        f"- Para cada notícia (no máximo 10), escreva:\n"
        f"  Título curto e claro (em portugues, mesmo se a original for em ingles)\n"
        f"  Resumo de 1-2 frases explicando o impacto\n"
        f"- Use apenas texto simples, sem markdown ou asteriscos\n"
        f"- Organize por importancia/relevancia\n"
        f"- Português do Brasil\n\n"
        f"Notícias do dia:{news_block}"
    )

    # 1) Groq primário
    groq_key = os.environ.get("GROQ_API_KEY", "")
    if groq_key and groq_key != "cole_sua_chave_aqui":
        try:
            from groq import Groq
            client = Groq(api_key=groq_key)
            await _progress("Resumindo com Groq...")
            resp = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.6,
            )
            text = resp.choices[0].message.content.strip()
            return f"Tech Digest — {date_str}\n\n{text}"
        except Exception as e:
            await _progress(f"Groq falhou: {e}. Tentando Gemini...")

    # 2) Gemini fallback
    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    if gemini_key:
        try:
            from google import genai
            client = genai.Client(api_key=gemini_key)
            await _progress("Resumindo com Gemini...")
            resp = client.models.generate_content(model="gemini-2.0-flash", contents=prompt)
            return f"Tech Digest — {date_str}\n\n{resp.text.strip()}"
        except Exception as e:
            return f"Erro: Gemini também falhou — {e}"

    return "Nenhum provedor LLM disponível (configure GROQ_API_KEY ou GEMINI_API_KEY)."


async def main():
    result = await generate_tech_digest()
    print("\n" + "=" * 60)
    print(result)
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
