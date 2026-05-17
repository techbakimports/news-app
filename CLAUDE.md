# news-app

## Objetivo
Pipeline automatizado: RSS de notícias → resumo com Gemini AI → áudio TTS → vídeo MP4 → upload YouTube. Gera podcast diário em português.

## Stack
- Python 3.10+ (asyncio)
- APIs: Google Gemini (resumo), Edge TTS (voz pt-BR), YouTube Data API, Pexels (imagens), Groq (fallback)
- Vídeo: moviepy, Pillow, numpy (H.264, 1280×720 @ 24fps)
- Web scraping: feedparser, BeautifulSoup

## Dependências
```bash
pip install -r requirements.txt
# Instalar também: moviepy, numpy, Pillow, google-auth-oauthlib, google-api-python-client, groq
```

## Comandos
```bash
python main.py          # Pipeline completo (fetch → resumo → áudio → vídeo → YouTube)
python test_run.py      # Teste com 1 notícia do G1
python list_models.py   # Lista modelos Gemini disponíveis
```

## Arquitetura do pipeline
```
RSS Google News → filtra (site + categoria + 24h) → deduplica → Gemini batch (5/chamada) → TTS Edge → MP4 → YouTube
```

## Configuração (config.py)
- Sites: g1.globo.com, r7.com, uol.com.br, globo.com, terra.com.br
- Categorias: Política, Esporte, Entretenimento, Mercado Financeiro, Tecnologia, Policial
- Voz TTS: `pt-BR-AntonioNeural`
- Drive sync: `J:\Meu Drive\News-app` (Google Drive Desktop montado)
- Limite: 150 palavras/resumo, 2200 palavras total (~15 min áudio)
- Limpeza automática de arquivos com mais de 24h

## Variáveis de ambiente (.env)
```
GEMINI_API_KEY=    # obrigatório (limite: 20 req/dia, 5 req/min no plano free)
GROQ_API_KEY=      # opcional (fallback de resumo)
PEXELS_API_KEY=    # opcional (imagens para vídeo)
```

## Regras
- Nunca commitar `.env` nem arquivos em `audio_news/` ou `video_news/` (já no .gitignore)
- Batch Gemini é sempre de 5 notícias por chamada — não alterar sem considerar o limite de rate
- O drive `J:` precisa estar montado para o sync funcionar
- OAuth do YouTube usa credenciais locais — não revogar o token sem ter o arquivo de auth à mão
