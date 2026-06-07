"""
Módulo de Trending Topics — agrega sinais de engajamento de múltiplas fontes.

Fontes implementadas:
  1. trends24.in       → Twitter/X trending BR (tempo real)
  2. pytrends          → Google Trends BR (mais buscados)
  3. YouTube Trending  → Vídeos em alta no Brasil (usa API key existente)
  4. G1 + UOL          → Notícias mais lidas agora nos portais

Uso standalone:
    python trends.py

Uso no pipeline:
    from trends import get_trending_topics
    topics = get_trending_topics()   # lista de strings, ex: ["Lula", "BBB", "Dólar"]
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

# Cache em memória — válido por 30 min pra não bater nas APIs toda execução
_CACHE: dict | None = None
_CACHE_TS: float = 0.0
_CACHE_TTL = 1800  # 30 minutos


# ---------------------------------------------------------------------------
# 1. Twitter/X trending — trends24.in
# ---------------------------------------------------------------------------

def _fetch_twitter_trends() -> list[str]:
    """
    Scrapa trends24.in/brazil/ e retorna top 20 trending topics do Twitter BR.
    Retorna lista vazia em caso de falha (não quebra o pipeline).
    """
    try:
        r = requests.get(
            "https://trends24.in/brazil/",
            headers=_HEADERS,
            timeout=10,
        )
        r.raise_for_status()
        soup = BeautifulSoup(r.content, "html.parser")

        trends: list[str] = []

        # trends24 lista os trends em <li> dentro de .trend-card__list
        # Tenta seletor principal, depois fallback por links
        items = soup.select(".trend-card__list li a")
        if not items:
            # fallback: qualquer link dentro de ol/ul com "#"
            items = soup.select("ol li a, ul.trend-list li a")

        for a in items:
            text = a.get_text(strip=True)
            if text and not text.startswith("http"):
                trends.append(text)
            if len(trends) >= 20:
                break

        if trends:
            print(f"  [trends24] {len(trends)} trending topics Twitter BR")
        else:
            print("  [trends24] Nenhum trend encontrado (HTML pode ter mudado)")
        return trends

    except Exception as e:
        print(f"  [trends24] Falhou: {e}")
        return []


# ---------------------------------------------------------------------------
# 2. Google Trends — pytrends
# ---------------------------------------------------------------------------

def _fetch_google_trends() -> list[str]:
    """
    Retorna top 20 termos em alta no Google Brasil usando pytrends.
    Retorna lista vazia em caso de falha.
    """
    try:
        from pytrends.request import TrendReq
        pt = TrendReq(hl="pt-BR", tz=-180, timeout=(5, 15))
        df = pt.trending_searches(pn="brazil")
        trends = df[0].tolist()[:20]
        print(f"  [pytrends] {len(trends)} trending searches Google BR")
        return [str(t) for t in trends]
    except ImportError:
        print("  [pytrends] biblioteca não instalada — pip install pytrends")
        return []
    except Exception as e:
        print(f"  [pytrends] Falhou: {e}")
        return []


# ---------------------------------------------------------------------------
# 3. YouTube Trending Brasil
# ---------------------------------------------------------------------------

def _fetch_youtube_trending() -> list[str]:
    """
    Retorna títulos dos vídeos em alta no YouTube Brasil via Data API v3.
    Usa a mesma chave OAuth do pipeline de upload (credenciais locais).
    Retorna lista vazia em caso de falha.
    """
    try:
        # Tenta usar as credenciais OAuth já configuradas no projeto
        creds_file = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "credentials", "youtube_token.json",
        )
        # Fallback: tenta API key simples se disponível no .env
        api_key = os.getenv("YOUTUBE_API_KEY", "")

        if api_key:
            url = (
                "https://www.googleapis.com/youtube/v3/videos"
                f"?part=snippet&chart=mostPopular&regionCode=BR"
                f"&maxResults=20&key={api_key}"
            )
            r = requests.get(url, timeout=10)
            data = r.json()
            titles = [
                item["snippet"]["title"]
                for item in data.get("items", [])
            ]
            print(f"  [youtube] {len(titles)} vídeos trending BR (API key)")
            return titles

        # Sem API key — tenta via OAuth do projeto
        from googleapiclient.discovery import build
        from google.oauth2.credentials import Credentials

        if not os.path.exists(creds_file):
            print("  [youtube] sem API key nem token OAuth — pulando trending YouTube")
            return []

        creds = Credentials.from_authorized_user_file(creds_file)
        youtube = build("youtube", "v3", credentials=creds)
        resp = youtube.videos().list(
            part="snippet",
            chart="mostPopular",
            regionCode="BR",
            maxResults=20,
        ).execute()
        titles = [item["snippet"]["title"] for item in resp.get("items", [])]
        print(f"  [youtube] {len(titles)} vídeos trending BR (OAuth)")
        return titles

    except Exception as e:
        print(f"  [youtube] Falhou: {e}")
        return []


# ---------------------------------------------------------------------------
# 4. G1 e UOL — notícias mais lidas agora
# ---------------------------------------------------------------------------

def _fetch_most_read() -> list[str]:
    """
    Scrapa as manchetes mais lidas / em destaque de G1 e UOL.
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
    Filtra stopwords e retorna lista deduplicada por ordem de frequência.
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

def get_trending_topics(use_cache: bool = True) -> dict:
    """
    Agrega trending topics de todas as fontes disponíveis.

    Retorna dict:
    {
        "twitter":   [...],   # trending topics do Twitter BR
        "google":    [...],   # trending searches Google BR
        "youtube":   [...],   # títulos dos vídeos trending BR
        "most_read": [...],   # manchetes mais lidas (G1 + UOL)
        "keywords":  [...],   # palavras-chave extraídas de tudo
        "all":       [...],   # lista unificada deduplicada
        "ts":        "...",   # timestamp da coleta
    }
    """
    global _CACHE, _CACHE_TS

    if use_cache and _CACHE and (time.time() - _CACHE_TS) < _CACHE_TTL:
        print("  [trends] Usando cache (< 30 min)")
        return _CACHE

    print(f"\n[trends] Coletando trending topics — {datetime.now().strftime('%H:%M:%S')}")

    twitter   = _fetch_twitter_trends()
    google    = _fetch_google_trends()
    youtube   = _fetch_youtube_trending()
    most_read = _fetch_most_read()

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

    _CACHE    = result
    _CACHE_TS = time.time()

    total = len(twitter) + len(google)
    print(
        f"[trends] Coleta concluída: "
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
