import feedparser
import requests
from bs4 import BeautifulSoup
from config import SITES_ALVO, CATEGORIES
import urllib.parse

def fetch_latest_news(limit=1):
    """
    Busca notícias por categoria dentro de cada site alvo usando busca do Google News.
    """
    all_news = []
    
    for category in CATEGORIES:
        for site in SITES_ALVO:
            print(f"Buscando {category} em {site}...")
            
            # Cria a query de busca: "categoria site:dominio"
            query = f"{category} site:{site}"
            encoded_query = urllib.parse.quote(query)
            rss_url = f"https://news.google.com/rss/search?q={encoded_query}&hl=pt-BR&gl=BR&ceid=BR:pt-419"
            
            feed = feedparser.parse(rss_url)
            
            for entry in feed.entries[:limit]:
                news_item = {
                    "category": category,
                    "source": site,
                    "title": entry.title,
                    "link": entry.link,
                    "published": getattr(entry, 'published', 'Data não disponível'),
                    "summary": getattr(entry, 'summary', '')
                }
                all_news.append(news_item)
            
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
