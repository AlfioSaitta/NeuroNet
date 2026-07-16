"""
Web Intelligence — SearXNG metasearch + Crawl4AI scraping in parallelo.
"""

import asyncio

from config import SEARXNG_HOST, CRAWL4AI_HOST, CRAWL4AI_API_TOKEN, logger
import state


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
