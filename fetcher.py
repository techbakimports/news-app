import json
import os
import re
import time
import feedparser
import requests
from bs4 import BeautifulSoup
from config import SITES_ALVO, CATEGORIES
import urllib.parse
from datetime import datetime, date, timezone, timedelta

BR_TZ = timezone(timedelta(hours=-3))

# User-Agent de Chrome real — muitos sites bloqueiam UAs genericos de bot.
_UA_CHROME = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
_BROWSER_HEADERS = {
    "User-Agent": _UA_CHROME,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
}

# Cache em disco pra evitar resolver o mesmo redirect 2x no mesmo dia.
# Estrutura: {google_news_url: {"resolved": "...", "ts": float}}
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_REDIRECT_CACHE_FILE = os.path.join(_BASE_DIR, "logs", "redirect_cache.json")
_REDIRECT_CACHE_TTL = 86400  # 1 dia
_REDIRECT_CACHE: dict | None = None


def _load_redirect_cache() -> dict:
    global _REDIRECT_CACHE
    if _REDIRECT_CACHE is not None:
        return _REDIRECT_CACHE
    try:
        with open(_REDIRECT_CACHE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        # Limpa entradas expiradas na carga
        now = time.time()
        data = {k: v for k, v in data.items() if now - v.get("ts", 0) < _REDIRECT_CACHE_TTL}
        _REDIRECT_CACHE = data
    except (FileNotFoundError, json.JSONDecodeError):
        _REDIRECT_CACHE = {}
    return _REDIRECT_CACHE


def _save_redirect_cache() -> None:
    if _REDIRECT_CACHE is None:
        return
    try:
        os.makedirs(os.path.dirname(_REDIRECT_CACHE_FILE), exist_ok=True)
        with open(_REDIRECT_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(_REDIRECT_CACHE, f)
    except OSError:
        pass


def _resolve_google_news_url(url: str, timeout: float = 10.0) -> str:
    """
    Resolve um link do Google News (news.google.com/rss/articles/...) pra URL real.
    Usa o endpoint interno batchexecute do Google.

    Algoritmo:
    1. GET na pagina do artigo → extrai tokens c-wiz (signature + timestamp)
    2. POST em /_/DotsSplashUi/data/batchexecute com esses tokens
    3. Parseia JSON aninhado pra extrair a URL real

    Cache em disco evita refazer no mesmo dia. Em caso de falha, retorna a URL original.
    """
    if "news.google.com" not in url:
        return url

    cache = _load_redirect_cache()
    if url in cache:
        return cache[url]["resolved"]

    fallback = url

    try:
        # Passo 1: pega a pagina do artigo e extrai os tokens
        r = requests.get(url, timeout=timeout, headers=_BROWSER_HEADERS, allow_redirects=True)

        # Se ja foi pra fora de news.google.com (caso raro), usamos a final
        if "news.google.com" not in r.url:
            cache[url] = {"resolved": r.url, "ts": time.time()}
            _save_redirect_cache()
            return r.url

        html = r.text

        # Extrai data-n-a-id (signature) e data-n-a-sg (timestamp)
        # Esses sao tokens que o JS usa pra autenticar a chamada batchexecute
        sig_match = re.search(r'data-n-a-sg="([^"]+)"', html)
        ts_match = re.search(r'data-n-a-ts="([^"]+)"', html)

        if not sig_match or not ts_match:
            # Layout antigo — tenta extrair direto do HTML algum href limpo
            soup = BeautifulSoup(html, "html.parser")
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if href.startswith("http") and "google" not in href:
                    cache[url] = {"resolved": href, "ts": time.time()}
                    _save_redirect_cache()
                    return href
            raise ValueError("tokens c-wiz nao encontrados")

        signature = sig_match.group(1)
        timestamp = ts_match.group(1)

        # gn_art_id vem do atributo data-n-a-id (eh o mesmo que esta na URL)
        id_match = re.search(r'data-n-a-id="([^"]+)"', html)
        if id_match:
            gn_art_id = id_match.group(1)
        else:
            # Fallback: pega do path da URL
            path_match = re.search(r"/articles/([^?]+)", url)
            if not path_match:
                raise ValueError("articles ID nao encontrado")
            gn_art_id = path_match.group(1)

        # Passo 2: POST em batchexecute com a RPC "Fbv4je" (garturlreq)
        be_url = "https://news.google.com/_/DotsSplashUi/data/batchexecute"
        inner = json.dumps([
            "garturlreq",
            [["X","X",["X","X"],None,None,1,1,"US:en",None,1,None,None,None,None,None,0,1],
             "X","X",1,[1,1,1],1,1,None,0,0,None,0],
            gn_art_id, int(timestamp), signature
        ])
        f_req = json.dumps([[["Fbv4je", inner, None, "generic"]]])
        payload = "f.req=" + urllib.parse.quote(f_req)

        be_headers = {
            **_BROWSER_HEADERS,
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            "Origin": "https://news.google.com",
            "Referer": url,
        }

        be_resp = requests.post(be_url, data=payload, headers=be_headers, timeout=timeout)

        if be_resp.status_code != 200:
            raise ValueError(f"batchexecute retornou {be_resp.status_code}")

        # Resposta vem como )]}'\n[json] — pula prefixo de seguranca
        text = be_resp.text
        # Procura o array "garturlres" e a primeira URL apos ele
        m = re.search(r'garturlres\\?",\\?"(https?://[^"\\]+)', text)
        if m:
            real_url = m.group(1).replace("\\u003d", "=").replace("\\/", "/")
            if "google" not in real_url or "google.com/amp" in real_url:
                cache[url] = {"resolved": real_url, "ts": time.time()}
                _save_redirect_cache()
                return real_url

    except Exception as e:
        print(f"  _resolve_google_news_url falhou ({type(e).__name__}: {e}). Usando URL original.")

    # Falhou — cacheia o fallback pra nao retentar agora
    cache[url] = {"resolved": fallback, "ts": time.time()}
    _save_redirect_cache()
    return fallback


def _hostname_of(url: str) -> str:
    """Extrai hostname limpo (sem www.) da URL."""
    try:
        host = urllib.parse.urlparse(url).hostname or ""
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""


def _is_today(entry) -> bool:
    """Retorna True se a notícia foi publicada hoje no horário de Brasília."""
    parsed = getattr(entry, 'published_parsed', None)
    if not parsed:
        return False
    pub_utc = datetime(*parsed[:6], tzinfo=timezone.utc)
    return pub_utc.astimezone(BR_TZ).date() == datetime.now(BR_TZ).date()


def fetch_latest_news(limit=1, categories=None):
    """
    Busca notícias de HOJE por categoria dentro de cada site alvo usando Google News.
    Filtra pelo fuso horário de Brasília (UTC-3).

    Args:
        limit: quantas notícias por (categoria × site)
        categories: lista de categorias a buscar (default: CATEGORIES do config)
    """
    all_news = []
    resolve_ok = 0
    resolve_fail = 0
    cats = categories if categories is not None else CATEGORIES

    for category in cats:
        for site in SITES_ALVO:
            source_label = "Google News" if site == "google_news" else site
            print(f"Buscando {category} em {source_label}...")

            # google_news = busca geral sem filtro de site (top resultados do Google News)
            if site == "google_news":
                query = f"{category} when:1d"
            else:
                query = f"{category} site:{site} when:1d"

            encoded_query = urllib.parse.quote(query)
            rss_url = f"https://news.google.com/rss/search?q={encoded_query}&hl=pt-BR&gl=BR&ceid=BR:pt-419"

            try:
                response = requests.get(
                    rss_url,
                    timeout=15,
                    headers=_BROWSER_HEADERS,
                )
                feed = feedparser.parse(response.content)
            except requests.exceptions.Timeout:
                print(f"  Timeout ao buscar feed do Google News para {category} em {site}. Pulando...")
                continue
            except requests.exceptions.RequestException as e:
                print(f"  Erro de rede ({e}). Pulando {category} em {site}...")
                continue

            count = 0
            for entry in feed.entries:
                if count >= limit:
                    break
                if not _is_today(entry):
                    print(f"  Ignorada (não é de hoje): {entry.title[:60]}")
                    continue

                # Resolve o redirect do Google News pra URL real do artigo
                raw_link = entry.link
                real_link = _resolve_google_news_url(raw_link)

                # source vira o hostname real (se resolveu) ou o filtro de site original
                resolved_host = _hostname_of(real_link)
                if resolved_host and "google" not in resolved_host:
                    source_final = resolved_host
                    resolve_ok += 1
                else:
                    source_final = "Google News" if site == "google_news" else site
                    resolve_fail += 1

                news_item = {
                    "category": category,
                    "source": source_final,
                    "title": entry.title,
                    "link": real_link,
                    "published": getattr(entry, 'published', 'Data não disponível'),
                    "summary": getattr(entry, 'summary', '')
                }
                all_news.append(news_item)
                count += 1

    total = resolve_ok + resolve_fail
    if total > 0:
        pct = resolve_ok * 100 // total
        print(f"\n[fetcher] Redirect Google News: {resolve_ok}/{total} resolvidos ({pct}%) | {resolve_fail} fallback")
    return all_news

def extract_article_content(url):
    """
    Tenta extrair o texto principal de uma notícia via URL.
    Resolve redirect do Google News se necessario.
    """
    try:
        # Se for link do Google News, resolve antes de scrapar
        if "news.google.com" in url:
            url = _resolve_google_news_url(url)

        response = requests.get(url, timeout=10, headers=_BROWSER_HEADERS)
        soup = BeautifulSoup(response.content, 'html.parser')

        # Remove scripts e estilos
        for script_or_style in soup(["script", "style"]):
            script_or_style.extract()

        # Busca comum por parágrafos (pode variar por site)
        paragraphs = soup.find_all('p')
        content = "\n".join([p.get_text() for p in paragraphs if len(p.get_text()) > 50])

        return content
    except Exception as e:
        print(f"Erro ao extrair conteúdo de {url}: {e}")
        return ""

_STOPWORDS_PT = {
    'a', 'ao', 'aos', 'as', 'com', 'da', 'das', 'de', 'do', 'dos',
    'e', 'em', 'na', 'nas', 'no', 'nos', 'o', 'os', 'ou', 'para',
    'por', 'que', 'se', 'um', 'uma',
}


def _normalize_title(title):
    words = re.sub(r'[^\w\s]', '', title.lower()).split()
    return {w for w in words if w not in _STOPWORDS_PT and len(w) > 2}


def _is_similar(title_a, title_b, threshold=0.45):
    wa, wb = _normalize_title(title_a), _normalize_title(title_b)
    if not wa or not wb:
        return False

    intersection = wa & wb
    jaccard = len(intersection) / min(len(wa), len(wb))
    if jaccard >= threshold:
        return True

    # Detecta mesma notícia com títulos diferentes:
    # se ambos mencionam o mesmo número significativo (ex: "500 mil", "1 bilhão")
    # e compartilham pelo menos 1 palavra-chave em comum → provavelmente a mesma história
    nums_a = set(re.findall(r'\d{3,}', title_a))
    nums_b = set(re.findall(r'\d{3,}', title_b))
    if nums_a & nums_b and len(intersection) >= 1:
        return True

    return False


def select_unique_news(raw_news):
    """
    A partir de múltiplos candidatos por (categoria, fonte), seleciona
    no máximo um item por par (categoria, fonte), depois aplica deduplicação
    global para evitar que a mesma história apareça de duas fontes distintas,
    independente de categoria.

    Garante representação de todas as fontes: se uma fonte perdeu todos os
    itens na dedup global, tenta reintroduzir o seu melhor candidato.
    """
    pools = {}
    for item in raw_news:
        pools.setdefault((item['category'], item['source']), []).append(item)

    # Passo 1: seleciona o melhor candidato por (categoria, fonte) — dedup local
    pre_selected = []
    seen_per_category = {}
    for (category, site), candidates in pools.items():
        seen = seen_per_category.setdefault(category, [])
        for candidate in candidates:
            if not any(_is_similar(candidate['title'], t) for t in seen):
                pre_selected.append(candidate)
                seen.append(candidate['title'])
                print(f"  [OK-local] {category}|{site}: {candidate['title'][:55]}")
                break
            else:
                print(f"  [~-local] Dup {category}|{site}: {candidate['title'][:55]}")

    # Passo 2: deduplicação global — remove a mesma história entre fontes diferentes
    selected = []
    seen_global = []
    for item in pre_selected:
        if any(_is_similar(item['title'], t) for t in seen_global):
            print(f"  [~-global] Dup entre fontes: {item['source']}|{item['category']}: {item['title'][:55]}")
            continue
        selected.append(item)
        seen_global.append(item['title'])

    # Passo 3: garantir representação de todas as fontes
    sources_represented = {item['source'] for item in selected}
    all_sources = {item['source'] for item in raw_news}
    missing_sources = all_sources - sources_represented

    for missing_site in missing_sources:
        candidates = [i for i in raw_news if i['source'] == missing_site]
        for candidate in candidates:
            if not any(_is_similar(candidate['title'], t) for t in seen_global):
                selected.append(candidate)
                seen_global.append(candidate['title'])
                print(f"  [+repr] Reintroduzindo {missing_site}: {candidate['title'][:55]}")
                break

    return selected


if __name__ == "__main__":
    news = fetch_latest_news(limit=3)
    for item in news:
        print(f"\n--- {item['source']} ---")
        print(f"Título: {item['title']}")
        # print(f"Conteúdo: {extract_article_content(item['link'])[:200]}...")
