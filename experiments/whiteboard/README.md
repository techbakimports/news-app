# Protótipo — "Resumo do Dia" (estilo whiteboard/NotebookLM)

**Status: protótipo experimental, NÃO integrado ao pipeline de produção.**
Vive isolado nesta pasta, sem tocar em `main.py`, `shorts.py` nem nenhum
outro arquivo do projeto. Roda com dados 100% reais (RSS + Groq + Edge TTS)
mas não publica nada — só gera vídeo local.

## Origem

Nasceu de uma pergunta simples: dá pra ter vídeo **longo** no canal (não só
Shorts), no mesmo espírito dos "Video Overviews" do Google NotebookLM
(ilustrações desenhadas que vão entrando na tela, narração contínua), sem
depender da ferramenta do Google (sem marca d'água, sem risco de ToS, sem
processo manual) e **sem gastar nada além do que já é gratuito no projeto**?

## O que faz

1. Busca 1 notícia real por categoria (mesmo `fetch_latest_news` do pipeline
   de produção) e extrai o artigo completo (`extract_article_content`).
2. Pede ao Groq (free tier, já usado em produção) pra escrever uma narração
   corrida de 150-220 palavras e dividir em 6-9 trechos, cada um com 2-3
   ícones escolhidos de um vocabulário curado (~140 nomes reais do
   [Tabler Icons](https://tabler.io/icons), MIT) e uma classificação de
   relação entre eles (fluxo / oposição / lista / destaque) — isso decide o
   layout: setas entre ícones, um "X" de conflito, ou um círculo em volta de
   um ícone só em destaque.
3. Baixa os SVGs (cache em `icons_cache/`), aplica uma distorção leve
   ("sketchify") pra imitar traço à mão, e monta as cenas com os ícones
   entrando animados (fade + escala), sincronizados por **timestamp real de
   palavra** do Edge TTS (`WordBoundary`) — a narração é gerada como **um
   único áudio contínuo**, nunca cortado em pedaços, pra não soar "picado".
4. `resumo_do_dia.py` empacota isso: 1 capítulo por categoria de
   `NEWS_SHORTS_CATEGORIES`, concatenados num vídeo só, reaproveitando a
   MESMA seleção/extração de notícia que já alimenta os Shorts (zero
   chamada de IA extra pra isso).

## Como rodar

```bash
# 1 notícia isolada, ~15-45s de vídeo (mais rápido pra testar mudanças)
python experiments/whiteboard/whiteboard_engine.py

# Dia inteiro — 1 capítulo por categoria, ~2-5min de vídeo
python experiments/whiteboard/resumo_do_dia.py
```

Saída em `experiments/whiteboard/output/` (mp3/mp4, git-ignorado). Cache de
ícones baixados em `icons_cache/` (git-ignorado, acelera execuções
seguintes). `tabler_icon_names.json` é versionado — é só a lista de nomes
válidos, evita bater na API do GitHub toda vez.

Requer as mesmas variáveis de ambiente do projeto (`GROQ_API_KEY` no
mínimo) e as libs `svglib`/`reportlab` (não estão no `requirements.txt`
principal — `pip install svglib` se for rodar).

## Gaps conhecidos / próximos passos, se for evoluir isso

- Extração de artigo (`extract_article_content`) às vezes retorna pouco
  conteúdo (matéria curta ou site sem parser bom) — narração sai mais curta
  que o ideal nessas notícias.
- Sem grifo de palavra-chave na frase (existe nos Shorts, `shorts.py` /
  `_pick_highlight_token`) — daria pra portar a mesma ideia aqui.
- Sem trilha de fundo.
- Só formato horizontal (1280×720) — uma versão vertical (1080×1920) do
  mesmo motor viraria Short também, reaproveitando as mesmas cenas.
- Testado só com o português (BR) — nomes de arquivo/vocabulário em inglês,
  mas sem nenhuma adaptação pra pipelines internacionais.
- Layout de cena é sempre "N ícones numa linha" — variar isso (ícone grande
  central, ícones empilhados, etc) evitaria repetição em vídeos com muitos
  capítulos.

## Decisão de arquitetura que vale lembrar

O motor usa **um único TTS por capítulo/notícia** (não um TTS gigante pro
vídeo inteiro) — cada notícia tem sua própria narração contínua, e as
trocas de CAPÍTULO (entre notícias diferentes) têm uma pausa natural, que é
desejável (sinaliza mudança de assunto). Só as cenas *dentro* do mesmo
capítulo são costuradas sem pausa.
