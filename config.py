# Configurações do App de Notícias

# Suas fontes originais
SITES_ALVO = [
    "g1.globo.com",
    "r7.com",
    "uol.com.br",
    "globo.com",
    "terra.com.br"
]

# Categorias desejadas
CATEGORIES = [
    "Política",
    "Esporte",
    "Entretenimento",
    "Mercado Financeiro",
    "Tecnologia",
    "Policial"
]

# Configurações de Resumo
SUMMARY_LANGUAGE = "pt-br"
MAX_WORDS_SUMMARY = 150

# Configurações de Áudio
TTS_VOICE = "pt-BR-AntonioNeural" # Voz masculina natural
AUDIO_OUTPUT_DIR = "./audio_news"

# Configurações de Output para NotebookLM (Google Drive)
# Se você tiver o Google Drive para Desktop, mude este caminho para sua pasta do Drive
DRIVE_SYNC_DIR = r"J:\Meu Drive\News-app" 
