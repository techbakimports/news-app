# Changelog

Todas as mudanças notáveis neste projeto serão documentadas aqui.

O formato segue [Keep a Changelog](https://keepachangelog.com/pt-BR/1.0.0/),
e este projeto adere ao [Versionamento Semântico](https://semver.org/lang/pt-BR/).

---

## [Unreleased]

### Planejado
- Suporte a mais fontes de notícias (BBC Brasil, CNN Brasil, Folha de S.Paulo)
- Interface web simples para acompanhar execuções
- Agendamento automático via cron/task scheduler
- Suporte a múltiplas vozes TTS configuráveis

---

## [1.0.0] - 2026-04-21

### Adicionado
- Pipeline completo de aggregação e geração de podcast (`main.py`)
- Busca de notícias via Google News RSS com filtro por site e categoria (`fetcher.py`)
- Extração de conteúdo completo dos artigos via web scraping com BeautifulSoup (`fetcher.py`)
- Resumo de notícias em estilo podcast com Google Gemini AI (`summarizer.py`)
- Geração de áudio TTS com voz `pt-BR-AntonioNeural` via Microsoft Edge TTS (`audio.py`)
- Suporte a textos longos: divisão automática em chunks de 2000 chars para o TTS (`audio.py`)
- Limpeza automática de arquivos gerados com mais de 24 horas (`main.py`)
- Deduplicação de notícias por link (`main.py`)
- Exportação para Google Drive Desktop: `.md` (roteiro) + `.mp3` (áudio)
- Configuração centralizada em `config.py` (fontes, categorias, voz, diretórios)
- Remoção de markdown no pré-processamento do TTS para evitar leitura de símbolos (`audio.py`)
- Script de teste de pipeline com uma notícia (`test_run.py`)
- Utilitário para listar modelos Gemini disponíveis (`list_models.py`)
- Delay de 2 segundos entre chamadas à API Gemini para respeitar quotas
- Categorias monitoradas: Política, Esporte, Entretenimento, Mercado Financeiro, Tecnologia, Policial
- Fontes: G1, R7, UOL, Globo, Terra

### Configuração
- Variáveis de ambiente via `.env` com `python-dotenv`
- Limite de ~2200 palavras por ciclo (~15 min de áudio)
- Output com timestamp no formato `Resumo_Completo_YYYYMMDD_HHMM`

---

[Unreleased]: https://github.com/techbakimports/news-app/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/techbakimports/news-app/releases/tag/v1.0.0