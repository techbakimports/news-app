import os

__version__ = "5.4.0"

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
    "Produtos":           "pt-BR-ThalitaNeural",
    # fallback implícito: TTS_VOICE (Antonio) para qualquer categoria não listada
}

# Locales do pipeline de notícias internacionais (international_news.py).
# Cada país publica num canal do YouTube próprio (token.json separado) —
# ver credentials/token_<locale>.json, criado pelo usuário via OAuth.
# Nomes de voz Edge TTS confirmados via `python -m edge_tts --list-voices`.
LOCALES = {
    "india": {
        "label":       "Índia",
        "hl": "en-IN", "gl": "IN", "ceid": "IN:en",
        "voice":       "en-IN-NeerjaNeural",
        "language":    "en",
        "token_file":  "credentials/token_india.json",
        "playlist_key": "india",
        "badge_label": "NEWS",
        "link_label":  "📎 Read the full story:",
        "source_label": "Source:",
        "hashtags":    ["Shorts", "IndiaNews", "News", "India"],
    },
    "japao": {
        "label":       "Japão",
        "hl": "ja",    "gl": "JP", "ceid": "JP:ja",
        "voice":       "ja-JP-NanamiNeural",
        "language":    "ja",
        "token_file":  "credentials/token_japao.json",
        "playlist_key": "japao",
        "badge_label": "ニュース",
        "cjk_font":    True,
        "link_label":  "📎 詳しい記事はこちら:",
        "source_label": "情報源:",
        "hashtags":    ["Shorts", "News", "Japan", "ニュース"],
    },
    "franca": {
        "label":       "França",
        "hl": "fr",    "gl": "FR", "ceid": "FR:fr",
        "voice":       "fr-FR-DeniseNeural",
        "language":    "fr",
        "token_file":  "credentials/token_franca.json",
        "playlist_key": "franca",
        "badge_label": "ACTUALITÉS",
        "link_label":  "📎 Lire l'article complet:",
        "source_label": "Source:",
        "hashtags":    ["Shorts", "ActualitesFrance", "News", "France"],
    },
    "alemanha": {
        "label":       "Alemanha",
        "hl": "de",    "gl": "DE", "ceid": "DE:de",
        "voice":       "de-DE-KatjaNeural",
        "language":    "de",
        "token_file":  "credentials/token_alemanha.json",
        "playlist_key": "alemanha",
        "badge_label": "NACHRICHTEN",
        "link_label":  "📎 Ganzen Artikel lesen:",
        "source_label": "Quelle:",
        "hashtags":    ["Shorts", "NachrichtenDeutschland", "News", "Deutschland"],
    },
    "italia": {
        "label":       "Itália",
        "hl": "it",    "gl": "IT", "ceid": "IT:it",
        "voice":       "it-IT-ElsaNeural",
        "language":    "it",
        "token_file":  "credentials/token_italia.json",
        "playlist_key": "italia",
        "badge_label": "NOTIZIE",
        "link_label":  "📎 Leggi l'articolo completo:",
        "source_label": "Fonte:",
        "hashtags":    ["Shorts", "NotizieItalia", "News", "Italia"],
    },
}

DRIVE_SYNC_DIR = os.environ.get(
    "DRIVE_SYNC_DIR",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "roteiros"),
)

# Configurações de Vídeo
VIDEO_OUTPUT_DIR = "./video_news"
CHANNEL_NAME = "NewsApp Brasil"  # Nome exibido no canto superior direito do vídeo

# Instagram — ativa se INSTAGRAM_USERNAME e INSTAGRAM_PASSWORD estiverem no .env
INSTAGRAM_UPLOAD = False  # False para desativar mesmo com credenciais configuradas
