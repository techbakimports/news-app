# 📻 News App

Sistema automatizado de agregação de notícias e geração de podcast em português brasileiro. Busca notícias de fontes nacionais, resume com IA (Google Gemini) e gera áudio via TTS — pronto para importar no Google NotebookLM.

## Funcionalidades

- **Agregação automática** de notícias de G1, R7, UOL, Globo e Terra via RSS
- **Extração de conteúdo** completo dos artigos via web scraping
- **Resumo com IA** usando Google Gemini em formato de podcast (pt-BR)
- **Geração de áudio** com voz natural masculina (Microsoft Edge TTS)
- **Exportação para Google Drive** — roteiro Markdown + MP3 (~15 min)
- **Limpeza automática** de arquivos com mais de 24 horas
- **Deduplicação** de notícias por link

## Categorias monitoradas

Política · Esporte · Entretenimento · Mercado Financeiro · Tecnologia · Policial

## Estrutura do projeto

```
news-app/
├── main.py          # Orquestrador principal do pipeline
├── fetcher.py       # Busca e extração de notícias via RSS + scraping
├── summarizer.py    # Resumo de notícias com Google Gemini AI
├── audio.py         # Geração de áudio com Microsoft Edge TTS
├── config.py        # Configurações globais (fontes, voz, diretórios)
├── test_run.py      # Script de teste do pipeline completo
├── list_models.py   # Utilitário para listar modelos Gemini disponíveis
├── requirements.txt # Dependências Python
└── .env.example     # Exemplo de variáveis de ambiente
```

## Pré-requisitos

- Python 3.10+
- Conta Google com acesso à [Google AI Studio](https://aistudio.google.com/) (para obter a API Key do Gemini)
- Google Drive para Desktop instalado *(opcional, para sincronização automática)*

## Instalação

```bash
# 1. Clone o repositório
git clone https://github.com/techbakimports/news-app.git
cd news-app

# 2. Crie e ative um ambiente virtual
python -m venv venv
source venv/bin/activate       # Linux/macOS
venv\Scripts\activate          # Windows

# 3. Instale as dependências
pip install -r requirements.txt

# 4. Configure as variáveis de ambiente
cp .env.example .env
# Edite o .env e insira sua GEMINI_API_KEY
```

## Configuração

Edite `config.py` para personalizar:

| Variável | Padrão | Descrição |
|---|---|---|
| `SITES_ALVO` | g1, r7, uol, globo, terra | Sites de notícias monitorados |
| `CATEGORIES` | 6 categorias | Categorias buscadas |
| `MAX_WORDS_SUMMARY` | 150 | Palavras por resumo |
| `TTS_VOICE` | pt-BR-AntonioNeural | Voz do Text-to-Speech |
| `AUDIO_OUTPUT_DIR` | `./audio_news` | Pasta local do áudio gerado |
| `DRIVE_SYNC_DIR` | `J:\Meu Drive\News-app` | Pasta do Google Drive Desktop |

> Ajuste `DRIVE_SYNC_DIR` para o caminho da sua pasta do Google Drive no sistema.

## Uso

```bash
# Executar o pipeline completo
python main.py

# Testar com uma única notícia
python test_run.py

# Listar modelos Gemini disponíveis
python list_models.py
```

### Saída gerada

Cada execução gera um par de arquivos com timestamp `YYYYMMDD_HHMM`:

- `Resumo_Completo_YYYYMMDD_HHMM.md` — roteiro para NotebookLM
- `Resumo_Completo_YYYYMMDD_HHMM.mp3` — áudio (~15 minutos)

## Pipeline

```
RSS Feeds → Extração de Conteúdo → Gemini AI (resumo) → Edge TTS (áudio) → Google Drive
```

1. Busca notícias via Google News RSS filtrando pelos sites-alvo
2. Extrai o texto completo dos artigos via BeautifulSoup
3. Resume cada artigo com Gemini em estilo de podcast (máx. 120 palavras)
4. Consolida todos os resumos em um único roteiro (máx. 2200 palavras / ~15 min)
5. Gera o áudio com Microsoft Edge TTS (voz `pt-BR-AntonioNeural`)
6. Salva `.md` e `.mp3` no Google Drive para uso no NotebookLM
7. Remove arquivos com mais de 24 horas

## Dependências

| Pacote | Uso |
|---|---|
| `google-generativeai` | Resumo de notícias com Gemini AI |
| `edge-tts` | Geração de áudio Text-to-Speech |
| `feedparser` | Parse de RSS feeds |
| `beautifulsoup4` | Extração de conteúdo dos artigos |
| `requests` | Requisições HTTP |
| `python-dotenv` | Gerenciamento de variáveis de ambiente |
| `aiofiles` | I/O assíncrono de arquivos |

## Contribuindo

1. Fork o repositório
2. Crie uma branch (`git checkout -b feat/nova-funcionalidade`)
3. Commit suas mudanças (`git commit -m 'feat: adicionar nova funcionalidade'`)
4. Push para a branch (`git push origin feat/nova-funcionalidade`)
5. Abra um Pull Request

## Licença

MIT — veja [LICENSE](LICENSE) para detalhes.