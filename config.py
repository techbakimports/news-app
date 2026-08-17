import os

__version__ = "5.4.0"

# Modelos de LLM usados em todos os pipelines (Groq = principal, Gemini = fallback).
# Centralizado aqui pra nunca mais precisar caçar string hardcoded em 13 arquivos
# quando um provedor descontinua um modelo — troca só aqui (ou via .env, sem
# precisar mexer no código nem fazer novo deploy, só reiniciar o processo).
GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")

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
#
# Mesmo fluxo e mesmas 4 categorias do pipeline de Notícias BR (main.py):
# Política, Entretenimento, Mercado Financeiro, Policial — traduzidas por país.
# "categories" mapeia a chave universal pro termo de busca no idioma local.
#
# twitter_country/google_country: slugs do trends24.in / pytrends.
# youtube_region: regionCode da YouTube Data API (ISO 3166-1 alpha-2).
LOCALES = {
    "india": {
        "label":       "Índia",
        "hl": "en-IN", "gl": "IN", "ceid": "IN:en",
        "voice":       "en-IN-NeerjaNeural",
        "language":    "en",
        "token_file":  "credentials/token_india.json",
        "playlist_key": "india",
        "channel_name": "NewsApp India",
        "badge_label": "NEWS",
        "link_label":  "📎 Read the full story:",
        "source_label": "Source:",
        "hashtags":    ["Shorts", "IndiaNews", "News", "India"],
        "categories": {
            "Política":            "Politics",
            "Entretenimento":      "Entertainment",
            "Mercado Financeiro":  "Business",
            "Policial":            "Crime",
        },
        "intro": "NewsApp here, your news in one minute.",
        "cta": (
            " Liked this news? Leave a like, share it with someone who needs "
            "to know, and subscribe to the channel to get your daily news in Short format."
        ),
        "twitter_country": "india",
        "google_country":  "india",
        "youtube_region":  "IN",
        # -- Celebridades (mesmo canal/token do país, playlist e voz separadas) --
        "celebrity_category": "Celebrities",
        "celebrity_voice": "en-IN-NeerjaExpressiveNeural",
        "celebrity_playlist_key": "india_celebridades",
        "celebrity_hashtags": ["Shorts", "Celebrities", "Gossip", "Entertainment"],
        "celebrity_cta": (
            " Liked this celebrity news? Leave a like, share it with a friend "
            "who loves gossip, and subscribe to the channel for more celebrity updates."
        ),
        "celebrity_cta_grave": (
            " Our thoughts are with the family and friends during this difficult "
            "time. Subscribe to the channel to follow the latest news."
        ),
    },
    "japao": {
        "label":       "Japão",
        "hl": "ja",    "gl": "JP", "ceid": "JP:ja",
        "voice":       "ja-JP-NanamiNeural",
        "language":    "ja",
        "token_file":  "credentials/token_japao.json",
        "playlist_key": "japao",
        "channel_name": "NewsApp Japan",
        "badge_label": "ニュース",
        "cjk_font":    True,
        "link_label":  "📎 詳しい記事はこちら:",
        "source_label": "情報源:",
        "hashtags":    ["Shorts", "News", "Japan", "ニュース"],
        "categories": {
            "Política":            "政治",
            "Entretenimento":      "エンタメ",
            "Mercado Financeiro":  "経済",
            "Policial":            "事件",
        },
        "intro": "NewsAppです。1分でニュースをお届けします。",
        "cta": (
            " 気に入ったら高評価とシェアをお願いします。そして、毎日のニュースを"
            "ショート動画でお届けするこのチャンネルへの登録もよろしくお願いします。"
        ),
        "twitter_country": "japan",
        "google_country":  "japan",
        "youtube_region":  "JP",
        # -- Celebridades (mesmo canal/token do país, playlist separada) --
        # Sem voz alternativa mais "leve" disponível em ja-JP (só Nanami/Keita) — reaproveita a mesma.
        "celebrity_category": "芸能",
        "celebrity_voice": "ja-JP-NanamiNeural",
        "celebrity_playlist_key": "japao_celebridades",
        "celebrity_hashtags": ["Shorts", "芸能", "ゴシップ", "エンタメ"],
        "celebrity_cta": (
            " このニュースが気に入ったら、高評価とシェアをお願いします。そして、"
            "芸能ニュースをもっと届けるこのチャンネルへの登録もよろしくお願いします。"
        ),
        "celebrity_cta_grave": (
            " ご家族やご関係者の皆様に心よりお悔やみ申し上げます。"
            "最新のニュースをフォローするには、チャンネル登録をお願いします。"
        ),
    },
    "franca": {
        "label":       "França",
        "hl": "fr",    "gl": "FR", "ceid": "FR:fr",
        "voice":       "fr-FR-DeniseNeural",
        "language":    "fr",
        "token_file":  "credentials/token_franca.json",
        "playlist_key": "franca",
        "channel_name": "NewsApp France",
        "badge_label": "ACTUALITÉS",
        "link_label":  "📎 Lire l'article complet:",
        "source_label": "Source:",
        "hashtags":    ["Shorts", "ActualitesFrance", "News", "France"],
        "categories": {
            "Política":            "Politique",
            "Entretenimento":      "Divertissement",
            "Mercado Financeiro":  "Économie",
            "Policial":            "Faits divers",
        },
        "intro": "NewsApp ici, votre actualité en une minute.",
        "cta": (
            " Cette actualité vous a plu ? Laissez un like, partagez-la avec "
            "quelqu'un qui doit le savoir, et abonnez-vous à la chaîne pour "
            "recevoir l'actualité du jour en format Short."
        ),
        "twitter_country": "france",
        "google_country":  "france",
        "youtube_region":  "FR",
        # -- Celebridades (mesmo canal/token do país, playlist e voz separadas) --
        "celebrity_category": "Célébrités",
        "celebrity_voice": "fr-FR-EloiseNeural",
        "celebrity_playlist_key": "franca_celebridades",
        "celebrity_hashtags": ["Shorts", "Célébrités", "People", "Ragots"],
        "celebrity_cta": (
            " Cette actualité people vous a plu ? Laissez un like, partagez-la "
            "avec une amie qui adore les ragots, et abonnez-vous à la chaîne "
            "pour plus d'actualités people."
        ),
        "celebrity_cta_grave": (
            " Nos pensées vont à la famille et aux proches en ce moment "
            "difficile. Abonnez-vous à la chaîne pour suivre les prochaines actualités."
        ),
    },
    "alemanha": {
        "label":       "Alemanha",
        "hl": "de",    "gl": "DE", "ceid": "DE:de",
        "voice":       "de-DE-KatjaNeural",
        "language":    "de",
        "token_file":  "credentials/token_alemanha.json",
        "playlist_key": "alemanha",
        "channel_name": "NewsApp Deutschland",
        "badge_label": "NACHRICHTEN",
        "link_label":  "📎 Ganzen Artikel lesen:",
        "source_label": "Quelle:",
        "hashtags":    ["Shorts", "NachrichtenDeutschland", "News", "Deutschland"],
        "categories": {
            "Política":            "Politik",
            "Entretenimento":      "Unterhaltung",
            "Mercado Financeiro":  "Wirtschaft",
            "Policial":            "Kriminalität",
        },
        "intro": "NewsApp hier, Ihre Nachrichten in einer Minute.",
        "cta": (
            " Hat Ihnen diese Nachricht gefallen? Hinterlassen Sie ein Like, "
            "teilen Sie sie mit jemandem, der es wissen muss, und abonnieren "
            "Sie den Kanal, um die täglichen Nachrichten im Short-Format zu erhalten."
        ),
        "twitter_country": "germany",
        "google_country":  "germany",
        "youtube_region":  "DE",
        # -- Celebridades (mesmo canal/token do país, playlist e voz separadas) --
        "celebrity_category": "Prominente",
        "celebrity_voice": "de-DE-AmalaNeural",
        "celebrity_playlist_key": "alemanha_celebridades",
        "celebrity_hashtags": ["Shorts", "Promis", "Klatsch", "Unterhaltung"],
        "celebrity_cta": (
            " Hat Ihnen diese Promi-News gefallen? Hinterlassen Sie ein Like, "
            "teilen Sie sie mit einer Freundin, die Klatsch liebt, und abonnieren "
            "Sie den Kanal für mehr Promi-News."
        ),
        "celebrity_cta_grave": (
            " Unsere Gedanken sind bei der Familie und den Angehörigen in dieser "
            "schwierigen Zeit. Abonnieren Sie den Kanal, um die nächsten Nachrichten zu verfolgen."
        ),
    },
    "italia": {
        "label":       "Itália",
        "hl": "it",    "gl": "IT", "ceid": "IT:it",
        "voice":       "it-IT-ElsaNeural",
        "language":    "it",
        "token_file":  "credentials/token_italia.json",
        "playlist_key": "italia",
        "channel_name": "NewsApp Italia",
        "badge_label": "NOTIZIE",
        "link_label":  "📎 Leggi l'articolo completo:",
        "source_label": "Fonte:",
        "hashtags":    ["Shorts", "NotizieItalia", "News", "Italia"],
        "categories": {
            "Política":            "Politica",
            "Entretenimento":      "Intrattenimento",
            "Mercado Financeiro":  "Economia",
            "Policial":            "Cronaca",
        },
        "intro": "NewsApp qui, le tue notizie in un minuto.",
        "cta": (
            " Ti è piaciuta questa notizia? Metti mi piace, condividila con "
            "chi ha bisogno di saperlo, e iscriviti al canale per ricevere "
            "le notizie del giorno in formato Short."
        ),
        "twitter_country": "italy",
        "google_country":  "italy",
        "youtube_region":  "IT",
        # -- Celebridades (mesmo canal/token do país, playlist e voz separadas) --
        "celebrity_category": "Celebrità",
        "celebrity_voice": "it-IT-IsabellaNeural",
        "celebrity_playlist_key": "italia_celebridades",
        "celebrity_hashtags": ["Shorts", "Celebrità", "Gossip", "Intrattenimento"],
        "celebrity_cta": (
            " Ti è piaciuta questa notizia sui vip? Metti mi piace, condividila "
            "con un'amica che ama il gossip, e iscriviti al canale per altre notizie sui vip."
        ),
        "celebrity_cta_grave": (
            " I nostri pensieri vanno alla famiglia e ai cari in questo momento "
            "difficile. Iscriviti al canale per seguire le prossime notizie."
        ),
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
