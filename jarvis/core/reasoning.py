"""
Reasoning Metadata & Agent Configuration Engine.

Integra ModelProfile + GatekeeperResult per decidere se attivare il
ragionamento complesso (thinking) durante la generazione LLM, e converte
l'output streaming in blocchi <details> HTML per UI esterne.

Elimina qualsiasi controllo manuale sul testo (liste saluti hardcoded)
usando lo stato del Gatekeeper per ogni decisione architetturale.
"""

import json
import logging
from typing import AsyncGenerator, Optional

from core.llm_engine import GatekeeperResult
from core.model_profiles import ModelProfile

logger = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════════
# REASONING METADATA — per-famiglia
# ════════════════════════════════════════════════════════════════
# Mappa i metadati di reasoning specifici per famiglia modello:
#   start_tag / end_tag: delimitatori di pensiero nel token stream
#   stop_token_id:       ID del token di start, da bloccare via logit_bias
#                        quando il reasoning è disabilitato
#   no_think_prefix:     prefisso testuale da prependere al prompt utente
#                        per modelli che lo supportano (es. Qwen /no_think)
#   penalty_tokens:      token addizionali da penalizzare quando reasoning off

REASONING_METADATA: dict[str, dict] = {
    "qwen": {
        "start_tag": "<think>",
        "end_tag": "</think>",
        "stop_token_id": 151649,      # <think> token ID in Qwen3.5
        "no_think_prefix": "/no_think ",
        "penalty_tokens": [],
    },
    "gemma": {
        "start_tag": "<|think|>",
        "end_tag": "<|not_think|>",
        "stop_token_id": 106,          # <|think|> control token in Gemma 4
        "no_think_prefix": "",
        "penalty_tokens": [],
    },
    "deepseek": {
        "start_tag": "<think>",
        "end_tag": "</think>",
        "stop_token_id": 151646,       # <think> token ID in DeepSeek
        "no_think_prefix": "/no_think ",
        "penalty_tokens": [],
    },
    "qwq": {
        "start_tag": "<think>",
        "end_tag": "</think>",
        "stop_token_id": 151649,
        "no_think_prefix": "/no_think ",
        "penalty_tokens": [],
    },
    "llama": {
        "start_tag": "",
        "end_tag": "",
        "stop_token_id": None,
        "no_think_prefix": "",
        "penalty_tokens": [],
    },
    "mistral": {
        "start_tag": "",
        "end_tag": "",
        "stop_token_id": None,
        "no_think_prefix": "",
        "penalty_tokens": [],
    },
    "phi": {
        "start_tag": "",
        "end_tag": "",
        "stop_token_id": None,
        "no_think_prefix": "",
        "penalty_tokens": [],
    },
    "command-r": {
        "start_tag": "",
        "end_tag": "",
        "stop_token_id": None,
        "no_think_prefix": "",
        "penalty_tokens": [],
    },
    "unknown": {
        "start_tag": "",
        "end_tag": "",
        "stop_token_id": None,
        "no_think_prefix": "",
        "penalty_tokens": [],
    },
}


def get_reasoning_meta(family: str) -> dict:
    """Restituisce i metadati di reasoning per la famiglia modello.

    Fallback sicuro per famiglie sconosciute (tag vuoti, nessun token block).
    """
    return REASONING_METADATA.get(family.lower(), REASONING_METADATA["unknown"])


# ════════════════════════════════════════════════════════════════
# CONFIGURAZIONE RICHIESTA AGENTE
# ════════════════════════════════════════════════════════════════

def _is_web_requiring_query(user_input: str) -> bool:
    """True se la richiesta richiede dati live/web (meteo, news, prezzi).

    Import lazy + try/except per sicurezza (convenzione codebase): nessun
    modulo RAG deve poter impedire il caricamento di core.reasoning.
    """
    try:
        from rag.web_search import is_web_requiring_query
        return is_web_requiring_query(user_input)
    except Exception:
        return False


def configura_richiesta_agente(
    profile: ModelProfile,
    gatekeeper: Optional[GatekeeperResult],
    user_input: str,
) -> tuple:
    """Configura la richiesta LLM basandosi su ModelProfile + GatekeeperResult.

    Il ragionamento viene attivato SOLO se:
    - il modello lo supporta (profile.thinking_support == True)
    - E l'intento dell'utente è strutturato ("project" o "meta")
    - OPPURE la richiesta richiede dati live/web (meteo, news, prezzi)

    Se l'intento è "general", il ragionamento viene spento per evitare
    cicli inutili su saluti e chiacchiere. Il blocco fisico avviene tramite
    logit_bias[stop_token_id] = -100 per i modelli nativamente reasoning.

    ECCEZIONE: le web queries (dati live) mantengono il reasoning attivo e
    NON ricevono il prefisso /no_think: il modello deve sintetizzare il
    contesto [WEB] fresco invece di rispondere dalla conoscenza interna
    stantia.

    Args:
        profile: Profilo del modello attualmente caricato.
        gatekeeper: Risultato della classificazione intento (None = default general).
        user_input: Testo originale dell'ultimo messaggio utente.

    Returns:
        (content_prompt, chat_template_kwargs, settings):
            content_prompt:       Testo da usare come contenuto utente (con
                                  eventuale prefisso no_think).
            chat_template_kwargs: Dict da passare a create_chat_completion
                                  per il template di chat (enable_thinking).
            settings:             Dict di parametri di generazione (temperature,
                                  top_p, logit_bias, ecc.).
    """
    meta = get_reasoning_meta(profile.family)
    intent = gatekeeper.intent if gatekeeper else "general"

    # Le web queries trasportano contesto [WEB] fresco nel prompt: il
    # reasoning va mantenuto attivo per sintetizzare i dati live.
    web_query = _is_web_requiring_query(user_input)

    # Decisione architetturale basata sul Gatekeeper
    with_reasoning = profile.thinking_support and (intent in ("project", "meta") or web_query)

    if with_reasoning:
        # Configurazione "Calda" per ragionamento logico strutturato
        content_prompt = user_input
        chat_template_kwargs = {"enable_thinking": True}
        settings = {
            "temperature": 1.0,
            "top_p": 0.95,
            "presence_penalty": 1.5,
            "repetition_penalty": 1.0,
            "logit_bias": {},
        }
        logger.info(
            "🧠 Reasoning ATTIVO (intent=%s, family=%s, web_query=%s): T=1.0, top_p=0.95",
            intent, profile.family, web_query,
        )
    else:
        # Intento generale/saluti o modello senza supporto reasoning.
        # Le web queries non ricevono MAI il prefisso /no_think: porterebbe
        # il modello a ignorare il contesto [WEB] e rispondere dalla memoria.
        content_prompt = user_input if web_query else f"{meta['no_think_prefix']}{user_input}"
        chat_template_kwargs = {"enable_thinking": False}

        # Blocco fisico del token di pensiero tramite logit_basis
        # per modelli nativamente reasoning (es. Gemma 4, DeepSeek)
        logit_bias: dict = {}
        if profile.thinking_support and meta.get("stop_token_id") is not None:
            logit_bias[int(meta["stop_token_id"])] = -100
            logger.debug(
                "🔇 Reasoning DISABILITATO: logit_bias[token=%s] = -100",
                meta["stop_token_id"],
            )

        settings = {
            "temperature": profile.default_temperature,
            "top_p": profile.default_top_p,
            "repeat_penalty": profile.default_repeat_penalty,
            "presence_penalty": 1.5 if profile.thinking_support else 0.0,
            "logit_bias": logit_bias,
        }
        logger.info(
            "🔇 Reasoning SPENTO (intent=%s, family=%s, web_query=%s): T=%s, logit_bias=%s",
            intent, profile.family, web_query, profile.default_temperature,
            bool(logit_bias),
        )

    return content_prompt, chat_template_kwargs, settings


# ════════════════════════════════════════════════════════════════
# STREAMING PARSER CON GESTIONE ANOMALIE (TOKEN SPLITTING)
# ════════════════════════════════════════════════════════════════

def _make_chunk(content: str, template_chunk: dict) -> dict:
    """Costruisce un chunk OpenAI valido con il contenuto dato.

    Usa il template_chunk come base per mantenere id/created/model coerenti.
    """
    return {
        "id": template_chunk.get("id", ""),
        "object": "chat.completion.chunk",
        "created": template_chunk.get("created", 0),
        "model": template_chunk.get("model", ""),
        "choices": [
            {
                "index": 0,
                "delta": {"content": content},
                "finish_reason": None,
            }
        ],
    }


async def genera_stream_agente(
    raw_stream: AsyncGenerator,
    profile: ModelProfile,
) -> AsyncGenerator[dict, None]:
    """Wraps un async generator LLM raw, convertendo i tag di thinking in
    blocchi HTML <details> per UI tipo Cherry Studio.

    Gestisce il Token Splitting: se i tag (<think>, </think>, <|think|>)
    arrivano frammentati su chunk adiacenti, vengono ricostruiti correttamente
    grazie al buffer interno prima della conversione in HTML.

    Args:
        raw_stream: Async generator di chunk OpenAI da engine.generate_chat().
        profile: Profilo del modello per risolvere i tag di famiglia.

    Yields:
        Chunk OpenAI dict con choices[0].delta.content contenente il testo
        già elaborato (think → <details>) o passato-through.
    """
    meta = get_reasoning_meta(profile.family)
    start_tag = meta["start_tag"]
    end_tag = meta["end_tag"]

    # Se il modello non supporta thinking, pass-through senza elaborazione
    if not profile.thinking_support or not start_tag or not end_tag:
        async for chunk in raw_stream:
            yield chunk
        return

    buffer = ""
    # Flag per sapere se siamo DENTRO un blocco di pensiero
    in_think_block = False

    async for chunk in raw_stream:
        # Estrai il contenuto testuale dal chunk OpenAI
        content = ""
        if "choices" in chunk and len(chunk["choices"]) > 0:
            delta = chunk["choices"][0].get("delta", {})
            content = delta.get("content", "")

        if not content:
            # Chunk senza testo (role, tool_call, ecc.) → pass-through
            yield chunk
            continue

        buffer += content

        if in_think_block:
            # Siamo all'interno di un blocco di pensiero — cerchiamo end_tag
            if end_tag in buffer:
                reasoning_text, after = buffer.split(end_tag, 1)

                # Emetti il contenuto reasoning (dentro details)
                yield _make_chunk(reasoning_text, chunk)

                # Chiudi il tag details
                yield _make_chunk("\n</details>\n\n", chunk)

                # Il resto dopo end_tag è testo normale
                buffer = after
                in_think_block = False
            else:
                # Token splitting protection: se il buffer matcha l'inizio
                # di end_tag, aspettiamo il prossimo chunk
                if end_tag.startswith(buffer):
                    continue  # aspetta

                # Testo reasoning normale — emetti e svuota buffer
                yield _make_chunk(buffer, chunk)
                buffer = ""
            continue
        else:
            # Non siamo in un blocco di pensiero — cerchiamo start_tag
            if start_tag in buffer:
                before, after = buffer.split(start_tag, 1)

                # Emetti il testo prima del tag (se presente)
                if before:
                    yield _make_chunk(before, chunk)

                # Apri il tag details per il reasoning
                yield _make_chunk(
                    "<details>\n<summary>Pensiero (Ragionamento)</summary>\n\n",
                    chunk,
                )

                buffer = after
                in_think_block = True

                # Se dopo start_tag c'è già end_tag (caso raro: think vuoto)
                if end_tag and end_tag in buffer:
                    reasoning_text, after = buffer.split(end_tag, 1)
                    yield _make_chunk(reasoning_text, chunk)
                    yield _make_chunk("\n</details>\n\n", chunk)
                    buffer = after
                    in_think_block = False
                continue
            else:
                # Token splitting protection: il buffer potrebbe essere
                # l'inizio di start_tag o end_tag
                if (start_tag and start_tag.startswith(buffer)) or \
                   (end_tag and end_tag.startswith(buffer)):
                    continue  # aspetta il prossimo chunk per vedere

                # Testo normale — emetti e svuota buffer
                yield _make_chunk(buffer, chunk)
                buffer = ""

    # Flush eventuale buffer residuo
    if buffer:
        if in_think_block:
            # Chiudi il blocco reasoning forzatamente
            yield _make_chunk(buffer, {"choices": [{"delta": {"content": ""}}]})
            yield _make_chunk(
                "\n</details>\n\n",
                {"choices": [{"delta": {"content": ""}}]},
            )
        else:
            yield _make_chunk(buffer, {"choices": [{"delta": {"content": ""}}]})
