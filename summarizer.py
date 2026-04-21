import os
import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()

# Configuração da API
api_key = os.getenv("GEMINI_API_KEY")
if api_key:
    genai.configure(api_key=api_key)
else:
    print("AVISO: GEMINI_API_KEY não encontrada no arquivo .env")

def summarize_news(category, title, content):
    """
    Usa o Gemini para resumir a notícia incluindo a categoria.
    """
    if not api_key:
        return "Resumo indisponível (chave de API não configurada)."

    try:
        model = genai.GenerativeModel('gemini-flash-latest')
        prompt = f"""
        Você é um locutor de podcast de notícias. Resuma a notícia abaixo de forma concisa e envolvente.

        REGRAS OBRIGATÓRIAS:
        - Comece anunciando a categoria: "Agora em {category}..." ou "Na área de {category}...".
        - Use linguagem natural e fluida em português do Brasil, como se estivesse falando ao vivo.
        - NÃO use markdown, asteriscos, hashtags, underlines ou qualquer formatação de texto.
        - Escreva APENAS texto simples, sem símbolos especiais.
        - Máximo de 120 palavras.

        Título: {title}
        Conteúdo: {content}

        Resumo (somente texto simples):
        """
        
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        print(f"Erro ao gerar resumo com Gemini: {e}")
        return None

if __name__ == "__main__":
    test_title = "Cientistas descobrem nova espécie de orquídea na Amazônia"
    test_content = "Uma expedição de botânicos brasileiros e estrangeiros identificou uma nova espécie de orquídea no coração da floresta amazônica. A planta apresenta cores vibrantes e um formato único que atraiu a atenção dos pesquisadores durante uma trilha de mapeamento."
    print(summarize_news(test_title, test_content))
