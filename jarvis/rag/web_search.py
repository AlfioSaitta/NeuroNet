"""
Web Intelligence — SearXNG metasearch + Crawl4AI scraping in parallelo.
"""

import asyncio
import re

from core.config import SEARXNG_HOST, CRAWL4AI_HOST, CRAWL4AI_API_TOKEN, logger
import core.state as state


# ════════════════════════════════════════════════════════════════
# DETECTION QUERY LIVE-DATI (meteo, news, prezzi, ricerca esplicita)
# ════════════════════════════════════════════════════════════════
#
# Usato dal branch "general" di build_omniscient_prompt per attivare
# la web search quando la richiesta richiede dati attuali/live invece
# di lasciare che il modello risponda dalla conoscenza interna (stantia).

# Preposizioni semplici o articolate davanti a "web/internet/online/rete".
# Copre: "su web", "su il web", "in internet", "sul web", "sulla rete",
# "nel web", "la rete", "il web", ecc. L'alternanza è ordinata: prima le
# forme con preposizione semplice + articolo opzionale, poi le articolate,
# poi l'articolo diretto.
_WEB_TARGET = (
    r'(?:(?:su|in)\s+(?:il|la|lo|le|i|gli)?\s*'
    r'|(?:sul|sullo|sulla|sui|sugli|sulle|nel|nello|nella|nei|negli|nelle)\s*'
    r'|(?:il|la|lo|le|i|gli)\s+'
    r')?'
)

_WEB_INSTRUCTION_RE = re.compile(
    r'(?:\b(?:cerca|cerchi|ricerca|ricerchi|guarda|controlla|verifica|trova|'
    r'cercami|dimmi|dammi)\s+' + _WEB_TARGET +
    r'(?:web|internet|online|rete)\b'
    r'|/\s*web\b'
    r'|\b(?:search|look\s+up|google|browse|find|check)\s+(?:the\s+|it\s+)?'
    r'(?:web|online|internet)\b)',
    re.IGNORECASE,
)

# Query che richiedono dati live senza menzione esplicita di web search
_LIVE_DATA_RE = re.compile(
    # Meteo / previsioni
    r'\b(?:meteo|previsioni?\s*(?:del\s+tempo|meteo)?|'
    r'temperatura\s+(?:attuale|a|di|oggi)|'
    r'che\s+tempo\s+(?:fa|farà|ha\s+fatto)|com\'?è\s+il\s+tempo|'
    r'weather\b|forecast)\b'
    # News / attualità
    r'|\b(?:notizie|ultime\s+notizie|breaking\s+news|news\s+(?:di\s+oggi|about|on)|'
    r'chi\s+ha\s+vinto|cosa\s+è\s+successo|ultime\s+novità|attualità|'
    r'aggiornamenti\s+(?:sul|sulla|sui))\b'
    # Prezzi / mercati / valute
    r'|\b(?:prezzo\s+(?:di|del|della|attuale)|quanto\s+costa|'
    r'quotazione|tasso\s+di\s+cambio|exchange\s+rate|'
    r'price\s+of|how\s+much\s+(?:is|are|does)|stock\s+price|'
    r'bitcoin|ethereum|criptovalut[ae])\b'
    # Trasporti / viaggi
    r'|\b(?:voli?\s+(?:per|da|verso)|treni?\s+(?:per|da|verso)|orari\s+dei?\s+'
    r'(?:treni|voli|autobus)|flights?\s+(?:to|from)|trains?\s+(?:to|from))\b'
    # Sport
    r'|\b(?:risultato\s+(?:della\s+|del\s+)?partita|classifica\s+di\s+\w+|'
    r'match\s+(?:score|result)|score\s+of)\b'
    # Varie live
    r'|\b(?:in\s+tempo\s+reale|live\s+(?:updates?|now)|orario\s+(?:di|dei|delle))\b',
    re.IGNORECASE,
)

# Frasi di istruzione da rimuovere prima della query di ricerca (SearXNG)
_WEB_PREFIX_STRIP_RE = re.compile(
    r'^(?:\s*(?:cerca|cerchi|ricerca|ricerchi|guarda|controlla|verifica|trova|'
    r'cercami)\s+' + _WEB_TARGET +
    r'(?:web|internet|online|rete)\s*'
    r'|/\s*web\s+'
    r'|\s*(?:search|look\s+up|google|browse|find|check)\s+(?:the\s+)?'
    r'(?:web|online|internet)\s*)',
    re.IGNORECASE,
)


def is_web_requiring_query(message: str) -> bool:
    """True se la richiesta richiede dati live/web (meteo, news, prezzi, ricerca esplicita).

    Usato per attivare la web search nel branch "general" della pipeline,
    dove normalmente si risponde immediatamente senza contesto.
    """
    if not message or not message.strip():
        return False
    msg = message.strip()
    return bool(_WEB_INSTRUCTION_RE.search(msg) or _LIVE_DATA_RE.search(msg))


def clean_web_query(message: str) -> str:
    """Rimuove le frasi di istruzione (\"cerca sul web...\") per ottenere una query di ricerca pulita."""
    if not message:
        return ""
    msg = _WEB_PREFIX_STRIP_RE.sub("", message).strip()
    return msg or message.strip()



async def perform_web_search_and_crawl(user_message, force=False):
    """
    Se il messaggio inizia con '/web ', esegue ricerca web e scraping.
    Con force=True ignora il prefisso /web (auto web discovery).
    Restituisce (contesto_web, messaggio_pulito).
    """
    if not force and not user_message.startswith("/web "):
        return None, user_message

    query = user_message[5:].strip() if user_message.startswith("/web ") else user_message.strip()
    try:
        searx_resp = await state.http_client.get(
            f"{SEARXNG_HOST}/search",
            params={"q": query, "format": "json"},
            timeout=30.0
        )
        if searx_resp.status_code != 200:
            return None, user_message

        results = searx_resp.json().get("results", [])[:3]
        if not results:
            return "Nessun risultato online.", user_message

        urls_to_crawl = [r.get("url") for r in results if r.get("url")]

        async def crawl_worker(url):
            try:
                headers = {}
                if CRAWL4AI_API_TOKEN:
                    headers["Authorization"] = f"Bearer {CRAWL4AI_API_TOKEN}"
                res = await state.http_client.post(
                    f"{CRAWL4AI_HOST}/crawl",
                    json={"urls": [url]},
                    headers=headers,
                    timeout=15.0
                )
                if res.status_code == 200:
                    data = res.json()

                    def extract_md(res_dict):
                        md = res_dict.get("markdown", "")
                        if isinstance(md, dict):
                            md = md.get("fit_markdown") or md.get("raw_markdown", "")
                        return str(md or "")[:3000]

                    if "results" in data and data["results"]:
                        return extract_md(data["results"][0])
            except Exception as e:
                logger.warning(f"Errore caricamento search results page: {e}")
            return ""

        markdown_contents = await asyncio.gather(*(crawl_worker(url) for url in urls_to_crawl))
        pieces = [
            f"FONTE: {res.get('title')}\nURL: {res.get('url')}\n"
            f"DATI: {md.strip() if md.strip() else res.get('snippet')}"
            for res, md in zip(results, markdown_contents)
        ]
        return "\n---\n".join(pieces), user_message.replace("/web ", "").strip()
    except Exception as e:
        logger.warning(f"Errore in web_search: {e}")
        return None, user_message
