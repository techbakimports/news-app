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


_VALID_CATEGORIES = {"Política", "Esporte", "Entretenimento", "Mercado Financeiro", "Tecnologia", "Policial", "Celebridades"}


def select_most_relevant(
    category: str,
    candidates: list[dict],
    trending: dict | None = None,
) -> dict:
    """
    Dado um pool de notícias candidatas para uma categoria, usa LLM para
    escolher a mais relevante/importante do dia.

    Cadeia: Groq (primário) → Gemini (fallback) → primeiro item (fallback final).

    Args:
        category:   nome da categoria (ex: "Política")
        candidates: lista de dicts com pelo menos "title" e "source"
        trending:   dict retornado por trends.get_trending_topics() — opcional.
                    Quando fornecido, o LLM considera o que está em alta nas
                    redes sociais ao escolher a notícia.

    Retorna o item escolhido (dict completo do candidato).
    """
    if len(candidates) == 1:
        return candidates[0]

    titulos = "\n".join(
        f"{i+1}. [{item.get('source', '?')}] {item['title']}"
        for i, item in enumerate(candidates)
    )

    # Monta bloco de contexto de trending se disponível
    trending_ctx = ""
    if trending:
        tw  = trending.get("twitter", [])[:15]
        gg  = trending.get("google",  [])[:10]
        kws = trending.get("keywords", [])[:20]
        partes = []
        if tw:
            partes.append(f"Twitter BR: {', '.join(tw)}")
        if gg:
            partes.append(f"Google BR: {', '.join(gg)}")
        if kws:
            partes.append(f"Keywords em alta: {', '.join(kws)}")
        if partes:
            trending_ctx = (
                "\n\nCONTEXTO — O que está em alta nas redes sociais agora:\n"
                + "\n".join(partes)
                + "\n\nUse esse contexto para PRIORIZAR notícias cujo assunto "
                "coincide com o que está sendo debatido. Se houver empate "
                "de relevância, prefira a que está em trending."
            )

    prompt = (
        f"Você é um editor de notícias brasileiro. "
        f"Da lista abaixo de notícias da categoria '{category}', "
        f"escolha a MAIS RELEVANTE e IMPORTANTE para o público geral hoje.\n\n"
        f"Critérios (por ordem de prioridade):\n"
        f"1. Assunto em alta nas redes sociais (veja contexto abaixo)\n"
        f"2. Impacto direto na vida das pessoas\n"
        f"3. Novidade real (breaking news, não continuação de assunto antigo)\n"
        f"4. Abrangência nacional (não só local)\n"
        f"5. Interesse jornalístico genuíno (não clickbait)\n"
        f"{trending_ctx}\n\n"
        f"Notícias:\n{titulos}\n\n"
        f"Responda APENAS com o número da notícia escolhida (ex: 3). "
        f"Sem explicação, sem texto adicional."
    )

    def _parse_index(text: str) -> int | None:
        """Extrai índice 1-based da resposta do LLM."""
        text = text.strip()
        import re as _re
        m = _re.search(r"\b([1-9]\d*)\b", text)
        if m:
            idx = int(m.group(1))
            if 1 <= idx <= len(candidates):
                return idx - 1  # converte para 0-based
        return None

    # 1) Groq primário
    if groq_key:
        try:
            from groq import Groq
            client = Groq(api_key=groq_key)
            resp = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,  # determinístico — queremos julgamento consistente
                max_tokens=10,
            )
            idx = _parse_index(resp.choices[0].message.content)
            if idx is not None:
                chosen = candidates[idx]
                print(f"  [Groq] Mais relevante em '{category}': [{idx+1}] {chosen['title'][:60]}")
                return chosen
        except Exception as e:
            print(f"  [select_most_relevant] Groq falhou: {e}. Tentando Gemini...")

    # 2) Gemini fallback
    try:
        client = _get_gemini_client()
        if client:
            response = client.models.generate_content(model="gemini-2.0-flash", contents=prompt)
            idx = _parse_index(response.text)
            if idx is not None:
                chosen = candidates[idx]
                print(f"  [Gemini] Mais relevante em '{category}': [{idx+1}] {chosen['title'][:60]}")
                return chosen
    except Exception as e:
        print(f"  [select_most_relevant] Gemini falhou: {e}.")

    # 3) Fallback final — primeira da lista (comportamento anterior)
    print(f"  [select_most_relevant] LLM indisponível — usando primeira candidata de '{category}'")
    return candidates[0]


def select_top_n_relevant(
    category: str,
    candidates: list[dict],
    n: int,
    trending: dict | None = None,
) -> list[dict]:
    """
    Seleciona os N itens mais relevantes de uma lista de candidatos para uma
    categoria usando LLM (Groq → Gemini → fallback).

    Retorna lista ordenada por relevância (mais relevante primeiro).
    Se N >= len(candidates), retorna todos.
    """
    if len(candidates) <= n:
        return candidates

    import time as _time
    now_ts = _time.time()

    def _age_label(item: dict) -> str:
        pp = item.get("_published_parsed")
        if not pp:
            return "?"
        age_h = (now_ts - _time.mktime(pp)) / 3600
        if age_h < 1:
            return f"{int(age_h * 60)}min atrás"
        return f"{age_h:.1f}h atrás"

    titulos = "\n".join(
        f"{i+1}. [{item.get('source', '?')}] [{_age_label(item)}] {item['title']}"
        for i, item in enumerate(candidates)
    )

    trending_ctx = ""
    if trending:
        tw  = trending.get("twitter", [])[:15]
        gg  = trending.get("google",  [])[:10]
        kws = trending.get("keywords", [])[:20]
        partes = []
        if tw:
            partes.append(f"Twitter BR: {', '.join(tw)}")
        if gg:
            partes.append(f"Google BR: {', '.join(gg)}")
        if kws:
            partes.append(f"Keywords em alta: {', '.join(kws)}")
        if partes:
            trending_ctx = (
                "\n\nCONTEXTO — O que está em alta nas redes sociais agora:\n"
                + "\n".join(partes)
                + "\n\nUse esse contexto para PRIORIZAR notícias cujo assunto "
                "coincide com o que está sendo debatido nas redes."
            )

    prompt = (
        f"Você é um editor de conteúdo digital brasileiro especializado em '{category}'. "
        f"Da lista abaixo, escolha as {n} MELHORES para viralizar como Short hoje.\n\n"
        f"Critérios com PESO IGUAL (combine recência e relevância):\n"
        f"RECÊNCIA   — prefira notícias publicadas há menos tempo (tempo entre colchetes)\n"
        f"RELEVÂNCIA — assunto em alta nas redes, impacto amplo, breaking news, potencial viral\n"
        f"DESCARTE   — notícias antigas (>6h) só entram se forem muito mais relevantes que as recentes\n"
        f"{trending_ctx}\n\n"
        f"Notícias (formato: número. [fonte] [idade] título):\n{titulos}\n\n"
        f"Responda APENAS com os {n} números separados por vírgula, em ordem decrescente de qualidade "
        f"(melhor primeiro). Ex: 3,1,5. Sem explicação, sem texto adicional."
    )

    def _parse_indices(text: str) -> list[int] | None:
        nums = re.findall(r"\b([1-9]\d*)\b", text.strip())
        indices = []
        seen = set()
        for s in nums:
            idx = int(s) - 1
            if 0 <= idx < len(candidates) and idx not in seen:
                seen.add(idx)
                indices.append(idx)
        return indices if indices else None

    # 1) Groq primário
    if groq_key:
        try:
            from groq import Groq
            client = Groq(api_key=groq_key)
            resp = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                max_tokens=30,
            )
            indices = _parse_indices(resp.choices[0].message.content)
            if indices:
                result = [candidates[i] for i in indices[:n]]
                titles = " | ".join(c["title"][:40] for c in result)
                print(f"  [Groq] Top {n} em '{category}': {titles}")
                return result
        except Exception as e:
            print(f"  [select_top_n_relevant] Groq falhou: {e}. Tentando Gemini...")

    # 2) Gemini fallback
    try:
        client = _get_gemini_client()
        if client:
            response = client.models.generate_content(model="gemini-2.0-flash", contents=prompt)
            indices = _parse_indices(response.text)
            if indices:
                result = [candidates[i] for i in indices[:n]]
                titles = " | ".join(c["title"][:40] for c in result)
                print(f"  [Gemini] Top {n} em '{category}': {titles}")
                return result
    except Exception as e:
        print(f"  [select_top_n_relevant] Gemini falhou: {e}.")

    # 3) Fallback — primeiros N por ordem de chegada (mais recentes)
    print(f"  [select_top_n_relevant] LLM indisponível — usando primeiros {n} de '{category}'")
    return candidates[:n]


def summarize_news_for_short(category: str, title: str, content: str) -> tuple[str, str] | None:
    """
    Gera narração longa (~350-400 palavras, ~3 min) pra Short de notícia.
    Cadeia: Groq (primário) → Gemini (fallback) → None.

    Retorna (narracao, categoria_corrigida) ou None se nenhum LLM funcionar.
    """
    cats_str = ", ".join(sorted(_VALID_CATEGORIES))

    # Tom do narrador varia por categoria
    if category == "Celebridades":
        persona = (
            "Você é uma apresentadora animada de programa de entretenimento brasileiro, "
            "narrando uma fofoca dos famosos em formato Short para YouTube."
        )
        tom_extra = (
            "- Tom: leve, divertido, animado — como fofoca entre amigas, mas sem difamar\n"
            "- Use linguagem coloquial brasileira (pode usar 'gente', 'olha', 'imagina')\n"
            "- Termine com comentário leve que convide a opinar nos comentários\n"
            "  (ex: 'E você, o que acha? Comenta aqui embaixo!')\n"
        )
    else:
        persona = "Você é um jornalista narrando notícia em formato YouTube Shorts."
        tom_extra = (
            "- Português do Brasil, tom natural de podcast/Shorts (não engessado)\n"
            "- Termine com uma reflexão sobre o impacto da notícia ou desdobramento esperado\n"
        )

    prompt = (
        f"{persona}\n"
        f"Título: {title}\n"
        f"Conteúdo bruto da notícia (use como base):\n{content[:3000]}\n\n"
        "REGRAS OBRIGATÓRIAS:\n"
        f"1. PRIMEIRA LINHA da resposta: indique a categoria CORRETA dentre: {cats_str}.\n"
        f"   A categoria sugerida foi '{category}', mas pode estar ERRADA.\n"
        f"   Formato: CATEGORIA: <nome exato da lista>\n\n"
        "2. DEPOIS, escreva a narração:\n"
        "- Comece com uma frase de IMPACTO (gancho — fato mais surpreendente)\n"
        "- Texto entre 350 e 400 palavras (~150-160s de fala — próximo ao limite de 3 min)\n"
        "- Cobertura COMPLETA: o que aconteceu, quem, quando, onde, por quê, desdobramento\n"
        "- NÃO deixe escapar nenhum aspecto importante\n"
        "- NÃO use markdown, asteriscos, hashtags, símbolos ou listas\n"
        "- NÃO mencione 'agora em [categoria]' ou similar — vá direto ao assunto\n"
        f"{tom_extra}"
    )

    def _parse(text: str) -> tuple[str, str]:
        lines = text.strip().splitlines()
        corrected = category
        start = 0
        if lines and lines[0].upper().startswith("CATEGORIA:"):
            raw = lines[0].split(":", 1)[1].strip()
            for valid in _VALID_CATEGORIES:
                if raw.lower() == valid.lower():
                    corrected = valid
                    break
            start = 1
            while start < len(lines) and not lines[start].strip():
                start += 1
        narration = "\n".join(lines[start:]).strip()
        return narration, corrected

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
            narration, corrected = _parse(resp.choices[0].message.content)
            print(f"  [Groq] Short de notícia gerado ({len(narration.split())} palavras)")
            return narration, corrected
        except Exception as e:
            print(f"  Groq falhou: {e}. Tentando Gemini...")

    # 2) Gemini fallback
    if gemini_key:
        try:
            client = _get_gemini_client()
            response = client.models.generate_content(model="gemini-2.0-flash", contents=prompt)
            narration, corrected = _parse(response.text)
            print(f"  [Gemini fallback] Short de notícia gerado ({len(narration.split())} palavras)")
            return narration, corrected
        except Exception as e:
            print(f"  Gemini também falhou: {e}")

    print("  ❌ Nenhum LLM disponível pra gerar Short de notícia.")
    return None


if __name__ == "__main__":
    test_title = "Cientistas descobrem nova espécie de orquídea na Amazônia"
    test_content = "Uma expedição de botânicos brasileiros e estrangeiros identificou uma nova espécie de orquídea no coração da floresta amazônica. A planta apresenta cores vibrantes e um formato único que atraiu a atenção dos pesquisadores durante uma trilha de mapeamento."
    print(summarize_news("Ciência", test_title, test_content))
