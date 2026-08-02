"""
Dict builders per telemetry — fonte unica per model_info, status, pending_ops.
Elimina la duplicazione tra main.py (endpoint HTTP) e api/mcp/server_v2.py (MCP resources).
"""

import time
import logging
from typing import Any

logger = logging.getLogger(__name__)


def get_status_dict() -> dict:
    """Stato generale del sistema — uptime, richieste, token, trace."""
    import core.state as state
    from core.telemetry import PipelineTracer

    total_s = int(time.time() - state._start_time) if hasattr(state, '_start_time') else 0
    return {
        "uptime_seconds": total_s,
        "uptime_hours": round(total_s / 3600, 1) if total_s else 0,
        "total_requests": state.total_requests,
        "total_prompt_tokens": state.total_prompt_tokens,
        "total_completion_tokens": state.total_completion_tokens,
        "active_traces": len(PipelineTracer.get_all_active()),
        "pipeline_traces_capacity": getattr(state.pipeline_traces, 'maxlen', 500),
        "intent_initialized": state.intent_stats is not None,
        "error_count": len(state.error_counters),
    }


def get_model_info_dict() -> dict:
    """Informazioni sul modello LLM caricato — path, GPU layers, ctx, flash_attn, thinking."""
    from core.config import MODEL_ID as cfg_model_id

    info: dict[str, Any] = {
        "model_id": cfg_model_id,
        "model_path": None,
        "n_gpu_layers": 0,
        "n_ctx": 0,
        "n_batch": 0,
        "n_ubatch": 0,
        "flash_attn": False,
        "thinking_mode": False,
        "max_tokens": 2048,
        "compressor_model_loaded": False,
        "model_loaded": False,
        "detected_family": "unknown",
    }
    try:
        from core.config import (
            LLAMA_MODEL_PATH, N_GPU_LAYERS, LLM_NUM_CTX,
            LLM_BATCH_SIZE, LLM_UBATCH_SIZE, LLM_FLASH_ATTN,
            LLM_THINKING_MODE, LLM_MAX_TOKENS,
        )
        info["model_path"] = LLAMA_MODEL_PATH
        info["n_gpu_layers"] = N_GPU_LAYERS
        info["n_ctx"] = LLM_NUM_CTX
        info["n_batch"] = LLM_BATCH_SIZE
        info["n_ubatch"] = LLM_UBATCH_SIZE
        info["flash_attn"] = LLM_FLASH_ATTN
        info["thinking_mode"] = LLM_THINKING_MODE
        info["max_tokens"] = LLM_MAX_TOKENS
    except Exception:
        pass
    try:
        from core.llm_engine import engine
        if engine.chat_model is not None:
            info["model_loaded"] = True
        if engine.gatekeeper_model is not None:
            info["compressor_model_loaded"] = True
    except Exception:
        info["model_loaded"] = False
    try:
        from core.model_profiles import detect_model_family
        from core.config import LLAMA_MODEL_PATH
        family = detect_model_family(LLAMA_MODEL_PATH)
        info["detected_family"] = family.family if family else "unknown"
    except Exception:
        info["detected_family"] = "unknown"
    return info


def get_pending_ops_dict() -> dict:
    """Operazioni pendenti: background tasks, coda eventi watchdog."""
    import core.state as state

    bg_count = len(state.background_tasks)
    queue_size = state.file_event_queue.qsize() if hasattr(state, 'file_event_queue') else 0
    bg_task_names = []
    for t in list(state.background_tasks)[:20]:
        name = getattr(t, 'get_name', lambda: str(t))()
        bg_task_names.append(str(name)[:80])
    return {
        "background_tasks_count": bg_count,
        "background_tasks_sample": bg_task_names[:10],
        "file_event_queue_size": queue_size,
        "reindexing_in_progress": getattr(state, 'is_reindexing', False),
    }
