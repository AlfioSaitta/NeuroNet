"""
Reranker: Qwen3-Reranker su CPU (priority), FlashRank fallback.
Estratto da rag.py per modularizzazione.

Entrambi girano su CPU: Qwen3 usa transformers, FlashRank usa ONNX.
Qwen3 offre multilingua (100+ lingue, incluso italiano) e punteggio MTEB-Code 73.42.
"""

import os
import logging

from config import (
    QENABLED_QWEN3_RERANKER,
    Qwen3_RERANKER_MODEL,
    RERANKER_DEVICE,
    FLASHRANK_MODEL,
    DATA_DIR,
)

logger = logging.getLogger(__name__)

os.environ.setdefault("TORCHDYNAMO_DISABLE", "1")


# ==============================================================================
# RERANKER STRATEGY:
#   1. FlashRank ONNX (leggero e veloce, sempre disponibile)
#   2. Se QENABLED_QWEN3_RERANKER=true E la directory esiste, prova Qwen3 (migliore qualità)
# ==============================================================================

_reranker = None
_use_qwen3_reranker = QENABLED_QWEN3_RERANKER and os.path.isdir(Qwen3_RERANKER_MODEL)

if _use_qwen3_reranker:
    try:
        import torch
        from transformers import AutoTokenizer, AutoModelForCausalLM

        _device = torch.device(RERANKER_DEVICE)
        _tok = AutoTokenizer.from_pretrained(Qwen3_RERANKER_MODEL, padding_side='left', trust_remote_code=True)
        _model = AutoModelForCausalLM.from_pretrained(
            Qwen3_RERANKER_MODEL,
            dtype=torch.float16,
            low_cpu_mem_usage=True,
            trust_remote_code=True
        ).to(_device).eval()

        _yes_id = _tok.convert_tokens_to_ids("yes")
        _no_id = _tok.convert_tokens_to_ids("no")
        logger.info(f"🔀 Reranker: Qwen3-Reranker su {RERANKER_DEVICE} ({Qwen3_RERANKER_MODEL})")

        def _reranker_fn(query, passages):
            texts = [f"Query: {query}\nDocument: {p.get('text', '')}\nRelevance:" for p in passages]
            inputs = _tok(texts, padding=True, truncation=True, max_length=8192, return_tensors="pt").to(_device)
            with torch.no_grad():
                logits = _model(**inputs).logits[:, -1, :]
            scores = torch.softmax(torch.stack([logits[:, _no_id], logits[:, _yes_id]], dim=-1), dim=-1)[:, 1]
            for p, s in zip(passages, scores.tolist()):
                p["score"] = round(s, 4)
            return sorted(passages, key=lambda x: x["score"], reverse=True)

        _reranker = _reranker_fn

    except Exception as e:
        logger.warning(f"Qwen3-Reranker errore ({e}), fallback su FlashRank...")
        _use_qwen3_reranker = False

if not _use_qwen3_reranker:
    try:
        from flashrank import Ranker, RerankRequest
        _flash = Ranker(model_name=FLASHRANK_MODEL, cache_dir=os.path.join(DATA_DIR, "flashrank_cache"))

        def _reranker_fn(query, passages):
            req = RerankRequest(query=query, passages=passages)
            return _flash.rerank(req)

        _reranker = _reranker_fn
        logger.info(f"🔀 Reranker: FlashRank ({FLASHRANK_MODEL})")
    except Exception as e2:
        logger.warning(f"FlashRank non caricabile ({e2}). Reranker disattivato.")
