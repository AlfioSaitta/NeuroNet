"""
Pipeline Telemetry — tracciamento strutturato del flusso di processamento input.

Fornisce PipelineTracer per tracciare ogni richiesta utente attraverso i 4 step:
1. Keyword Bypass       (STEP 1, 0 LLM)
2. Qwen3.5 Gatekeeper   (STEP 2, CPU)
3. Caveman Compression  (STEP 3, CPU)
4. Gemma 4 Generation   (STEP 4, GPU)

Ogni trace finito viene inserito in un ring buffer circolare in memoria
(state.pipeline_traces) per essere esposto dal server MCP ad agenti AI esterni.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import time
import uuid
from collections import deque
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# Step Data
# ──────────────────────────────────────────────

@dataclasses.dataclass
class StepRecord:
    """Dati di un singolo step della pipeline."""
    name: str
    duration_ms: float
    status: str  # "ok" | "skipped" | "error"
    details: dict[str, Any] = dataclasses.field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "duration_ms": round(self.duration_ms, 1),
            "status": self.status,
            **self.details,
        }


@dataclasses.dataclass
class LlmCallRecord:
    """Dati di una chiamata LLM (Gatekeeper Qwen / Compression Qwen / Gemma)."""
    model: str  # "gatekeeper" | "chat"
    step: str   # nome step che ha chiamato il LLM
    duration_ms: float
    tokens_prompt: int = 0
    tokens_completion: int = 0
    temperature: float = 0.0
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "model": self.model,
            "step": self.step,
            "duration_ms": round(self.duration_ms, 1),
            "tokens_prompt": self.tokens_prompt,
            "tokens_completion": self.tokens_completion,
            "temperature": self.temperature,
            "error": self.error,
        }


@dataclasses.dataclass
class PipelineTrace:
    """
    Trace completo di una richiesta utente attraverso la pipeline.
    Dopo il completamento viene inserito in state.pipeline_traces (ring buffer).
    """
    request_id: str
    user_message: str          # troncato a 200 caratteri
    user_id: str
    timestamp: float           # time.time()
    total_duration_ms: float
    steps: list[StepRecord] = dataclasses.field(default_factory=list)
    llm_calls: list[LlmCallRecord] = dataclasses.field(default_factory=list)
    gatekeeper: Optional[dict] = None
    error: Optional[str] = None
    tool_calls_count: int = 0

    def to_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "user_message": self.user_message,
            "user_id": self.user_id,
            "timestamp": self.timestamp,
            "datetime": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.timestamp)),
            "total_duration_ms": round(self.total_duration_ms, 1),
            "steps": [s.to_dict() for s in self.steps],
            "llm_calls": [c.to_dict() for c in self.llm_calls],
            "gatekeeper": self.gatekeeper,
            "error": self.error,
            "tool_calls_count": self.tool_calls_count,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False, default=str)


# ──────────────────────────────────────────────
# Pipeline Tracer
# ──────────────────────────────────────────────

class PipelineTracer:
    """
    Tracciamento strutturato della pipeline di una singola richiesta.

    Usage:
        tracer = PipelineTracer.begin(user_message="...", user_id="...")
        tracer.start_step("keyword_bypass")
        ... esegue step ...
        tracer.end_step("keyword_bypass", status="ok", details={"bypassed": True})
        ...
        tracer.finish()
        # → automaticamente inserito in state.pipeline_traces
    """

    _active_traces: dict[str, PipelineTracer] = {}
    """Trace correntemente in esecuzione, keyati per request_id."""

    def __init__(self, request_id: str, user_message: str, user_id: str):
        self.request_id = request_id
        self._user_message = user_message[:200]
        self._user_id = user_id
        self._start_time = time.monotonic()
        self._steps: list[StepRecord] = []
        self._llm_calls: list[LlmCallRecord] = []
        self._current_step: Optional[_StepTimer] = None
        self._gatekeeper: Optional[dict] = None
        self._error: Optional[str] = None
        self._tool_calls_count: int = 0
        self._finished: bool = False

    # ── Factory ─────────────────────────────────

    @classmethod
    def begin(cls, user_message: str = "", user_id: str = "") -> PipelineTracer:
        """
        Crea un nuovo tracer e lo registra in _active_traces.

        Returns:
            PipelineTracer già associato a un request_id univoco.
        """
        request_id = uuid.uuid4().hex[:12]
        obj = cls(request_id, user_message, user_id)
        cls._active_traces[request_id] = obj
        logger.info(f"[TRACE {request_id}] ▶️ Begin | user='{user_id}' msg='{user_message[:80]}'")
        return obj

    @classmethod
    def get(cls, request_id: str) -> Optional[PipelineTracer]:
        """Recupera un tracer attivo per request_id."""
        return cls._active_traces.get(request_id)

    @classmethod
    def get_all_active(cls) -> list[dict]:
        """Lista di tutti i trace attualmente in esecuzione (per MCP)."""
        return [
            {"request_id": rid, "elapsed_ms": round((time.monotonic() - t._start_time) * 1000, 1)}
            for rid, t in cls._active_traces.items()
            if not t._finished
        ]

    # ── Step Management ─────────────────────────

    def start_step(self, name: str):
        """Avvia un nuovo step. Se c'è già uno step aperto, lo chiude forzatamente."""
        if self._current_step is not None:
            self._force_close_current_step()
        self._current_step = _StepTimer(name)

    def end_step(self, name: str, status: str = "ok", details: Optional[dict] = None):
        """Chiude lo step corrente e lo registra."""
        if self._current_step is None:
            logger.warning(f"[TRACE {self.request_id}] end_step('{name}') senza start_step")
            return
        if self._current_step.name != name:
            logger.warning(
                f"[TRACE {self.request_id}] end_step('{name}') ma step corrente è '{self._current_step.name}'"
            )
            self._force_close_current_step()
            return
        duration = self._current_step.finish()
        record = StepRecord(
            name=name,
            duration_ms=duration,
            status=status,
            details=details or {},
        )
        self._steps.append(record)
        self._current_step = None

        # Log strutturato su logger standard
        _log_step(record, self.request_id)

    def step(
        self, name: str, status: str = "ok", details: Optional[dict] = None
    ) -> "PipelineTracer":
        """Registra uno step istantaneo (per decisioni semplici, senza attesa)."""
        record = StepRecord(name=name, duration_ms=0.0, status=status, details=details or {})
        self._steps.append(record)
        _log_step(record, self.request_id)
        return self

    def _force_close_current_step(self):
        """Chiude forzatamente lo step corrente in stato 'error'."""
        cs = self._current_step
        duration = cs.finish()
        record = StepRecord(name=cs.name, duration_ms=duration, status="interrupted", details={})
        self._steps.append(record)
        logger.warning(f"[TRACE {self.request_id}] Step '{cs.name}' interrotto forzatamente dopo {duration:.0f}ms")
        self._current_step = None

    # ── LLM Calls Tracking ──────────────────────

    def add_llm_call(self, record: LlmCallRecord):
        """Registra una chiamata LLM."""
        self._llm_calls.append(record)
        token_info = f" ({record.tokens_prompt}→{record.tokens_completion}tok)" if record.tokens_prompt else ""
        logger.info(
            f"[TRACE {self.request_id}] 🤖 LLM {record.step}: "
            f"model={record.model} dur={record.duration_ms:.0f}ms{token_info}"
            + (f" ERR={record.error}" if record.error else "")
        )

    # ── Metadata ────────────────────────────────

    def set_gatekeeper(self, intent: str, project: Optional[str], confidence: float, bypassed: bool):
        """Registra il risultato del Gatekeeper (STEP 1+2)."""
        self._gatekeeper = {
            "intent": intent,
            "project": project,
            "confidence": round(confidence, 2),
            "bypassed": bypassed,
        }
        logger.info(
            f"[TRACE {self.request_id}] 🧠 Gatekeeper: {intent} "
            f"proj={project} conf={confidence:.2f} bypass={bypassed}"
        )

    def set_error(self, error: str):
        """Registra un errore a livello di trace."""
        self._error = error
        logger.error(f"[TRACE {self.request_id}] ❌ Error: {error}")

    def increment_tool_calls(self, n: int = 1):
        """Incrementa il contatore tool calls."""
        self._tool_calls_count += n

    # ── Finalize ────────────────────────────────

    def finish(self) -> Optional[PipelineTrace]:
        """
        Finalizza il trace e lo inserisce nel ring buffer state.pipeline_traces.

        Returns:
            PipelineTrace completato, oppure None se già finito.
        """
        if self._finished:
            return None
        self._finished = True

        # Chiudi eventuale step ancora aperto
        if self._current_step is not None:
            self._force_close_current_step()

        total_duration = (time.monotonic() - self._start_time) * 1000
        trace = PipelineTrace(
            request_id=self.request_id,
            user_message=self._user_message,
            user_id=self._user_id,
            timestamp=time.time(),
            total_duration_ms=total_duration,
            steps=self._steps,
            llm_calls=self._llm_calls,
            gatekeeper=self._gatekeeper,
            error=self._error,
            tool_calls_count=self._tool_calls_count,
        )

        # Inserimento thread-safe nel ring buffer (state.pipeline_traces)
        _push_trace(trace)

        # Rimuovi dagli active traces
        PipelineTracer._active_traces.pop(self.request_id, None)

        logger.info(
            f"[TRACE {self.request_id}] ✅ Done | {len(self._steps)} steps | "
            f"{len(self._llm_calls)} LLM calls | {total_duration:.0f}ms total"
        )
        return trace


# ──────────────────────────────────────────────
# Internal Helpers
# ──────────────────────────────────────────────

class _StepTimer:
    """Timer interno per misurare la durata di uno step."""
    __slots__ = ("name", "_start")

    def __init__(self, name: str):
        self.name = name
        self._start = time.monotonic()

    def finish(self) -> float:
        """Restituisce la durata in millisecondi."""
        return (time.monotonic() - self._start) * 1000


def _log_step(record: StepRecord, request_id: str):
    """Scrive un record di step sul logger standard con formato strutturato."""
    details_str = ""
    if record.details:
        # Mostra solo i dettagli più importanti (max 3 chiavi, max 80 char totali)
        compact = {k: v for k, v in record.details.items() if v is not None}
        if compact:
            parts = []
            for k, v in list(compact.items())[:3]:
                v_str = str(v)[:40]
                parts.append(f"{k}={v_str}")
            if len(compact) > 3:
                parts.append(f"...+{len(compact)-3}")
            details_str = " | " + " ".join(parts)

    status_icon = {"ok": "✅", "skipped": "⏭️", "error": "❌", "interrupted": "⚠️"}.get(
        record.status, "❓"
    )
    logger.info(
        f"[TRACE {request_id}] {status_icon} Step {record.name}: "
        f"{record.duration_ms:.0f}ms [{record.status}]{details_str}"
    )


def _push_trace(trace: PipelineTrace):
    """Inserisce il trace nel ring buffer state.pipeline_traces (thread-safe)."""
    try:
        import state
        state.pipeline_traces.append(trace)
    except Exception as exc:
        logger.warning(f"telemetry: impossibile scrivere trace in state: {exc}")


# ──────────────────────────────────────────────
# Convenience function per main.py / callers
# ──────────────────────────────────────────────

def get_recent_traces(limit: int = 10) -> list[dict]:
    """Ultimi N trace completati, in formato dict (dal più recente)."""
    try:
        import state
        # pipeline_traces è un deque, il più recente è in fondo
        all_traces = list(state.pipeline_traces)
        recent = all_traces[-limit:] if limit else all_traces
        return [t.to_dict() for t in reversed(recent)]
    except Exception as exc:
        logger.warning(f"telemetry: get_recent_traces fallito: {exc}")
        return []


def get_trace_by_id(request_id: str) -> Optional[dict]:
    """Cerca un trace completato per request_id."""
    try:
        import state
        for t in state.pipeline_traces:
            if t.request_id == request_id:
                return t.to_dict()
    except Exception:
        pass
    return None


# ──────────────────────────────────────────────
# Gatekeeper Stats Collector
# ──────────────────────────────────────────────

class GatekeeperStats:
    """
    Statistiche cumulative del Gatekeeper.
    Mantenuto in memoria (state.gatekeeper_stats) ed esposto via MCP.
    """

    def __init__(self):
        self.total_classified: int = 0
        self.bypassed: int = 0      # STEP 1 ha matchato (0 LLM)
        self.llm_called: int = 0    # STEP 2 invocato (Qwen)
        self.by_intent: dict[str, int] = {}
        self.by_intent_with_bypass: dict[str, dict] = {}  # intent -> {bypass: N, llm: N}
        self.confidence_sum: float = 0.0
        self.confidence_count: int = 0

    def record(
        self, intent: str, confidence: float, bypassed: bool, project: Optional[str] = None
    ):
        self.total_classified += 1
        if bypassed:
            self.bypassed += 1
        else:
            self.llm_called += 1
        self.by_intent[intent] = self.by_intent.get(intent, 0) + 1

        bucket = self.by_intent_with_bypass.setdefault(intent, {"bypass": 0, "llm": 0})
        bucket["bypass" if bypassed else "llm"] += 1

        self.confidence_sum += confidence
        self.confidence_count += 1

    @property
    def avg_confidence(self) -> float:
        if self.confidence_count == 0:
            return 0.0
        return round(self.confidence_sum / self.confidence_count, 3)

    def to_dict(self) -> dict:
        return {
            "total_classified": self.total_classified,
            "bypassed": self.bypassed,
            "llm_called": self.llm_called,
            "by_intent": dict(self.by_intent),
            "by_intent_with_bypass": {
                k: dict(v) for k, v in self.by_intent_with_bypass.items()
            },
            "avg_confidence": self.avg_confidence,
        }

    def reset(self):
        self.__init__()
