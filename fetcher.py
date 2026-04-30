import feedparser
import requests
from bs4 import BeautifulSoup
from config import SITES_ALVO, CATEGORIES
import urllib.parse
from datetime import datetime, date, timezone, timedelta

BR_TZ = timezone(timedelta(hours=-3))


def _is_today(entry) -> bool:
    """Retorna True se a notícia foi publicada hoje no horário de Brasília."""
    parsed = getattr(entry, 'published_parsed', None)
    if not parsed:
        return False
    pub_utc = datetime(*parsed[:6], tzinfo=timezone.utc)
    return pub_utc.astimezone(BR_TZ).date() == datetime.now(BR_TZ).date()


def fetch_latest_news(limit=1):
    """
    Busca notícias de HOJE por categoria dentro de cada site alvo usando Google News.
    Filtra pelo fuso horário de Brasília (UTC-3).
    """
    all_news = []

    for category in CATEGORIES:
        for site in SITES_ALVO:
            print(f"Buscando {category} em {site}...")

            # when:1d restringe o Google News às últimas 24h
            query = f"{category} site:{site} when:1d"
            encoded_query = urllib.parse.quote(query)
            rss_url = f"https://news.google.com/rss/search?q={encoded_query}&hl=pt-BR&gl=BR&ceid=BR:pt-419"

            try:
                response = requests.get(
                    rss_url,
                    timeout=15,
                    headers={"User-Agent": "Mozilla/5.0 (compatible; NewsBot/1.0)"}
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
                news_item = {
                    "category": category,
                    "source": site,
                    "title": entry.title,
                    "link": entry.link,
                    "published": getattr(entry, 'published', 'Data não disponível'),
                    "summary": getattr(entry, 'summary', '')
                }
                all_news.append(news_item)
                count += 1

    return all_news

def extract_article_content(url):
    """
    Tenta extrair o texto principal de uma notícia via URL.
    """
    try:
        response = requests.get(url, timeout=10)
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

if __name__ == "__main__":
    news = fetch_latest_news(limit=1)
    for item in news:
        print(f"\n--- {item['source']} ---")
        print(f"Título: {item['title']}")
        # print(f"Conteúdo: {extract_article_content(item['link'])[:200]}...")
