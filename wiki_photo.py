"""
Busca foto/logo REAL de uma pessoa ou empresa/produto via Wikipedia +
Wikidata — gratuito, licenciado (CC/domínio público), sem risco de
direito autoral (diferente de raspar Google Imagens).

Por que não usar a Pexels pra isso: Pexels é banco de fotos de ESTOQUE —
buscar "Jenna Ortega" lá devolve fotos genéricas sem relação nenhuma com a
pessoa (confirmado testando: 3.934 resultados, nenhum é ela de fato).

Checagem de segurança: nome ambíguo pode casar com a página ERRADA da
Wikipedia (ex: "Virgínia" sozinho batendo na bandeira do estado americano
da Virgínia, não na influencer Virginia Fonseca). Por isso toda função
aqui confirma o TIPO da entidade no Wikidata antes de aceitar a imagem.

Nenhuma função aqui lança exceção pro chamador — qualquer falha (timeout,
sem match, tipo rejeitado) retorna None, e quem chama cai pro fallback
normal (Pexels), sem mudar o fluxo de erro existente nos pipelines.
"""
import requests

_UA = {"User-Agent": "youtuber-automatico/1.0 (news-app; contato: geovane.baker89@gmail.com)"}
_TIMEOUT = 10

# Tipos Wikidata claramente ERRADOS pra empresa/produto/software — usado
# como REJECT-list (não allowlist) porque empresa/produto tem tipo demais
# no Wikidata pra listar todos que seriam válidos (empresa, software,
# marca, app, jogo, hardware, serviço...).
#
# ATENÇÃO: essa lista nunca é 100% exaustiva (Wikidata tem centenas de
# subtipos de divisão administrativa/geográfica) — é uma rede de segurança
# contra os casos mais comuns, não uma garantia absoluta. Achado no teste:
# "Virgínia" sozinho batia no estado americano (Q35657), tipo que não
# estava coberto até essa lista ser ampliada.
_ORG_REJECT_TYPES = {
    "Q14660",     # bandeira
    "Q6256",      # país
    "Q515",       # cidade
    "Q1549591",   # grande cidade
    "Q3624078",   # estado soberano
    "Q82794",     # região geográfica
    "Q486972",    # povoado/assentamento humano
    "Q202813",    # ilha
    "Q4022",      # rio
    "Q23397",     # lago
    "Q5107",      # continente
    "Q202444",    # prenome
    "Q101352",    # sobrenome
    "Q4167410",   # página de desambiguação
    "Q5",         # humano (empresa não devia casar com pessoa)
    # divisões administrativas/políticas — categoria inteira costuma ser
    # "casamento errado" pra busca de empresa/produto
    "Q35657",     # estado dos EUA
    "Q107390160", # subdivisão administrativa de primeiro nível de país
    "Q10864048",  # subdivisão administrativa de país (genérico)
    "Q1221156",   # subdivisão de segundo nível
    "Q13418847",  # unidade administrativa histórica
    "Q56061",     # entidade territorial administrativa (categoria-mãe geral)
    "Q133442",    # município
    "Q1489259",   # área metropolitana
    "Q3336843",   # departamento (divisão administrativa tipo França/Colômbia)
    "Q159",       # Rússia (caso específico comum de ambiguidade nome/país)
}

_PERSON_TYPE = "Q5"  # humano


def _wikidata_p31(qid: str) -> set[str]:
    """Retorna o conjunto de valores da propriedade P31 (instance of) da entidade."""
    try:
        r = requests.get(
            "https://www.wikidata.org/w/api.php",
            params={"action": "wbgetentities", "ids": qid, "props": "claims", "format": "json"},
            headers=_UA, timeout=_TIMEOUT,
        )
        entity = r.json().get("entities", {}).get(qid, {})
        claims = entity.get("claims", {}).get("P31", [])
        return {
            c.get("mainsnak", {}).get("datavalue", {}).get("value", {}).get("id")
            for c in claims
        }
    except Exception as e:
        print(f"  [wikidata] falhou: {e}")
        return set()


def _wikidata_logo_url(qid: str) -> str | None:
    """P154 = 'logo image' — propriedade específica do Wikidata pra logo de
    empresa/produto. Usado como fallback quando a página da Wikipedia não
    define 'imagem de destaque' (comum em artigos de empresa/software)."""
    try:
        r = requests.get(
            "https://www.wikidata.org/w/api.php",
            params={"action": "wbgetentities", "ids": qid, "props": "claims", "format": "json"},
            headers=_UA, timeout=_TIMEOUT,
        )
        entity = r.json().get("entities", {}).get(qid, {})
        p154 = entity.get("claims", {}).get("P154", [])
        if not p154:
            return None
        filename = p154[0].get("mainsnak", {}).get("datavalue", {}).get("value")
        if not filename:
            return None
        return f"https://commons.wikimedia.org/wiki/Special:FilePath/{filename.replace(' ', '_')}?width=720"
    except Exception as e:
        print(f"  [wikidata-logo] falhou: {e}")
        return None


def _wiki_pageimage_and_qid(nome: str, lang: str) -> tuple[str | None, str | None]:
    """Consulta a Wikipedia (idioma `lang`) por `nome`. Retorna (thumbnail_url, wikidata_qid)."""
    r = requests.get(
        f"https://{lang}.wikipedia.org/w/api.php",
        params={
            "action": "query", "titles": nome, "prop": "pageimages|pageprops",
            "format": "json", "pithumbsize": 720, "redirects": 1,
        },
        headers=_UA, timeout=_TIMEOUT,
    )
    pages = r.json().get("query", {}).get("pages", {})
    for pid, p in pages.items():
        if pid == "-1":
            continue
        thumb = p.get("thumbnail", {}).get("source")
        qid = p.get("pageprops", {}).get("wikibase_item")
        return thumb, qid
    return None, None


def find_person_photo(nome: str) -> str | None:
    """
    Busca a foto real de uma pessoa (Wikipedia pt→en). Só aceita se o
    Wikidata confirmar 'instance of = human' — rejeita qualquer página
    ambígua (ex: nome que também é palavra comum, lugar, etc).
    """
    if not nome or not nome.strip():
        return None
    for lang in ("pt", "en"):
        try:
            thumb, qid = _wiki_pageimage_and_qid(nome, lang)
            if not thumb:
                continue
            if not qid:
                continue
            if _PERSON_TYPE in _wikidata_p31(qid):
                return thumb
        except Exception as e:
            print(f"  [wiki-{lang}] falhou: {e}")
    return None


def find_org_logo(nome: str) -> str | None:
    """
    Busca o logo real de uma empresa/produto/software (Wikipedia pt→en).
    Rejeita tipos claramente errados via reject-list.

    Prioriza o LOGO (P154 do Wikidata) sobre a 'imagem de destaque' da
    página — descoberto no teste: empresas grandes (Meta, Samsung, Nvidia,
    Spotify) têm foto de SEDE/prédio como imagem de destaque, que fica
    ruim espremida no selo pequeno (~110px) da tela. Só usa a imagem de
    destaque genérica como último recurso, se não existir logo cadastrado.
    """
    if not nome or not nome.strip():
        return None
    for lang in ("pt", "en"):
        try:
            thumb, qid = _wiki_pageimage_and_qid(nome, lang)
            if not qid:
                if thumb:
                    return thumb  # sem qid pra checar tipo, mas tem imagem — aceita
                continue
            if _wikidata_p31(qid) & _ORG_REJECT_TYPES:
                continue  # tipo errado nesse idioma — tenta o próximo

            logo = _wikidata_logo_url(qid)
            if logo:
                return logo
            if thumb:
                return thumb
        except Exception as e:
            print(f"  [wiki-{lang}] falhou: {e}")
    return None
