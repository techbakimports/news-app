import os
import re
import time
from dotenv import load_dotenv

load_dotenv()

gemini_key = os.getenv("GEMINI_API_KEY")
groq_key = os.getenv("GROQ_API_KEY")

if not gemini_key:
    print("AVISO: GEMINI_API_KEY não encontrada no arquivo .env")

_gemini_client = None

def _get_gemini_client():
    global _gemini_client
    if _gemini_client is None and gemini_key:
        from google import genai
        _gemini_client = genai.Client(api_key=gemini_key)
    return _gemini_client

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
    client = _get_gemini_client()
    response = client.models.generate_content(model="gemini-2.0-flash", contents=prompt)
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
        client = _get_gemini_client()
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
        response = client.models.generate_content(model="gemini-2.0-flash", contents=prompt)
        return response.text.strip()
    except Exception as e:
        print(f"Erro ao gerar resumo com Gemini: {e}")
        return None


def summarize_news_batch(items, _retry=True):
    """
    Resume múltiplas notícias em uma única chamada de API.
    Cadeia: Groq (primário) → Gemini (fallback) → None.
    Retorna list de str na mesma ordem de items; None para falhas.
    """
    if not items:
        return []

    prompt = _build_batch_prompt(items)
    n = len(items)

    # --- Groq (primário) — free tier 14.400 req/dia ---
    if groq_key:
        try:
            summaries = _call_groq_batch(prompt, n)
            print(f"  [Groq] {n} resumos gerados.")
            return summaries
        except Exception as e:
            print(f"  Erro Groq: {e}. Tentando Gemini como fallback...")

    # --- Gemini (fallback) ---
    if gemini_key:
        try:
            summaries = _call_gemini_batch(prompt, n)
            print(f"  [Gemini fallback] {n} resumos gerados.")
            return summaries
        except Exception as e:
            err = str(e)
            if '429' in err:
                if 'PerDay' in err or 'per_day' in err.lower():
                    print("  Gemini também esgotou cota diária.")
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

    print("  Nenhum provedor disponível. Usando fallback (título).")
    return [None] * n


def summarize_news_for_short(category: str, title: str, content: str) -> str | None:
    """
    Gera narração longa (~350-400 palavras, ~3 min) pra Short de notícia.
    Cadeia: Groq (primário) → Gemini (fallback) → None.

    Diferente de summarize_news_batch (que gera resumos curtos de ~120 palavras
    pra vídeo longo): aqui geramos texto denso pra cada categoria virar 1 Short.
    """
    prompt = (
        "Você é um jornalista narrando notícia em formato Shorts (TikTok/YouTube).\n"
        f"Categoria: {category}\n"
        f"Título: {title}\n"
        f"Conteúdo bruto da notícia (use como base):\n{content[:3000]}\n\n"
        "REGRAS OBRIGATÓRIAS:\n"
        "- Comece com uma frase de IMPACTO (gancho da notícia — fato mais surpreendente)\n"
        "- Texto entre 350 e 400 palavras (~150-160s de fala — próximo ao limite de 3 min)\n"
        "- Cobertura COMPLETA: o que aconteceu, quem, quando, onde, por quê, e qual o impacto/desdobramento\n"
        "- NÃO deixe escapar nenhum aspecto importante — é melhor explicar bem 1 notícia "
        "do que tocar superficialmente em vários pontos\n"
        "- NÃO use markdown, asteriscos, hashtags, símbolos ou listas\n"
        "- Português do Brasil, tom natural de podcast/Shorts (não engessado)\n"
        "- Termine com uma reflexão sobre o impacto da notícia ou desdobramento esperado\n"
        "- NÃO mencione 'agora em [categoria]' ou similar — vá direto ao assunto\n\n"
        "Resposta (apenas o texto narrado, sem prefixos):"
    )

    # 1) Groq primário
    if groq_key:
        try:
            from groq import Groq
            client = Groq(api_key=groq_key)
            resp = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
            )
            text = resp.choices[0].message.content.strip()
            print(f"  [Groq] Short de notícia gerado ({len(text.split())} palavras)")
            return text
        except Exception as e:
            print(f"  Groq falhou: {e}. Tentando Gemini...")

    # 2) Gemini fallback
    if gemini_key:
        try:
            client = _get_gemini_client()
            response = client.models.generate_content(model="gemini-2.0-flash", contents=prompt)
            text = response.text.strip()
            print(f"  [Gemini fallback] Short de notícia gerado ({len(text.split())} palavras)")
            return text
        except Exception as e:
            print(f"  Gemini também falhou: {e}")

    print("  ❌ Nenhum LLM disponível pra gerar Short de notícia.")
    return None


if __name__ == "__main__":
    test_title = "Cientistas descobrem nova espécie de orquídea na Amazônia"
    test_content = "Uma expedição de botânicos brasileiros e estrangeiros identificou uma nova espécie de orquídea no coração da floresta amazônica. A planta apresenta cores vibrantes e um formato único que atraiu a atenção dos pesquisadores durante uma trilha de mapeamento."
    print(summarize_news("Ciência", test_title, test_content))
