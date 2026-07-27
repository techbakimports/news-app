"""
Pipeline de Produtos/Afiliados — gera Shorts de ofertas via Shopee/Lomadee Affiliate API.

Fluxo: Shopee/Lomadee (produtos com desconto) -> seleção heurística (desconto × comissão)
       -> Groq/Gemini gera copy de venda (com divulgação de publicidade obrigatória)
       -> generate_short_from_text (imagem do produto + badge de preço) -> YouTube + TikTok

IMPORTANTE — CONFORMIDADE: desde o guia CONAR de maio/2026, conteúdo remunerado por
performance (link de afiliado) precisa de divulgação CLARA como publicidade — não bastam
termos vagos como "parceria". Este pipeline inclui a divulgação na narração falada, na
tela (badge) e na descrição do vídeo em todo Short gerado, sem exceção.

Uso:
    python afiliados.py
    python afiliados.py --sem-upload
    python afiliados.py --privado
    python afiliados.py --max 3
"""
from __future__ import annotations

import argparse
import asyncio
import logging
from logging.handlers import RotatingFileHandler
import os
import sys
from datetime import datetime
from dotenv import load_dotenv

os.chdir(os.path.dirname(os.path.abspath(__file__)))
load_dotenv()


# -- Logging -------------------------------------------------------------------

_LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
os.makedirs(_LOG_DIR, exist_ok=True)
_log_file = os.path.join(_LOG_DIR, "afiliados.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        RotatingFileHandler(_log_file, maxBytes=5*1024*1024, backupCount=0, encoding="utf-8"),
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

YOUTUBE_UPLOAD       = True
YOUTUBE_PUBLISH_NOW  = True
TIKTOK_UPLOAD        = True   # posta como rascunho (SELF_ONLY) até o app TikTok ser aprovado
MAX_PRODUTOS_PER_RUN = 3


# -- CTA / divulgação obrigatória ------------------------------------------------

# Falado no fim de todo vídeo — nunca omitir, é a divulgação de publicidade exigida
# pelo guia CONAR 2026 pra conteúdo remunerado por performance (link de afiliado).
_CTA = (
    " Publicidade — este vídeo contém link de afiliado, "
    "e eu posso ganhar uma comissão se você comprar por ele. "
    "Aproveita a oferta antes que acabe, e se inscreve no canal pra mais promoções assim."
)

_DESC_DISCLOSURE = (
    "📢 PUBLICIDADE — este vídeo contém link de afiliado. "
    "Podemos receber comissão por compras feitas através dele."
)


def _price_text(product: dict) -> str:
    price = product.get("price", 0)
    original = product.get("original_price", price)
    discount = product.get("discount_pct", 0)
    if discount > 0 and original > price:
        return f"R$ {original:.2f} → R$ {price:.2f} (-{discount}%)".replace(".", ",")
    return f"R$ {price:.2f}".replace(".", ",")


# -- Narração com copy de venda (Groq → Gemini) --------------------------------

def _generate_product_narration(product: dict) -> str | None:
    """
    Gera narração de venda (~110-125 palavras, ~60s com CTA de divulgação).
    Cadeia: Groq (primário) → Gemini (fallback) → None.
    """
    groq_key   = os.getenv("GROQ_API_KEY", "")
    gemini_key = os.getenv("GEMINI_API_KEY", "")

    name = product.get("name", "produto")
    price_line = _price_text(product)

    prompt = (
        "Você é um apresentador de vídeos de ofertas/achados, narrando um produto "
        "em formato Short para YouTube — estilo 'achadinho', animado e convincente, "
        "mas sem exagero nem promessa falsa.\n\n"
        f"Produto: {name}\n"
        f"Preço: {price_line}\n\n"
        "REGRAS OBRIGATÓRIAS:\n"
        "- Comece direto destacando o principal benefício/uso do produto — sem apresentação\n"
        "- Mencione o preço/desconto pelo menos uma vez de forma natural na fala\n"
        "- Texto entre 100 e 120 palavras (o CTA de divulgação será adicionado depois)\n"
        "- Tom animado e prático, como quem recomenda um achado pra um amigo\n"
        "- NÃO invente características que não foram informadas — use só o nome do produto e o preço\n"
        "- NÃO use markdown, asteriscos, hashtags, símbolos ou listas\n"
        "- NÃO inclua o aviso de publicidade nem CTA de inscrição — serão adicionados automaticamente\n\n"
        "Responda APENAS com o texto da narração, sem título nem formatação."
    )

    if groq_key and groq_key not in ("", "cole_sua_chave_aqui"):
        try:
            from groq import Groq
            client = Groq(api_key=groq_key)
            resp = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.8,
            )
            text = resp.choices[0].message.content.strip()
            if text:
                print(f"  [Groq] narração gerada ({len(text.split())} palavras)")
                return text
        except Exception as e:
            print(f"  Groq falhou: {e}. Tentando Gemini...")

    if gemini_key and gemini_key not in ("", "cole_sua_chave_aqui"):
        try:
            from google import genai as google_genai
            client = google_genai.Client(api_key=gemini_key)
            response = client.models.generate_content(model="gemini-2.0-flash", contents=prompt)
            text = response.text.strip()
            if text:
                print(f"  [Gemini] narração gerada ({len(text.split())} palavras)")
                return text
        except Exception as e:
            print(f"  Gemini também falhou: {e}")

    print("  ❌ Nenhum LLM disponível para gerar narração de produto.")
    return None


# -- Pipeline principal --------------------------------------------------------

async def run_afiliados(on_progress=None, max_shorts: int | None = None) -> list[str]:
    """
    Pipeline Produtos/Afiliados — Shopee/Lomadee -> Groq/Gemini -> Shorts.
    Retorna lista de video_ids postados no YouTube.
    """
    from affiliate_sources import get_candidate_products, select_top_products

    print(f"--- Afiliados Pipeline: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')} ---")

    privacy = "public" if YOUTUBE_PUBLISH_NOW else "private"
    limite  = max_shorts or MAX_PRODUTOS_PER_RUN

    # 1. Buscar produtos candidatos
    print("\n[1/3] Buscando produtos (Shopee/Lomadee)...")
    if on_progress:
        try: await on_progress("Buscando produtos em oferta...")
        except Exception: pass

    raw_products = get_candidate_products(limit=20)
    if not raw_products:
        print("Nenhum produto encontrado (fontes não configuradas ou API indisponível). Abortando.")
        try:
            from telegram_notifier import notify
            notify("⚠️ <b>Afiliados:</b> nenhum produto encontrado — verifique credenciais Shopee/Lomadee.")
        except Exception:
            pass
        return []

    # Filtra produtos já postados nas últimas 48h (dedupe por nome)
    from history import filter_not_posted, mark_as_posted
    raw_products, n_skip = filter_not_posted(
        [{"title": p["name"], **p} for p in raw_products]
    )
    if n_skip:
        print(f"  {n_skip} produto(s) ignorado(s) — já postado(s) nas últimas 48h")

    if not raw_products:
        print("Todos os produtos já foram postados recentemente. Abortando.")
        return []

    products = select_top_products(raw_products, limite)
    print(f"  {len(raw_products)} candidatos → {len(products)} selecionados (desconto × comissão)")

    # 2. Gerar narração por produto
    print(f"\n[2/3] Gerando narração para {len(products)} produtos...")
    if on_progress:
        try: await on_progress(f"Gerando narração para {len(products)} produtos...")
        except Exception: pass

    produtos_com_narracao = []
    for i, product in enumerate(products, 1):
        print(f"  [{i}/{len(products)}] {product['name'][:70]}")
        narracao = _generate_product_narration(product)
        if narracao:
            product["narracao"] = narracao + _CTA
            produtos_com_narracao.append(product)
        else:
            print(f"    ⚠️  Sem narração — pulando")

    print(f"  Com narração: {len(produtos_com_narracao)} | pulados: {len(products) - len(produtos_com_narracao)}")

    if not produtos_com_narracao:
        msg = "❌ <b>Afiliados:</b> pipeline abortado — nenhuma narração gerada (Groq + Gemini falharam)."
        print(msg)
        try:
            from telegram_notifier import notify
            notify(msg)
        except Exception:
            pass
        return []

    # 3. Gerar Shorts
    print(f"\n[3/3] Gerando {len(produtos_com_narracao)} Shorts...")
    if on_progress:
        try: await on_progress(f"Gerando {len(produtos_com_narracao)} Shorts...")
        except Exception: pass

    from shorts import generate_short_from_text

    produto_hashtags = ["Shorts", "Achadinhos", "Promocao", "Publicidade", "Ofertas"]
    uploaded_ids: list[str] = []

    for i, product in enumerate(produtos_com_narracao, 1):
        print(f"\n  ── Short {i}/{len(produtos_com_narracao)} ──")
        name       = product["name"]
        narracao   = product["narracao"]
        source     = product.get("source", "afiliado").capitalize()
        price_line = _price_text(product)

        common_kwargs = dict(
            title=name,
            narration=narracao,
            category="Produtos",
            source=source,
            hashtags=produto_hashtags,
            playlist_key="produtos",
            instagram_enabled=False,
            link=product.get("affiliate_link"),
            voice=None,  # resolve via CATEGORY_VOICES / fallback
            display_text=narracao,
            image_url=product.get("image_url"),
            price_text=price_line,
            link_label="🛒 Compre aqui (link de afiliado):",
            extra_description=_DESC_DISCLOSURE,
        )

        if not YOUTUBE_UPLOAD:
            try:
                path = await generate_short_from_text(**common_kwargs, upload=False, privacy=privacy)
                print(f"  Vídeo local: {path}")
            except Exception as e:
                print(f"  Erro: {e}")
            continue

        # Verifica se vale a pena manter o arquivo local pro TikTok ANTES de renderizar,
        # pra não pagar o custo de manter/apagar arquivo à toa quando não há conexão.
        tiktok_connected = False
        if TIKTOK_UPLOAD:
            try:
                from tiktok_publisher import check_tiktok_token
                tiktok_connected, _tt_msg = check_tiktok_token()
                if not tiktok_connected:
                    print(f"    TikTok: pulado ({_tt_msg})")
            except Exception as e:
                print(f"    TikTok indisponível: {e}")

        captured_path: dict[str, str] = {}
        try:
            video_id = await generate_short_from_text(
                **common_kwargs,
                upload=True,
                privacy=privacy,
                keep_local=tiktok_connected,
                on_local_path=lambda p: captured_path.__setitem__("path", p),
            )
            if video_id:
                uploaded_ids.append(video_id)
                print(f"  ✅ https://youtu.be/{video_id}")
                mark_as_posted(name, pipeline="afiliados")
        except Exception as e:
            print(f"  Erro no Short {i}: {e}")

        # TikTok — reaproveita o MESMO arquivo renderizado acima (sem gerar de novo)
        if tiktok_connected:
            local_path = captured_path.get("path")
            if local_path and os.path.exists(local_path):
                try:
                    from tiktok_publisher import upload_video as tiktok_upload
                    caption = f"{name[:80]} {price_line} #Shorts #publicidade #achadinhos"
                    tiktok_upload(local_path, caption, public=False)
                except Exception as e:
                    print(f"    TikTok falhou: {e}")
                finally:
                    try:
                        os.remove(local_path)
                    except Exception:
                        pass

        # Espaçamento entre Shorts para não canibalizar o alcance no algoritmo
        if i < len(produtos_com_narracao):
            print(f"\n  ⏳ Aguardando 10 min antes do próximo Short ({i+1}/{len(produtos_com_narracao)})...")
            await asyncio.sleep(600)

    # Notificação final
    try:
        from telegram_notifier import notify
        if uploaded_ids:
            notify(
                f"✅ <b>Afiliados postado!</b>\n"
                f"{len(uploaded_ids)} Short(s) no ar.\n"
                f"Primeiro: https://youtu.be/{uploaded_ids[0]}"
            )
        elif YOUTUBE_UPLOAD:
            notify("⚠️ <b>Afiliados:</b> nenhum Short foi enviado ao YouTube.")
    except Exception:
        pass

    return uploaded_ids


# -- Entry point ---------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog="afiliados.py", add_help=True)
    parser.add_argument("--sem-upload", action="store_true", help="só gera, sem upload")
    parser.add_argument("--privado",    action="store_true", help="publica como privado")
    parser.add_argument("--max", type=int, default=None, help=f"máx Shorts (padrão {MAX_PRODUTOS_PER_RUN})")
    args, _ = parser.parse_known_args()

    if args.sem_upload:
        YOUTUBE_UPLOAD = False
    if args.privado:
        YOUTUBE_PUBLISH_NOW = False

    if YOUTUBE_UPLOAD:
        from uploader import check_youtube_token
        ok, msg = check_youtube_token()
        if not ok:
            print(f"❌ Token YouTube inválido: {msg}")
            sys.exit(1)

    asyncio.run(run_afiliados(max_shorts=args.max))
