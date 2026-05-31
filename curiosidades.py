"""
Pipeline de Curiosidades — gera 1 Short com curiosidade aleatória gerada por Gemini.

Fluxo: Gemini sorteia tema + redige curiosidade -> generate_short_from_text -> YouTube + TikTok

Uso:
    python curiosidades.py
    python curiosidades.py --sem-upload
    python curiosidades.py --privado
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import random
import sys
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

from config import AUDIO_OUTPUT_DIR

# -- Logging -------------------------------------------------------------------

_LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
os.makedirs(_LOG_DIR, exist_ok=True)
_log_file = os.path.join(_LOG_DIR, "curiosidades.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.FileHandler(_log_file, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)
_orig_print = print
def print(*args, **kwargs):  # noqa: A001
    _orig_print(*args, **kwargs)
    msg = " ".join(str(a) for a in args)
    if msg.strip():
        log.info(msg)

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


# -- Flags ---------------------------------------------------------------------

YOUTUBE_UPLOAD = True
YOUTUBE_PUBLISH_NOW = True

# Plataformas-alvo (modificadas via CLI args)
POST_YOUTUBE = True
POST_TIKTOK = True

# Histórico de temas pra evitar repetição
_HISTORY_FILE = os.path.join(_LOG_DIR, "curiosidades_history.json")
_HISTORY_MAX = 30  # últimas 30 curiosidades pra Gemini evitar


# -- Histórico de temas --------------------------------------------------------

def _load_history() -> list[dict]:
    """Carrega histórico recente de curiosidades geradas."""
    if not os.path.exists(_HISTORY_FILE):
        return []
    try:
        with open(_HISTORY_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data[-_HISTORY_MAX:] if isinstance(data, list) else []
    except Exception:
        return []


def _save_history(entry: dict) -> None:
    """Adiciona uma entrada ao histórico."""
    history = _load_history()
    history.append(entry)
    history = history[-_HISTORY_MAX:]
    try:
        with open(_HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


# -- Universo de temas (Gemini escolhe aleatório dentro do universo) -----------

_TEMA_POOLS = [
    "ciência (física, química, biologia, matemática)",
    "história antiga (Egito, Roma, Grécia, civilizações perdidas)",
    "história moderna (séculos XVI a XX)",
    "espaço e astronomia (planetas, estrelas, buracos negros, exploração espacial)",
    "animais e natureza (comportamento, recordes, espécies raras)",
    "corpo humano (anatomia, evolução, capacidades surpreendentes)",
    "tecnologia histórica (invenções que mudaram o mundo)",
    "geografia e geologia (lugares extremos, formações naturais)",
    "linguagem e cultura (idiomas, mitologia, tradições)",
    "psicologia e comportamento (mente humana, ilusões, vieses)",
    "música e arte (curiosidades sobre obras, compositores, movimentos)",
    "alimentação (origem de pratos, alimentos raros, história gastronômica)",
    "esportes (recordes inacreditáveis, histórias bizarras)",
    "cinema e literatura (bastidores, curiosidades de obras famosas)",
    "economia e finanças (eventos históricos, paradoxos econômicos)",
]


# -- Geração da curiosidade ----------------------------------------------------

def _parse_curiosidade_json(text: str) -> dict | None:
    """Limpa wrapper markdown e parseia JSON da resposta."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1]) if len(lines) >= 3 else text.replace("```", "")
        text = text.strip()
        if text.startswith("json"):
            text = text[4:].strip()
    try:
        data = json.loads(text)
        return {
            "tema": data.get("tema_escolhido", "Curiosidade"),
            "titulo": data.get("titulo", "").strip(),
            "narracao": data.get("curiosidade", "").strip(),
        }
    except json.JSONDecodeError:
        return None


async def _gerar_curiosidade() -> dict | None:
    """
    Gera 1 curiosidade num tema aleatório.
    Cadeia: Groq (JSON mode, primário) → Gemini (fallback) → None.
    Retorna dict {tema, titulo, narracao} ou None se falhar.
    """
    # Sorteia 3 temas pra LLM escolher (ajuda a variar)
    temas_sorteados = random.sample(_TEMA_POOLS, k=3)

    # Recupera histórico pra evitar repetição
    history = _load_history()
    historicos_recentes = [h.get("titulo", "") for h in history[-15:] if h.get("titulo")]
    historico_texto = ""
    if historicos_recentes:
        historico_texto = (
            "\n\nIMPORTANTE — Já geramos estas curiosidades recentemente, "
            "NÃO repita o mesmo assunto nem nada parecido:\n"
            + "\n".join(f"- {t}" for t in historicos_recentes)
        )

    prompt = (
        "Você é um roteirista de Shorts especializado em curiosidades virais. "
        "Sua missão: produzir UMA curiosidade SURPREENDENTE e POUCO CONHECIDA, "
        "que prenda a atenção do começo ao fim.\n\n"
        f"Escolha UM destes temas (escolha o que renderá a curiosidade mais inesperada):\n"
        f"  1. {temas_sorteados[0]}\n"
        f"  2. {temas_sorteados[1]}\n"
        f"  3. {temas_sorteados[2]}\n\n"
        "Regras OBRIGATÓRIAS para a curiosidade:\n"
        "- Comece com uma frase de IMPACTO IMEDIATO (gancho viral)\n"
        "- Texto entre 350 e 400 palavras (~150-160s de fala — próximo ao limite de 3 min do Shorts)\n"
        "- Cobertura COMPLETA da curiosidade: contexto, fato principal, detalhes "
        "fundamentais (datas, nomes, números), causas/consequências e desfecho. "
        "NÃO deixe escapar nenhum aspecto importante — é melhor explicar bem 1 curiosidade "
        "do que tocar em vários sem aprofundar.\n"
        "- NÃO use markdown, asteriscos, hashtags ou símbolos\n"
        "- Português do Brasil, tom natural de podcast/Shorts\n"
        "- Termine com uma frase reflexiva, surpreendente ou que estimule curiosidade\n"
        "- Inclua fatos verificáveis (datas, nomes, números reais)\n"
        "- EVITE clichês supercitados (Marie Curie, Einstein, Mariana Trench, etc)"
        f"{historico_texto}\n\n"
        "Responda APENAS com este JSON (sem markdown, sem ```):\n"
        '{\n'
        '  "tema_escolhido": "qual tema você escolheu",\n'
        '  "titulo": "Título curto e clicável de até 80 caracteres",\n'
        '  "curiosidade": "Texto completo da curiosidade pra ser narrado"\n'
        '}'
    )

    print(f"  Temas oferecidos: {temas_sorteados}")
    print(f"  Histórico carregado: {len(historicos_recentes)} curiosidades anteriores")

    # 1) Groq (primário) — usa JSON mode pra garantir estrutura
    groq_key = os.environ.get("GROQ_API_KEY", "")
    if not groq_key or groq_key == "cole_sua_chave_aqui":
        print("  ⚠️  GROQ_API_KEY ausente ou placeholder no .env — pulando Groq")
    else:
        try:
            from groq import Groq
            client = Groq(api_key=groq_key)
            print(f"  [Groq] tentando llama-3.3-70b-versatile...")
            resp = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0.9,  # mais criativo
            )
            text = resp.choices[0].message.content
            result = _parse_curiosidade_json(text)
            if result and result["titulo"] and result["narracao"]:
                print(f"  [Groq] ✅ curiosidade gerada.")
                return result
            print(f"  [Groq] retornou JSON incompleto. Tentando Gemini...")
        except ImportError:
            print(f"  ⚠️  Pacote 'groq' não instalado — pip install groq")
        except Exception as e:
            print(f"  [Groq] falhou: {e}. Tentando Gemini...")

    # 2) Gemini (fallback)
    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    if not gemini_key:
        print("  ⚠️  GEMINI_API_KEY ausente — sem fallback")
    else:
        try:
            from google import genai
            client = genai.Client(api_key=gemini_key)
            print(f"  [Gemini] tentando gemini-2.0-flash...")
            resp = client.models.generate_content(
                model="gemini-2.0-flash",
                contents=prompt,
            )
            result = _parse_curiosidade_json(resp.text)
            if result and result["titulo"] and result["narracao"]:
                print(f"  [Gemini fallback] ✅ curiosidade gerada.")
                return result
            print(f"  [Gemini] retornou JSON incompleto.")
        except Exception as e:
            print(f"  [Gemini] também falhou: {e}")

    print("  ❌ Nenhum provedor conseguiu gerar curiosidade.")
    return None


# -- Pipeline principal --------------------------------------------------------

async def run_curiosidade(on_progress=None):
    """
    Pipeline Curiosidades — gera 1 Short com curiosidade aleatória.
    Retorna o video_id do YouTube ou None se falhar.
    """
    print(f"--- Curiosidade Pipeline: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')} ---")

    privacy = "public" if YOUTUBE_PUBLISH_NOW else "private"

    # 1. Gera curiosidade via Groq (primário) → Gemini (fallback)
    print("\n[1/2] Gerando curiosidade (Groq → Gemini)...")
    if on_progress:
        try:
            await on_progress("Gerando curiosidade (Groq → Gemini)...")
        except Exception:
            pass

    curiosidade = await _gerar_curiosidade()
    if not curiosidade or not curiosidade.get("titulo") or not curiosidade.get("narracao"):
        print("Falha ao gerar curiosidade. Abortando.")
        from telegram_notifier import notify
        notify("❌ <b>Curiosidades:</b> falha ao gerar conteúdo (Groq + Gemini falharam).")
        return None

    print(f"  Tema: {curiosidade['tema']}")
    print(f"  Título: {curiosidade['titulo']}")
    print(f"  Narração: {len(curiosidade['narracao'].split())} palavras")

    # Adiciona CTA fixo no final — convite pra curtir, compartilhar e se inscrever
    cta = (
        " Curtiu essa curiosidade? Então deixa o seu like, "
        "compartilha com quem precisa saber disso, "
        "e se inscreve no canal pra mais curiosidades como essa todo dia."
    )
    curiosidade["narracao"] = curiosidade["narracao"].rstrip() + cta
    print(f"  + CTA: narração final {len(curiosidade['narracao'].split())} palavras")

    # Salva no histórico
    _save_history({
        "ts": datetime.now().isoformat(),
        "tema": curiosidade["tema"],
        "titulo": curiosidade["titulo"],
    })

    # 2. Gera Short
    print("\n[2/2] Gerando Short...")
    if on_progress:
        try:
            await on_progress("Gerando vídeo Short...")
        except Exception:
            pass

    from shorts import generate_short_from_text

    common_args = dict(
        title=curiosidade["titulo"],
        narration=curiosidade["narracao"],
        category="Curiosidade",
        source="Curiosidade do dia",
        privacy=privacy,
        hashtags=["Shorts", "Curiosidade", "VoceSabia", "Fatos", "Aprender"],
        playlist_key="curiosidades",
        instagram_enabled=False,
        youtube_enabled=POST_YOUTUBE,
        tiktok_enabled=POST_TIKTOK,
    )

    plataformas = []
    if POST_YOUTUBE: plataformas.append("YouTube")
    if POST_TIKTOK: plataformas.append("TikTok")
    print(f"  Plataformas: {' + '.join(plataformas) if plataformas else 'NENHUMA'}")

    if not YOUTUBE_UPLOAD:
        try:
            path = await generate_short_from_text(upload=False, **common_args)
            print(f"\nVídeo local: {path}")
        except Exception as e:
            print(f"Erro ao gerar vídeo: {e}")
        return None

    try:
        video_id = await generate_short_from_text(upload=True, **common_args)
    except Exception as e:
        print(f"Erro no upload: {e}")
        from telegram_notifier import notify
        notify(f"❌ <b>Curiosidade:</b> erro no upload — {e}")
        return None

    from telegram_notifier import notify
    if video_id:
        print(f"\nCuriosidade publicada: https://youtu.be/{video_id}")
        notify(
            f"✅ <b>Curiosidade postada!</b>\n"
            f"<i>{curiosidade['tema']}</i>\n"
            f"{curiosidade['titulo']}\n"
            f"Plataformas: {' + '.join(plataformas)}\n"
            f"https://youtu.be/{video_id}"
        )
    elif POST_TIKTOK and not POST_YOUTUBE:
        # Só TikTok — sem video_id do YouTube, mas pode ter ido pro TikTok
        print(f"\nCuriosidade postada (TikTok only)")
        notify(
            f"✅ <b>Curiosidade postada no TikTok!</b>\n"
            f"<i>{curiosidade['tema']}</i>\n"
            f"{curiosidade['titulo']}"
        )
    return video_id


# -- Entry point ---------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog="curiosidades.py", add_help=True)
    parser.add_argument("--sem-upload", action="store_true", help="só gera, sem upload em nenhuma plataforma")
    parser.add_argument("--privado", action="store_true", help="publica como privado no YouTube")
    parser.add_argument("--apenas-youtube", action="store_true", help="publica SOMENTE no YouTube")
    parser.add_argument("--apenas-tiktok", action="store_true", help="publica SOMENTE no TikTok")
    args, _ = parser.parse_known_args()

    if args.sem_upload:
        YOUTUBE_UPLOAD = False
    if args.apenas_youtube:
        POST_TIKTOK = False
    if args.apenas_tiktok:
        POST_YOUTUBE = False
    if args.privado:
        YOUTUBE_PUBLISH_NOW = False

    asyncio.run(run_curiosidade())
