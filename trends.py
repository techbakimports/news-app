"""
Módulo de Trending Topics — agrega sinais de engajamento de múltiplas fontes.

Fontes implementadas (parametrizáveis por país desde 2026-07-26):
  1. trends24.in       → Twitter/X trending (trends24.in/<slug>/)
  2. pytrends          → Google Trends (trending_searches(pn=<país>))
  3. YouTube Trending  → Vídeos em alta (regionCode=<CC>)
  4. G1 + UOL          → Notícias mais lidas — SÓ BRASIL (scraping bespoke
                          desses dois portais específicos; sem equivalente
                          pros outros países ainda)

Uso standalone:
    python trends.py

Uso no pipeline:
    from trends import get_trending_topics
    topics = get_trending_topics()   # Brasil (padrão)
    topics = get_trending_topics(twitter_country="japan", google_country="japan",
                                 youtube_region="JP", cache_key="japao")
"""
from __future__ import annotations

import os
import re
import time
import logging
from datetime import datetime

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
_HEADERS = {
    "User-Agent": _UA,
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# Cache em memória por país — válido por 30 min pra não bater nas APIs toda execução
_CACHE: dict[str, dict] = {}
_CACHE_TS: dict[str, float] = {}
_CACHE_TTL = 1800  # 30 minutos


# ---------------------------------------------------------------------------
# 1. Twitter/X trending — trends24.in
# ---------------------------------------------------------------------------

def _fetch_twitter_trends(country_slug: str = "brazil") -> list[str]:
    """
    Scrapa trends24.in/<country_slug>/ e retorna top 20 trending topics do Twitter.
    Retorna lista vazia em caso de falha (não quebra o pipeline).
    """
    try:
        r = requests.get(
            f"https://trends24.in/{country_slug}/",
            headers=_HEADERS,
            timeout=10,
        )
        r.raise_for_status()
        soup = BeautifulSoup(r.content, "html.parser")

        trends: list[str] = []
        seen: set[str] = set()

        # trends24 lista os trends em <li> dentro de .trend-card__list
        # Tenta seletor principal, depois fallback por links
        items = soup.select(".trend-card__list li a")
        if not items:
            # fallback: qualquer link dentro de ol/ul com "#"
            items = soup.select("ol li a, ul.trend-list li a")

        for a in items:
            text = a.get_text(strip=True)
            if text and not text.startswith("http") and text not in seen:
                seen.add(text)
                trends.append(text)
            if len(trends) >= 20:
                break

        if trends:
            print(f"  [trends24] {len(trends)} trending topics Twitter ({country_slug})")
        else:
            print(f"  [trends24] Nenhum trend encontrado pra '{country_slug}' (HTML pode ter mudado ou país sem página)")
        return trends

    except Exception as e:
        print(f"  [trends24] Falhou ({country_slug}): {e}")
        return []


# ---------------------------------------------------------------------------
# 2. Google Trends — pytrends
# ---------------------------------------------------------------------------

def _fetch_google_trends(pn: str = "brazil", hl: str = "pt-BR") -> list[str]:
    """
    Retorna top 20 termos em alta no Google usando pytrends.
    pn: código de país do pytrends (ex: "brazil", "japan", "france",
        "germany", "italy", "india" — nomes completos em minúsculo/underscore).
    Retorna lista vazia em caso de falha.
    """
    try:
        from pytrends.request import TrendReq
        pt = TrendReq(hl=hl, tz=-180, timeout=(5, 15))
        df = pt.trending_searches(pn=pn)
        trends = df[0].tolist()[:20]
        print(f"  [pytrends] {len(trends)} trending searches Google ({pn})")
        return [str(t) for t in trends]
    except ImportError:
        print("  [pytrends] biblioteca não instalada — pip install pytrends")
        return []
    except Exception as e:
        print(f"  [pytrends] Falhou ({pn}): {e}")
        return []


# ---------------------------------------------------------------------------
# 3. YouTube Trending
# ---------------------------------------------------------------------------

def _fetch_youtube_trending(region_code: str = "BR") -> list[str]:
    """
    Retorna títulos dos vídeos em alta no YouTube pra uma região via Data API v3.
    Reutiliza as credenciais OAuth do canal principal (credentials/token.json)
    — funciona pra qualquer regionCode, não precisa ser o canal daquele país.
    Retorna lista vazia em caso de falha.
    """
    try:
        from uploader import _get_credentials
        from googleapiclient.discovery import build

        creds   = _get_credentials()
        youtube = build("youtube", "v3", credentials=creds)
        resp    = youtube.videos().list(
            part="snippet",
            chart="mostPopular",
            regionCode=region_code,
            maxResults=20,
        ).execute()
        titles = [item["snippet"]["title"] for item in resp.get("items", [])]
        print(f"  [youtube] {len(titles)} vídeos trending ({region_code})")
        return titles

    except FileNotFoundError:
        print("  [youtube] credentials/token.json não encontrado — pulando")
        return []
    except Exception as e:
        print(f"  [youtube] Falhou ({region_code}): {e}")
        return []


# ---------------------------------------------------------------------------
# 4. G1 e UOL — notícias mais lidas (SÓ BRASIL — sem equivalente internacional)
# ---------------------------------------------------------------------------

def _fetch_most_read() -> list[str]:
    """
    Scrapa as manchetes mais lidas / em destaque de G1 e UOL.
    Só existe pro Brasil — scraping bespoke desses dois portais específicos.
    Retorna lista de títulos (strings).
    """
    titles: list[str] = []

    # G1 — manchetes da home (as mais destacadas = mais lidas/relevantes)
    try:
        r = requests.get("https://g1.globo.com/", headers=_HEADERS, timeout=10)
        soup = BeautifulSoup(r.content, "html.parser")
        # G1 usa .feed-post-body-title ou .gui-color-primary pra manchetes
        for el in soup.select(".feed-post-body-title, .post__title")[:10]:
            t = el.get_text(strip=True)
            if t and len(t) > 15:
                titles.append(t)
        print(f"  [g1] {len(titles)} manchetes coletadas")
    except Exception as e:
        print(f"  [g1] Falhou: {e}")

    # UOL Noticias — manchetes da seção de notícias
    try:
        r = requests.get("https://noticias.uol.com.br/", headers=_HEADERS, timeout=10)
        soup = BeautifulSoup(r.content, "html.parser")
        uol_count = 0
        # UOL usa vários seletores dependendo do layout
        for el in soup.select(
            "h2 a, h3 a, .title a, .headlineTitle, "
            "[class*='title'] a, [class*='headline'] a"
        )[:20]:
            t = el.get_text(strip=True)
            if t and len(t) > 20:
                titles.append(t)
                uol_count += 1
            if uol_count >= 10:
                break
        print(f"  [uol] {uol_count} manchetes coletadas")
    except Exception as e:
        print(f"  [uol] Falhou: {e}")

    return titles


# ---------------------------------------------------------------------------
# Extrator de keywords dos títulos/trending
# ---------------------------------------------------------------------------

_STOPWORDS = {
    "a", "o", "e", "de", "da", "do", "em", "no", "na", "com", "que",
    "se", "por", "para", "as", "os", "ao", "um", "uma", "mais", "mas",
    "é", "são", "foi", "seu", "sua", "ele", "ela", "eles", "elas",
    "como", "tem", "ter", "sobre", "após", "entre", "contra", "não",
    "dos", "das", "nos", "nas", "pelo", "pela", "pelos", "pelas",
    "ser", "está", "isso", "este", "esse", "esta", "essa", "também",
    "faz", "vai", "vem", "diz", "pode", "deve", "seja", "seus", "suas",
    "quando", "onde", "qual", "quem", "novo", "nova", "anos", "ano",
}


def _extract_keywords(texts: list[str], min_len: int = 4) -> list[str]:
    """
    Extrai palavras-chave significativas de uma lista de textos.
    Filtra stopwords (lista em português) e retorna lista deduplicada por
    frequência. Pra idiomas sem espaço entre palavras (japonês), a extração
    fica pouco útil — o "all_topics" (Twitter+Google trends brutos) continua
    válido normalmente, só o refinamento de keywords é mais fraco nesses casos.
    """
    freq: dict[str, int] = {}
    for text in texts:
        words = re.sub(r"[^\w\s]", " ", text.lower()).split()
        for w in words:
            if len(w) >= min_len and w not in _STOPWORDS:
                freq[w] = freq.get(w, 0) + 1

    # Ordena por frequência decrescente, retorna top 40
    sorted_words = sorted(freq.items(), key=lambda x: x[1], reverse=True)
    return [w for w, _ in sorted_words[:40]]


# ---------------------------------------------------------------------------
# Função principal
# ---------------------------------------------------------------------------

def get_trending_topics(
    use_cache: bool = True,
    twitter_country: str = "brazil",
    google_country: str = "brazil",
    google_hl: str = "pt-BR",
    youtube_region: str = "BR",
    cache_key: str = "brazil",
    include_most_read: bool = True,
) -> dict:
    """
    Agrega trending topics de todas as fontes disponíveis pra um país.

    Args:
        twitter_country: slug do trends24.in (ex: "brazil", "japan", "france")
        google_country: código pytrends (ex: "brazil", "japan", "france")
        google_hl: idioma da interface do pytrends (ex: "pt-BR", "ja", "fr")
        youtube_region: regionCode da YouTube Data API (ex: "BR", "JP", "FR")
        cache_key: chave de cache — use um valor distinto por país pra não
                   misturar o cache de locales diferentes
        include_most_read: G1/UOL só existe pro Brasil — passe False pros
                            outros países (economiza 2 requests inúteis)

    Retorna dict:
    {
        "twitter":   [...],   # trending topics do Twitter
        "google":    [...],   # trending searches Google
        "youtube":   [...],   # títulos dos vídeos trending
        "most_read": [...],   # manchetes mais lidas (só Brasil)
        "keywords":  [...],   # palavras-chave extraídas de tudo
        "all":       [...],   # lista unificada deduplicada
        "ts":        "...",   # timestamp da coleta
    }
    """
    cached = _CACHE.get(cache_key)
    cached_ts = _CACHE_TS.get(cache_key, 0.0)
    if use_cache and cached and (time.time() - cached_ts) < _CACHE_TTL:
        print(f"  [trends] Usando cache ({cache_key}, < 30 min)")
        return cached

    print(f"\n[trends] Coletando trending topics ({cache_key}) — {datetime.now().strftime('%H:%M:%S')}")

    twitter   = _fetch_twitter_trends(twitter_country)
    google    = _fetch_google_trends(google_country, hl=google_hl)
    youtube   = _fetch_youtube_trending(youtube_region)
    most_read = _fetch_most_read() if include_most_read else []

    # Lista unificada deduplicada (preserva ordem: Twitter primeiro)
    seen: set[str] = set()
    all_topics: list[str] = []
    for item in twitter + google:
        key = item.lower().strip()
        if key and key not in seen:
            seen.add(key)
            all_topics.append(item)

    # Keywords extraídas de todas as fontes
    keywords = _extract_keywords(twitter + google + youtube + most_read)

    result = {
        "twitter":   twitter,
        "google":    google,
        "youtube":   youtube,
        "most_read": most_read,
        "keywords":  keywords,
        "all":       all_topics,
        "ts":        datetime.now().strftime("%d/%m/%Y %H:%M"),
    }

    _CACHE[cache_key]    = result
    _CACHE_TS[cache_key] = time.time()

    total = len(twitter) + len(google)
    print(
        f"[trends] Coleta concluída ({cache_key}): "
        f"{len(twitter)} Twitter | {len(google)} Google | "
        f"{len(youtube)} YouTube | {len(most_read)} manchetes | "
        f"{len(keywords)} keywords"
    )
    return result


# ---------------------------------------------------------------------------
# Entry point standalone
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    # Força UTF-8 no terminal Windows
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    topics = get_trending_topics(use_cache=False)
    print("\n=== TRENDING TOPICS BRASIL ===")
    print(f"\nTwitter ({len(topics['twitter'])}):")
    for t in topics["twitter"][:10]:
        print(f"  {t}")
    print(f"\nGoogle ({len(topics['google'])}):")
    for t in topics["google"][:10]:
        print(f"  {t}")
    print(f"\nYouTube trending ({len(topics['youtube'])}):")
    for t in topics["youtube"][:5]:
        print(f"  {t}")
    print(f"\nMais lidas G1/UOL ({len(topics['most_read'])}):")
    for t in topics["most_read"][:5]:
        print(f"  {t}")
    print(f"\nKeywords em alta ({len(topics['keywords'])}):")
    print(f"  {', '.join(topics['keywords'][:20])}")
