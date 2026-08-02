"""
Context Compressor — compressione contesto caveman (Qwen3.5-0.8B su CPU).

Estratto da agent/prompt.py in Fase 5.5 (modulo autonomo con API compress()
invariata: skip < COMPRESSOR_MIN_CHARS, fallback raw, chiamata a
engine.compress_prompt). Zero cambi comportamentali.
"""

import logging

from core.llm_engine import engine
from core.config import logger  # noqa: F401  (ri-esportato per compatibilità)

__all__ = ["compress", "compress_concise", "COMPRESSOR_MIN_CHARS"]

COMPRESSOR_MIN_CHARS = 1000


async def compress(
    clean_msg: str, rag_context_for_compress: str, history_str: str,
    active_project: str | None, mem_final: str, tasks_final: str,
    rag_final: str, web_final: str,
) -> tuple[str, bool]:
    """Compress context via Qwen3.5 caveman, fall back to raw labels on failure.

    Salta completamente la compressione LLM se il contesto è trascurabile
    (< 1000 chars totali tra RAG, history e web). Questa è l'ottimizzazione
    principale per query semplici (data, ora, meteo, etc.) dove non c'è nulla
    da comprimere — risparmia 10-50s per richiesta.

    Returns (compressed_text, is_raw_fallback).
    """
    # ── Skip compressor se contesto trascurabile (Op1/Op8) ──
    total_context = len(rag_context_for_compress or '') + len(history_str or '') + len(web_final or '')
    if total_context < COMPRESSOR_MIN_CHARS and not rag_final and not active_project:
        logger.info(f"🗜️ Skip compressor: contesto trascurabile ({total_context}ch < {COMPRESSOR_MIN_CHARS}ch), raw fallback")
        is_raw = True
        fallback_parts = []
        if mem_final:
            fallback_parts.append(f"Memory: {mem_final[:500]}")
        if tasks_final:
            fallback_parts.append(f"Tasks: {tasks_final[:300]}")
        if active_project:
            fallback_parts.append(f"Project: {active_project}")
        if web_final:
            fallback_parts.append(f"Web: {web_final[:500]}")
        fallback_parts.append(f"Query: {clean_msg}")
        compressed = "\n".join(fallback_parts)[:4096]
        return compressed, True

    compressed = await engine.compress_prompt(
        user_query=clean_msg,
        rag_context=rag_context_for_compress,
        history=history_str,
        active_project=active_project,
    )

    is_raw = False
    if not compressed or len(compressed) < 20:
        logger.warning("⚠️ Caveman compression fallita, uso fallback raw")
        is_raw = True
        fallback_parts = []
        if mem_final:
            fallback_parts.append(f"Memory: {mem_final[:500]}")
        if tasks_final:
            fallback_parts.append(f"Tasks: {tasks_final[:300]}")
        if active_project:
            fallback_parts.append(f"Project: {active_project}")
        if rag_final:
            fallback_parts.append(f"Context:\n{rag_final[:2000]}")
        if web_final:
            fallback_parts.append(f"Web: {web_final[:500]}")
        fallback_parts.append(f"Query: {clean_msg}")
        compressed = "\n".join(fallback_parts)[:4096]

    if compressed.startswith("[PROJECT:") or compressed.startswith("[RAG_CONTEXT]"):
        is_raw = True
        logger.warning("⚠️ Caveman compression fallback raw (raw_data labels)")

    return compressed, is_raw


async def compress_concise(clean_msg: str) -> str:
    """Compressione concise-mode: solo la query utente, nessun contesto.

    Copre il 2° call site reale di engine.compress_prompt (parametri parziali,
    rag_context/history vuoti, active_project=None) — ramo CONCISE di
    build_omniscient_prompt. Zero cambi comportamentali.
    """
    return await engine.compress_prompt(
        user_query=clean_msg, rag_context="", history="", active_project=None,
    )
