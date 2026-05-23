import os

# Configurações do App de Notícias

# Suas fontes originais
SITES_ALVO = [
    "g1.globo.com",
    "r7.com",
    "uol.com.br",
    "terra.com.br",
    "google_news",  # busca geral no Google News sem filtro de site
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

# Diretório de sync — define via variável de ambiente para portabilidade.
# Windows padrão: J:\Meu Drive\News-app (Google Drive Desktop)
# Linux padrão: ~/news-app-drive  (ou monte o Drive com rclone)
_drive_default = r"J:\Meu Drive\News-app" if os.name == "nt" else os.path.expanduser("~/news-app-drive")
DRIVE_SYNC_DIR = os.environ.get("DRIVE_SYNC_DIR", _drive_default)

# Configurações de Vídeo
VIDEO_OUTPUT_DIR = "./video_news"
CHANNEL_NAME = "NewsApp Brasil"  # Nome exibido no canto superior direito do vídeo

# Instagram — ativa se INSTAGRAM_USERNAME e INSTAGRAM_PASSWORD estiverem no .env
INSTAGRAM_UPLOAD = True  # False para desativar mesmo com credenciais configuradas

# TikTok — ativa se tiktok_cookies.txt existir na raiz do projeto
TIKTOK_UPLOAD = True  # False para desativar mesmo com cookies configurados
