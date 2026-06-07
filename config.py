import os

# Configurações do App de Notícias

# Fontes de notícias gerais
SITES_ALVO = [
    "g1.globo.com",
    "r7.com",
    "uol.com.br",
    "terra.com.br",
    "msn.com/pt-br",
    "oglobo.globo.com",
    "estadao.com.br",
    "cnnbrasil.com.br",
    "metropoles.com",
    "jovempan.com.br",
]

# Fontes especializadas em celebridades / entretenimento / fofoca
# Usadas pelo pipeline de Celebridades para buscar conteúdo no Google News
SITES_CELEBRIDADES = [
    "hugogloss.uol.com.br",   # maior portal de fofoca BR
    "quem.globo.com",          # Revista Quem (Globo)
    "contigo.com.br",          # Revista Contigo
    "extra.globo.com",         # Extra — coluna de famosos
    "metropoles.com",          # Leo Dias mora aqui — maior colunista de fofoca BR
    "caras.com.br",            # Revista Caras
    "ofuxico.com.br",          # Portal especializado em fofoca
    "papelpop.com",            # Pop + música + celebridades
    "purepeople.com.br",       # Pure People BR
    "odia.com.br",             # Fábia Oliveira — coluna de fofoca forte
    "splash.uol.com.br",       # Splash UOL — entretenimento moderno
    "gshow.globo.com",         # GShow (Globo) — BBB, reality, novelas
    "recordtv.r7.com",         # Fabíola Reipert — Hora da Venenosa (Record TV)
]

# Categorias possíveis (universo total)
CATEGORIES = [
    "Política",
    "Esporte",
    "Entretenimento",
    "Mercado Financeiro",
    "Tecnologia",
    "Policial",
    "Celebridades",
]

# Categorias usadas pelo Pipeline de Notícias (Shorts)
# Cada uma vira 1 Short denso de ~3 min (formato Curiosidades)
NEWS_SHORTS_CATEGORIES = [
    "Política",
    "Entretenimento",
    "Mercado Financeiro",
    "Policial",
]

# Configurações de Resumo
SUMMARY_LANGUAGE = "pt-br"
MAX_WORDS_SUMMARY = 150

# Configurações de Áudio
TTS_VOICE = "pt-BR-AntonioNeural"  # voz padrão (fallback)
AUDIO_OUTPUT_DIR = "./audio_news"

# Voz por categoria:
#   Antonio  (masculino, jornalístico) → Política, Policial
#   Francisca (feminino, profissional) → demais notícias + padrão
#   Thalita   (feminino, jovem/leve)   → Celebridades
CATEGORY_VOICES = {
    "Política":           "pt-BR-AntonioNeural",
    "Policial":           "pt-BR-AntonioNeural",
    "Esporte":            "pt-BR-FranciscaNeural",
    "Entretenimento":     "pt-BR-FranciscaNeural",
    "Mercado Financeiro": "pt-BR-FranciscaNeural",
    "Tecnologia":         "pt-BR-FranciscaNeural",
    "Celebridades":       "pt-BR-ThalitaNeural",
    # fallback implícito: TTS_VOICE (Antonio) para qualquer categoria não listada
}

# Diretório de sync — define via variável de ambiente para portabilidade.
# Windows padrão: J:\Meu Drive\News-app (Google Drive Desktop)
# Linux padrão: ~/news-app-drive  (ou monte o Drive com rclone)
_drive_default = r"J:\Meu Drive\News-app" if os.name == "nt" else os.path.expanduser("~/news-app-drive")
DRIVE_SYNC_DIR = os.environ.get("DRIVE_SYNC_DIR", _drive_default)

# Configurações de Vídeo
VIDEO_OUTPUT_DIR = "./video_news"
CHANNEL_NAME = "NewsApp Brasil"  # Nome exibido no canto superior direito do vídeo

# Instagram — ativa se INSTAGRAM_USERNAME e INSTAGRAM_PASSWORD estiverem no .env
INSTAGRAM_UPLOAD = False  # False para desativar mesmo com credenciais configuradas
