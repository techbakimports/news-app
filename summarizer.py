import os
import re
import time
import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()

gemini_key = os.getenv("GEMINI_API_KEY")
groq_key = os.getenv("GROQ_API_KEY")

if gemini_key:
    genai.configure(api_key=gemini_key)
else:
    print("AVISO: GEMINI_API_KEY não encontrada no arquivo .env")

if not groq_key or groq_key == "cole_sua_chave_aqui":
    groq_key = None


def _build_batch_prompt(items):
    noticias_text = ""
    for i, item in enumerate(items, 1):
        content_preview = (item.get('content') or '')[:800]
        noticias_text += (
            f"\nNotícia {i}:\n"
            f"Categoria: {item['category']}\n"
            f"Título: {item['title']}\n"
            f"Conteúdo: {content_preview}\n"
        )
    return f"""Você é um locutor de podcast de notícias. Para cada notícia abaixo, escreva um resumo conciso e envolvente.

REGRAS OBRIGATÓRIAS:
- Comece cada resumo anunciando a categoria: "Agora em [Categoria]..." ou "Na área de [Categoria]...".
- Use linguagem natural e fluida em português do Brasil, como se estivesse falando ao vivo.
- NÃO use markdown, asteriscos, hashtags, underlines ou qualquer formatação de texto.
- Escreva APENAS texto simples, sem símbolos especiais.
- Máximo de 120 palavras por notícia.
- Separe cada resumo com a marcação exata: ===RESUMO_N=== (onde N é o número da notícia).

{noticias_text}
Resumos (somente texto simples, separados por ===RESUMO_N===):"""


def _parse_batch_response(text, n):
    summaries = []
    for i in range(1, n + 1):
        marker = f"===RESUMO_{i}==="
        next_marker = f"===RESUMO_{i + 1}===" if i < n else None
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


def _call_gemini_batch(prompt, n):
    model = genai.GenerativeModel('gemini-flash-latest')
    response = model.generate_content(prompt)
    return _parse_batch_response(response.text.strip(), n)


def _call_groq_batch(prompt, n):
    from groq import Groq
    client = Groq(api_key=groq_key)
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
    )
    return _parse_batch_response(response.choices[0].message.content.strip(), n)


def summarize_news(category, title, content):
    """Resume uma única notícia. Mantido para compatibilidade com test_run.py."""
    if not gemini_key:
        return "Resumo indisponível (chave de API não configurada)."
    try:
        model = genai.GenerativeModel('gemini-flash-latest')
        prompt = f"""Você é um locutor de podcast de notícias. Resuma a notícia abaixo de forma concisa e envolvente.

REGRAS OBRIGATÓRIAS:
- Comece anunciando a categoria: "Agora em {category}..." ou "Na área de {category}...".
- Use linguagem natural e fluida em português do Brasil, como se estivesse falando ao vivo.
- NÃO use markdown, asteriscos, hashtags, underlines ou qualquer formatação de texto.
- Escreva APENAS texto simples, sem símbolos especiais.
- Máximo de 120 palavras.

Título: {title}
Conteúdo: {content}

Resumo (somente texto simples):"""
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        print(f"Erro ao gerar resumo com Gemini: {e}")
        return None


def summarize_news_batch(items, _retry=True):
    """
    Resume múltiplas notícias em uma única chamada de API.
    Tenta Gemini primeiro; se o limite diário for atingido, cai para Groq.
    Retorna list de str na mesma ordem de items; None para falhas.
    """
    if not items:
        return []

    prompt = _build_batch_prompt(items)
    n = len(items)

    # --- Gemini ---
    if gemini_key:
        try:
            summaries = _call_gemini_batch(prompt, n)
            print(f"  [Gemini] {n} resumos gerados.")
            return summaries
        except Exception as e:
            err = str(e)
            if '429' in err:
                if 'PerDay' in err or 'per_day' in err.lower():
                    print("  Limite DIÁRIO Gemini atingido — usando Groq como fallback...")
                    # não retorna aqui: cai para o bloco Groq abaixo
                elif _retry:
                    m = re.search(r'retry_delay\s*\{\s*seconds:\s*(\d+)', err)
                    wait = int(m.group(1)) + 5 if m else 65
                    print(f"  Rate limit Gemini (por minuto). Aguardando {wait}s...")
                    time.sleep(wait)
                    return summarize_news_batch(items, _retry=False)
                else:
                    print(f"  Erro Gemini (retry esgotado): {e}")
            else:
                print(f"  Erro Gemini: {e}")

    # --- Groq (fallback ou primário se Gemini não configurado) ---
    if groq_key:
        try:
            summaries = _call_groq_batch(prompt, n)
            print(f"  [Groq] {n} resumos gerados.")
            return summaries
        except Exception as e:
            print(f"  Erro Groq: {e}")

    print("  Nenhum provedor disponível. Usando fallback (título).")
    return [None] * n


if __name__ == "__main__":
    test_title = "Cientistas descobrem nova espécie de orquídea na Amazônia"
    test_content = "Uma expedição de botânicos brasileiros e estrangeiros identificou uma nova espécie de orquídea no coração da floresta amazônica. A planta apresenta cores vibrantes e um formato único que atraiu a atenção dos pesquisadores durante uma trilha de mapeamento."
    print(summarize_news("Ciência", test_title, test_content))
