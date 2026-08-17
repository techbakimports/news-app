"""
Gerador de config de nicho via LLM.
Transforma descrição livre do usuário em config JSON de pipeline.
"""
import json
import os
import re
from config import GROQ_MODEL, GEMINI_MODEL

_PROMPT = """\
Você vai configurar um pipeline de Shorts para YouTube.
O usuário descreveu o nicho como: "{description}"

Gere um JSON com a configuração:
- "name": nome curto do nicho (2-4 palavras)
- "queries": lista de 4-6 queries para buscar no Google News RSS em português do Brasil
- "categories": lista de 2-4 categorias para organizar o conteúdo
- "voice": escolha entre pt-BR-AntonioNeural / pt-BR-FranciscaNeural / pt-BR-ThalitaNeural
- "tone": como a narração deve soar (ex: "informativo e descontraído")
- "max_shorts": quantos Shorts gerar por execução (1 a 5)
- "color_hex": cor hex que representa o nicho visualmente (ex: "#4ADE80")

Responda APENAS com o JSON, sem explicações, sem markdown.
"""


def generate_nicho_config(description: str) -> dict:
    """Groq → Gemini: gera config de pipeline a partir de descrição livre."""
    prompt = _PROMPT.format(description=description)

    groq_key = os.getenv("GROQ_API_KEY")
    if groq_key:
        try:
            from groq import Groq
            resp = Groq(api_key=groq_key).chat.completions.create(
                model=GROQ_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.4,
            )
            return _parse(resp.choices[0].message.content)
        except Exception as e:
            print(f"  [nicho_generator] Groq falhou: {e}")

    gemini_key = os.getenv("GEMINI_API_KEY")
    if gemini_key:
        try:
            from google import genai
            client = genai.Client(api_key=gemini_key)
            resp = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
            return _parse(resp.text)
        except Exception as e:
            print(f"  [nicho_generator] Gemini falhou: {e}")

    raise RuntimeError("Nenhum LLM disponível para gerar o nicho.")


def _parse(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```[a-z]*\n?", "", text)
    text = re.sub(r"\n?```$", "", text)
    return json.loads(text.strip())
