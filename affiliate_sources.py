"""
Fontes de produtos de afiliados — Shopee Affiliate Open API + Lomadee.

Shopee: API oficial GraphQL (open-api.affiliate.shopee.com.br/graphql),
autenticação por assinatura SHA256 por requisição. Cadastro e aprovação
em affiliate.shopee.com.br/open_api (manual, 3-5 dias úteis).

IMPORTANTE: os nomes exatos dos campos da query GraphQL abaixo (productOfferV2,
generateShortLink) seguem a convenção pública documentada pela Shopee no
momento da escrita deste módulo, mas GraphQL é introspectável — assim que
SHOPEE_AFFILIATE_APP_ID/SECRET estiverem configurados e aprovados, rode
`python -c "from affiliate_sources import shopee_schema_probe; shopee_schema_probe()"`
pra conferir contra o schema real e ajustar os nomes de campo se necessário.

Lomadee: API REST simples (developer.lomadee.com), token único por afiliado.
"""
import hashlib
import os
import time

import requests

# ── Shopee ────────────────────────────────────────────────────────────────────

SHOPEE_APP_ID = os.getenv("SHOPEE_AFFILIATE_APP_ID", "")
SHOPEE_APP_SECRET = os.getenv("SHOPEE_AFFILIATE_APP_SECRET", "")
SHOPEE_GRAPHQL_URL = "https://open-api.affiliate.shopee.com.br/graphql"


def _shopee_configured() -> bool:
    return bool(SHOPEE_APP_ID and SHOPEE_APP_SECRET)


def _shopee_sign(payload: str, timestamp: int) -> str:
    """Assinatura SHA256: sha256(app_id + timestamp + payload + app_secret)."""
    raw = f"{SHOPEE_APP_ID}{timestamp}{payload}{SHOPEE_APP_SECRET}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _shopee_graphql(query: str, variables: dict | None = None) -> dict:
    if not _shopee_configured():
        raise RuntimeError(
            "Shopee Affiliate não configurado — defina SHOPEE_AFFILIATE_APP_ID "
            "e SHOPEE_AFFILIATE_APP_SECRET no .env (após aprovação em affiliate.shopee.com.br/open_api)"
        )
    body = {"query": query, "variables": variables or {}}
    import json as _json
    payload = _json.dumps(body, separators=(",", ":"))
    timestamp = int(time.time())
    sign = _shopee_sign(payload, timestamp)

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"SHA256 Credential={SHOPEE_APP_ID}, Timestamp={timestamp}, Signature={sign}",
    }
    resp = requests.post(SHOPEE_GRAPHQL_URL, data=payload, headers=headers, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    if data.get("errors"):
        raise RuntimeError(f"Shopee GraphQL retornou erro: {data['errors']}")
    return data.get("data", {})


def shopee_schema_probe() -> None:
    """Introspecção rápida do schema — rodar manualmente após aprovação pra validar nomes de campo."""
    introspection = "{ __schema { queryType { fields { name } } } }"
    data = _shopee_graphql(introspection)
    print("Campos disponíveis na query root da Shopee Affiliate API:")
    for f in data.get("__schema", {}).get("queryType", {}).get("fields", []):
        print(f"  - {f['name']}")


def get_shopee_offers(limit: int = 20) -> list[dict]:
    """
    Busca ofertas/produtos em destaque via Shopee Affiliate API.
    Retorna lista no formato comum (ver get_candidate_products).
    """
    query = """
    query ProductOffers($limit: Int) {
      productOfferV2(listType: 0, page: 1, limit: $limit) {
        nodes {
          itemId
          productName
          price
          priceMin
          commissionRate
          imageUrl
          offerLink
        }
      }
    }
    """
    data = _shopee_graphql(query, {"limit": limit})
    nodes = data.get("productOfferV2", {}).get("nodes", [])

    products = []
    for n in nodes:
        price = float(n.get("price", 0) or 0)
        price_min = float(n.get("priceMin", price) or price)
        discount_pct = round((1 - price_min / price) * 100) if price > 0 and price_min < price else 0
        products.append({
            "name": n.get("productName", ""),
            "price": price_min,
            "original_price": price,
            "discount_pct": discount_pct,
            "image_url": n.get("imageUrl", ""),
            "affiliate_link": n.get("offerLink", ""),
            "commission_pct": float(n.get("commissionRate", 0) or 0) * 100,
            "source": "shopee",
        })
    return products


# ── Lomadee ───────────────────────────────────────────────────────────────────

LOMADEE_APP_TOKEN = os.getenv("LOMADEE_APP_TOKEN", "")
LOMADEE_BASE_URL = "https://api.lomadee.com/v3"


def _lomadee_configured() -> bool:
    return bool(LOMADEE_APP_TOKEN)


def get_lomadee_offers(limit: int = 20) -> list[dict]:
    """Busca ofertas em destaque via Lomadee (Americanas, Magalu, Casas Bahia, Netshoes...)."""
    if not _lomadee_configured():
        raise RuntimeError(
            "Lomadee não configurado — defina LOMADEE_APP_TOKEN no .env "
            "(cadastro em lomadee.com.br/afiliados)"
        )
    url = f"{LOMADEE_BASE_URL}/{LOMADEE_APP_TOKEN}/offer/_search"
    resp = requests.get(url, params={"size": limit}, timeout=20)
    resp.raise_for_status()
    data = resp.json()

    products = []
    for offer in data.get("offers", [])[:limit]:
        price = float(offer.get("price", 0) or 0)
        price_from = float(offer.get("priceFrom", price) or price)
        discount_pct = round((1 - price / price_from) * 100) if price_from > price > 0 else 0
        products.append({
            "name": offer.get("name", ""),
            "price": price,
            "original_price": price_from,
            "discount_pct": discount_pct,
            "image_url": offer.get("thumbnail", ""),
            "affiliate_link": offer.get("link", ""),
            "commission_pct": 0.0,  # Lomadee não expõe comissão por oferta na busca
            "source": "lomadee",
        })
    return products


# ── Agregação e seleção ────────────────────────────────────────────────────────

def get_candidate_products(limit: int = 20) -> list[dict]:
    """Junta produtos de todas as fontes configuradas. Fontes não configuradas são puladas silenciosamente."""
    products = []

    if _shopee_configured():
        try:
            products.extend(get_shopee_offers(limit))
        except Exception as e:
            print(f"  [affiliate_sources] Shopee falhou: {e}")

    if _lomadee_configured():
        try:
            products.extend(get_lomadee_offers(limit))
        except Exception as e:
            print(f"  [affiliate_sources] Lomadee falhou: {e}")

    if not products:
        print("  [affiliate_sources] Nenhuma fonte de afiliados configurada ou disponível.")

    return products


def select_top_products(products: list[dict], n: int) -> list[dict]:
    """
    Seleciona os N melhores produtos por score = desconto% × (1 + comissão%/100).
    Heurística simples — sem custo de LLM, já que os critérios são numéricos.
    """
    def score(p: dict) -> float:
        return p.get("discount_pct", 0) * (1 + p.get("commission_pct", 0) / 100)

    ranked = sorted(products, key=score, reverse=True)
    return ranked[:n]
