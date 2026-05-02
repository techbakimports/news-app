import os
import re
import time
import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()

# Configuração da API
api_key = os.getenv("GEMINI_API_KEY")
if api_key:
    genai.configure(api_key=api_key)
else:
    print("AVISO: GEMINI_API_KEY não encontrada no arquivo .env")

def summarize_news(category, title, content):
    """
    Usa o Gemini para resumir a notícia incluindo a categoria.
    """
    if not api_key:
        return "Resumo indisponível (chave de API não configurada)."

    try:
        model = genai.GenerativeModel('gemini-flash-latest')
        prompt = f"""
        Você é um locutor de podcast de notícias. Resuma a notícia abaixo de forma concisa e envolvente.

        REGRAS OBRIGATÓRIAS:
        - Comece anunciando a categoria: "Agora em {category}..." ou "Na área de {category}...".
        - Use linguagem natural e fluida em português do Brasil, como se estivesse falando ao vivo.
        - NÃO use markdown, asteriscos, hashtags, underlines ou qualquer formatação de texto.
        - Escreva APENAS texto simples, sem símbolos especiais.
        - Máximo de 120 palavras.

        Título: {title}
        Conteúdo: {content}

        Resumo (somente texto simples):
        """
        
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        print(f"Erro ao gerar resumo com Gemini: {e}")
        return None

def summarize_news_batch(items, _retry=True):
    """
    Resume múltiplas notícias em uma única chamada Gemini.
    items: list of dict com chaves 'category', 'title', 'content'.
    Retorna list de str (mesma ordem); None para itens que falharam.
    Usa marcadores ===RESUMO_N=== para separar as respostas.
    """
    if not api_key:
        return [None] * len(items)
    if not items:
        return []

    noticias_text = ""
    for i, item in enumerate(items, 1):
        content_preview = (item.get('content') or '')[:800]
        noticias_text += (
            f"\nNotícia {i}:\n"
            f"Categoria: {item['category']}\n"
            f"Título: {item['title']}\n"
            f"Conteúdo: {content_preview}\n"
        )

    prompt = f"""Você é um locutor de podcast de notícias. Para cada notícia abaixo, escreva um resumo conciso e envolvente.

REGRAS OBRIGATÓRIAS:
- Comece cada resumo anunciando a categoria: "Agora em [Categoria]..." ou "Na área de [Categoria]...".
- Use linguagem natural e fluida em português do Brasil, como se estivesse falando ao vivo.
- NÃO use markdown, asteriscos, hashtags, underlines ou qualquer formatação de texto.
- Escreva APENAS texto simples, sem símbolos especiais.
- Máximo de 120 palavras por notícia.
- Separe cada resumo com a marcação exata: ===RESUMO_N=== (onde N é o número da notícia).

{noticias_text}
Resumos (somente texto simples, separados por ===RESUMO_N===):"""

    try:
        model = genai.GenerativeModel('gemini-flash-latest')
        response = model.generate_content(prompt)
        text = response.text.strip()

        summaries = []
        for i in range(1, len(items) + 1):
            marker = f"===RESUMO_{i}==="
            next_marker = f"===RESUMO_{i + 1}===" if i < len(items) else None
            start = text.find(marker)
            if start == -1:
                summaries.append(None)
                continue
            start += len(marker)
            end = text.find(next_marker) if next_marker else len(text)
            if end == -1:
                end = len(text)
            summary = text[start:end].strip()
            summaries.append(summary or None)

        return summaries

    except Exception as e:
        err = str(e)
        if '429' in err:
            # Limite diário: não adianta esperar na mesma sessão
            if 'PerDay' in err or 'per_day' in err.lower():
                print(f"  Limite DIÁRIO da API Gemini atingido. Tente novamente amanhã.")
                return [None] * len(items)
            # Limite por minuto: aguarda o tempo sugerido e tenta uma vez
            if _retry:
                m = re.search(r'retry_delay\s*\{\s*seconds:\s*(\d+)', err)
                wait = int(m.group(1)) + 5 if m else 65
                print(f"  Rate limit (por minuto). Aguardando {wait}s e tentando novamente...")
                time.sleep(wait)
                return summarize_news_batch(items, _retry=False)
        print(f"Erro ao gerar resumos em lote com Gemini: {e}")
        return [None] * len(items)


if __name__ == "__main__":
    test_title = "Cientistas descobrem nova espécie de orquídea na Amazônia"
    test_content = "Uma expedição de botânicos brasileiros e estrangeiros identificou uma nova espécie de orquídea no coração da floresta amazônica. A planta apresenta cores vibrantes e um formato único que atraiu a atenção dos pesquisadores durante uma trilha de mapeamento."
    print(summarize_news(test_title, test_content))
