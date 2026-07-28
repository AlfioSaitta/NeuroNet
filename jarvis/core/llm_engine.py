import asyncio
import os
import re
import json
import time
import subprocess
import logging
from concurrent.futures import ThreadPoolExecutor
import numpy as np

import core.state as state
from core.config import LLM_THINKING_MODE, MODEL_PROFILE, EXTERNAL_GPU_URL, MODEL_ID, LLM_MAX_TOKENS, EMBEDDING_DIMS
from dataclasses import dataclass
from typing import Literal, Optional

try:
    from llama_cpp import Llama
except ImportError:
    Llama = None
    logging.warning("llama-cpp-python non installato. Il motore LLM locale non funzionerà.")

logger = logging.getLogger(__name__)

def log_vram_usage(label=""):
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total,temperature.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            parts = result.stdout.strip().split(", ")
            if len(parts) >= 2:
                used, total = parts[0], parts[1]
                temp = parts[2] if len(parts) >= 3 else "?"
                percent = int(used) / int(total) * 100 if int(total) > 0 else 0
                logger.info(f"🎯 [VRAM] {label} {used}MiB / {total}MiB ({percent:.0f}%) | GPU {temp}°C")
    except Exception:
        pass

import heapq

class PriorityLock:
    def __init__(self):
        self._waiters = []
        self._locked = False
        self._counter = 0

    async def acquire(self, priority: int):
        if not self._locked and not self._waiters:
            self._locked = True
            return

        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self._counter += 1
        heapq.heappush(self._waiters, (priority, self._counter, fut))
        try:
            await fut
        except asyncio.CancelledError:
            fut.cancel()
            raise

    def release(self):
        self._locked = False
        while self._waiters:
            _, _, fut = heapq.heappop(self._waiters)
            if not fut.done() and not fut.cancelled():
                self._locked = True
                fut.set_result(None)
                break

class PriorityLockTimeoutError(Exception):
    pass


@dataclass
class GatekeeperResult:
    """Risultato della classificazione intento a 3 stati."""
    intent: str  # "project" | "meta" | "general"
    project: str | None = None
    confidence: float = 0.0


CAVEMAN_COMPRESSOR_SYSTEM_PROMPT = """You are a data compressor. Compress the text below by removing fluff while keeping all technical details.

Example:
  INPUT:  "[PROJECT: MyApp]\n[RAG_CONTEXT]\nThe application uses React 18 with TypeScript 5 and has 25 database tables. The authentication system uses JWT tokens with refresh rotation."
  OUTPUT: "MyApp: React18+TS5, 25 DB tables, auth=JWT+refresh rotation."

RULES:
- Keep: project names, tech stack, numbers, versions, file paths, URLs, statuses.
- Remove: articles (the/a/an), filler phrases, polite greetings, transition verbs.
- Merge: related facts into single dense lines.
- NO thinking/reasoning — just output the compressed data directly.
- KEEP ALL code snippets, function names, and syntax intact.
- If already short (<200 chars), pass through unchanged."""

CAVEMAN_RESPONSE_INSTRUCTION = (
    "\n\nRespond concisely and naturally. No templates, no bullet-point lists, "
    "no greetings. Direct answers in plain prose. "
    "If writing code: output ONLY the code block."
)

class PriorityLockContextManager:
    def __init__(self, lock: PriorityLock, priority: int, timeout: float = 0):
        self.lock = lock
        self.priority = priority
        self.timeout = timeout

    async def __aenter__(self):
        if self.timeout > 0:
            try:
                await asyncio.wait_for(self.lock.acquire(self.priority), timeout=self.timeout)
            except asyncio.TimeoutError:
                raise PriorityLockTimeoutError(f"Lock acquisition timed out after {self.timeout}s")
        else:
            await self.lock.acquire(self.priority)

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.lock.release()

# Configurazione Singleton
class LlamaEngine:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(LlamaEngine, cls).__new__(cls)
            cls._instance.initialized = False
        return cls._instance

    def __init__(self):
        if self.initialized:
            return
            
        self.chat_model = None       # Qwen3.5-4B su GPU (main_brain)
        self.fastembed_model = None  # FastEmbed (ONNX CPU) per embedding
        self.gatekeeper_model = None # Qwen3.5-0.8B (classify + compress, CPU default)
        # Thread pool per non bloccare l'event loop di FastAPI (concurrency safe)
        self.executor = ThreadPoolExecutor(max_workers=8)
        # Lock separati: ogni modello Llama è indipendente, non devono bloccarsi
        self.chat_lock = PriorityLock()
        self.gatekeeper_lock = PriorityLock()
        self.initialized = True

    # ── Helper di caricamento modelli ────────────────────────────────

    def _load_chat_model(self, path: str) -> None:
        """Carica il modello chat principale con parametri auto-adattati alla famiglia.
        
        Gerarchia parametri (priorità decrescente):
        1. Valore esplicito in .env (es. N_GPU_LAYERS=15) 
        2. Default per famiglia modello (da _family_hardware_defaults)
        3. Default globale hardcoded (fallback estremo)
        
        CRITICO: N_GPU_LAYERS e flash_attn errati causano segfault o crash.
        """
        import os as _os
        
        # ── Step 1: Rileva famiglia modello PRIMA del caricamento ──
        from core.model_profiles import detect_model_family, _family_hardware_defaults
        _detected = detect_model_family(path)
        _hw_def = _family_hardware_defaults(_detected.family)
        
        # ── Step 2: Leggi ctx/batch da config (sempre da .env) ──
        from core.config import (
            LLM_NUM_CTX as _cfg_ctx,
            LLM_BATCH_SIZE as _cfg_batch,
            MODEL_PROFILE as _init_profile,
        )
        n_ctx = _cfg_ctx
        n_batch = _cfg_batch
        _chat_format = _init_profile.chat_format
        
        # ── Step 3: Risolvi parametri GPU con fallback gerarchico ──
        # Per ogni parametro: se esplicitamente impostato in .env → usalo,
        # altrimenti usa il default della famiglia modello.
        _env_n_gpu = _os.environ.get("N_GPU_LAYERS", "")
        _env_flash = _os.environ.get("LLM_FLASH_ATTN", "")
        _env_ubatch = _os.environ.get("LLM_UBATCH_SIZE", "")
        
        if _env_n_gpu.strip():
            n_gpu_layers = int(_env_n_gpu)
            _src_gpu = f".env={_env_n_gpu}"
        else:
            n_gpu_layers = _hw_def["n_gpu_layers"]
            _src_gpu = f"profilo {_detected.family}={_hw_def['n_gpu_layers']}"
        
        if _env_flash.strip():
            flash_attn = _env_flash.lower() == "true"
            _src_flash = f".env={_env_flash}"
        else:
            flash_attn = _hw_def["flash_attn"]
            _src_flash = f"profilo {_detected.family}={_hw_def['flash_attn']}"
        
        if _env_ubatch.strip():
            n_ubatch = int(_env_ubatch)
            _src_ubatch = f".env={_env_ubatch}"
        else:
            n_ubatch = _hw_def["n_ubatch"]
            _src_ubatch = f"profilo {_detected.family}={_hw_def['n_ubatch']}"
        
        logger.info(f"Caricamento Chat Model (MAIN BRAIN): {path}")
        logger.info(f"⚙️ [rilevato: {_detected.family}/{_detected.variant}] "
                    f"n_gpu_layers={n_gpu_layers} ({_src_gpu})")
        logger.info(f"⚙️ flash_attn={flash_attn} ({_src_flash}) "
                    f"n_ubatch={n_ubatch} ({_src_ubatch}) n_ctx={n_ctx}")
        logger.info(f"⚙️ chat_format={_chat_format}")
        
        self.chat_model = Llama(
            model_path=path,
            n_gpu_layers=n_gpu_layers, n_ctx=n_ctx,
            n_batch=n_batch, n_ubatch=n_ubatch,
            n_threads=4, flash_attn=flash_attn,
            use_mmap=True, chat_format=_chat_format, verbose=False,
            # embedding=True disabilitato: causa crash con n_gpu_layers=-1
            # su questo GGUF (fused_gated_delta_net). Usiamo fastembed invece.
        )
        log_vram_usage("Dopo caricamento Chat Model (Gemma 4)")

        # Estrazione metadati GGUF e aggiornamento MODEL_PROFILE
        try:
            _metadata = {}
            if hasattr(self.chat_model, 'metadata') and self.chat_model.metadata:
                _metadata = dict(self.chat_model.metadata)
            if hasattr(self.chat_model, 'model_metadata') and self.chat_model.model_metadata:
                _metadata = dict(self.chat_model.model_metadata)

            if _metadata:
                from core.model_profiles import detect_from_metadata
                from core.config import MODEL_PROFILE as _old_profile
                _new_profile = detect_from_metadata(_metadata, _old_profile)
                import core.config as _cfg
                _cfg.MODEL_PROFILE = _new_profile
                if not _cfg.LLM_THINKING_MODE_RAW:
                    _cfg.LLM_THINKING_MODE = _new_profile.thinking_support
                logger.info(f"🧠 Modello rilevato: {_new_profile.model_name} "
                            f"({_new_profile.family}/{_new_profile.variant}) "
                            f"chat_format={_new_profile.chat_format} | "
                            f"thinking={'✅' if _new_profile.thinking_support else '❌'}")
            else:
                logger.info("ℹ️  Nessun metadato GGUF disponibile, uso profilo da filename")
        except Exception as _meta_err:
            logger.warning(f"⚠️ Estrazione metadati modello fallita: {_meta_err}")

    def _load_gatekeeper_model(self, path: str) -> None:
        """Carica il modello gatekeeper (Qwen3.5) per intent classification + compressione."""
        from core.config import (
            GATEKEEPER_N_CTX as _gk_ctx,
            GATEKEEPER_N_THREADS as _gk_threads,
            GATEKEEPER_N_GPU_LAYERS as _gk_gpu,
        )
        _dev = "GPU" if _gk_gpu != 0 else "CPU"
        logger.info(f"Caricamento Gatekeeper Model ({_dev}): {path}")
        logger.info(f"⚙️ n_gpu_layers={_gk_gpu} n_ctx={_gk_ctx} n_threads={_gk_threads}")
        self.gatekeeper_model = Llama(
            model_path=path,
            n_gpu_layers=_gk_gpu, n_ctx=_gk_ctx,
            n_batch=512, n_ubatch=512, n_threads=_gk_threads,
            flash_attn=True, use_mmap=True, chat_format="chatml", verbose=False,
        )
        logger.info(f"✅ Gatekeeper Model caricato ({_dev})")
        try:
            logger.info("🔄 Warmup Gatekeeper Model (CPU first-call)...")
            self.gatekeeper_model.create_completion("warmup", max_tokens=1)
            logger.info("✅ Gatekeeper Model warmup completato")
        except Exception as e:
            logger.warning(f"⚠️ Gatekeeper Model warmup fallito (non critico): {e}")

    # ── Caricamento orchestrato ──────────────────────────────────────

    def load_models(self):
        """Carica tutti i modelli: embed (subprocess CPU), chat (GPU), gatekeeper (CPU).

        STRATEGIA:
        1. FastEmbed (ONNX CPU) per embedding → zero VRAM, zero subprocess.
           Modello: intfloat/multilingual-e5-base (768d, multilingua).
        2. Chat model (Qwen3.5-4B) → full GPU offload (N_GPU_LAYERS=-1).
        3. Gatekeeper model (Qwen3.5-0.8B) → CPU, nessuna competizione VRAM.
        """
        if Llama is None:
            logger.error("Impossibile caricare i modelli: llama-cpp-python mancante.")
            return

        from core.config import (
            LLAMA_MODEL_PATH as _cfg_model_path,
            GATEKEEPER_MODEL_PATH as _cfg_gk_path,
        )

        # 1. FastEmbed (ONNX CPU) per embedding — zero VRAM, zero subprocess
        try:
            from fastembed import TextEmbedding
            logger.info("📦 Caricamento FastEmbed (bge-base-en-v1.5, 768d)...")
            self.fastembed_model = TextEmbedding(
                model_name="BAAI/bge-base-en-v1.5",
                max_length=512,
                cache_dir="./models/fastembed_cache",
            )
            # Warmup: embed una frase breve
            _ = list(self.fastembed_model.embed(["warmup"]))
            logger.info("✅ FastEmbed pronto")
        except Exception as _fe_err:
            logger.warning(f"⚠️ FastEmbed non caricato (embedding non disponibile): {_fe_err}")
            self.fastembed_model = None

        # 2. Chat model su GPU (full offload)
        if os.path.exists(_cfg_model_path):
            self._load_chat_model(_cfg_model_path)
        else:
            logger.warning(f"File chat model {_cfg_model_path} non trovato!")

        # 3. Gatekeeper model su CPU — NON critico, graceful failure
        if os.path.exists(_cfg_gk_path):
            try:
                self._load_gatekeeper_model(_cfg_gk_path)
            except Exception as _gk_err:
                logger.warning(f"⚠️ Gatekeeper model non caricato (non critico): {_gk_err}")
        else:
            logger.warning(
                f"File gatekeeper model {_cfg_gk_path} non trovato! "
                "Gatekeeper e compressione disabilitati. "
                "Imposta GATEKEEPER_MODEL_PATH nel .env per abilitare."
            )

        log_vram_usage("VRAM finale dopo caricamento tutti i modelli")

    def _resolve_model(self, model: str):
        """Seleziona il modello Llama in base al nome logico."""
        if model == "gatekeeper":
            if not self.gatekeeper_model:
                raise RuntimeError("Gatekeeper model (Qwen3.5) non caricato — imposta GATEKEEPER_MODEL_PATH")
            return self.gatekeeper_model
        # default: main chat model (Gemma 4 GPU)
        if not self.chat_model:
            raise RuntimeError("Chat model (Gemma 4) non caricato")
        return self.chat_model

    def _resolve_lock(self, model: str) -> PriorityLock:
        """Seleziona il lock in base al modello."""
        if model == "gatekeeper":
            return self.gatekeeper_lock
        return self.chat_lock

    # ── Helper di generazione ────────────────────────────────────────

    @staticmethod
    def _inject_thinking_tag(model: str, messages: list) -> list:
        """Inietta il tag di thinking nel system prompt per modelli che lo supportano."""
        if model == "chat" and LLM_THINKING_MODE and MODEL_PROFILE.thinking_support and messages:
            _thinking_tag = "[Thinking]" if MODEL_PROFILE.chat_format == "gemma" else "<|think|>"
            processed = []
            for msg in messages:
                if isinstance(msg, dict) and msg.get("role") == "system":
                    content = msg.get("content", "")
                    if _thinking_tag not in content:
                        msg = {**msg, "content": f"{_thinking_tag}\n" + content}
                processed.append(msg)
            return processed
        return messages

    @staticmethod
    def _normalize_tools(tools: Optional[list], model: str) -> Optional[list]:
        """Normalizza tool in formato OpenAI per llama-cpp-python."""
        if not tools or model != "chat":
            return None
        normalized = []
        for t in tools:
            if isinstance(t, dict) and "function" in t:
                normalized.append(t)
            elif isinstance(t, dict) and "name" in t:
                normalized.append({
                    "type": "function",
                    "function": {
                        "name": t.get("name"),
                        "description": t.get("description", ""),
                        "parameters": t.get("parameters", {"type": "object", "properties": {}}),
                    },
                })
        return normalized or None

    @staticmethod
    def _build_external_payload(messages: list, tools: Optional[list], stream: bool, opts: dict) -> dict:
        """Costruisce il payload per la richiesta HTTP al Worker GPU remoto."""
        payload: dict = {
            "model": MODEL_ID,
            "messages": messages,
            "stream": stream,
            "options": {"skip_rag": True, **opts},
        }
        if tools:
            payload["tools"] = tools
        return payload

    async def generate_chat(self, messages, tools=None, options=None, stream=False, grammar=None, model="chat", priority=0):
        """Genera rispresa da un modello Llama.

        Args:
            messages: Lista di messaggi in formato OpenAI.
            tools: Tool definitions per function calling.
            options: Opzioni di generazione (temperature, max_tokens, ecc.).
            stream: Se True, restituisce un generatore asincrono.
            grammar: Grammatica GBNF per output strutturato.
            model: "chat" (Gemma 4 GPU) o "gatekeeper" (Qwen3.5 CPU).
            priority: Priorità lock (0=più alta, default). Gatekeeper usa 1.
        """
        try:
            llm = self._resolve_model(model)
        except RuntimeError as e:
            return {"error": str(e)}

        opts = options or {}

        messages = self._inject_thinking_tag(model, messages)

        temperature = opts.get("temperature", 1.0)
        max_tokens = opts.get("num_predict", 2048)
        presence_penalty = opts.get("presence_penalty", 0.1)
        frequency_penalty = opts.get("frequency_penalty", 0.1)
        repeat_penalty = opts.get("repeat_penalty", 1.1)
        top_p = opts.get("top_p", 0.9)
        top_k = opts.get("top_k", 40)
        response_format = opts.get("response_format")
        
        openai_tools = self._normalize_tools(tools, model)

        # --- DELEGAZIONE EXTERNAL GPU — solo per main chat model ---
        if model == "chat" and EXTERNAL_GPU_URL:
            try:
                payload = self._build_external_payload(messages, tools, stream, opts)
                client = state.http_client
                if client is None:
                    raise RuntimeError("http_client not initialized")

                logger.info(f"🚀 Offloading inferenza a {EXTERNAL_GPU_URL}...")

                if stream:
                    async def external_async_generator():
                        _role_sent = False
                        async with client.stream("POST", f"{EXTERNAL_GPU_URL.rstrip('/')}/api/chat", json=payload) as response:
                            response.raise_for_status()
                            async for line in response.aiter_lines():
                                if line:
                                    try:
                                        data = json.loads(line)
                                        choices = data.get("choices", [{}])
                                        content = choices[0].get("delta", {}).get("content", "") if choices else ""
                                        delta = {"role": "assistant", "content": content} if not _role_sent else {"content": content}
                                        _role_sent = True
                                        done = choices[0].get("finish_reason") == "stop" if choices else False
                                        chunk = {"choices": [{"delta": delta, "finish_reason": "stop" if done else None}]}
                                        yield chunk
                                    except Exception:
                                        pass
                    return external_async_generator()
                else:
                    response = await client.post(f"{EXTERNAL_GPU_URL.rstrip('/')}/api/chat", json=payload)
                    response.raise_for_status()
                    data = response.json()
                    choices = data.get("choices", [{}])
                    content = choices[0].get("message", {}).get("content", "") if choices else ""
                    return {"choices": [{"message": {"role": "assistant", "content": content}}]}

            except Exception as e:
                logger.warning(f"⚠️ Nodo GPU Esterno offline o irraggiungibile ({e}). Fallback su Motore C++ Locale (CPU)...")
        # -----------------------------------------------------------

        loop = asyncio.get_running_loop()
        lock = self._resolve_lock(model)

        # Cap massimo tokens solo per chat model
        if model == "chat":
            _max_tokens_cap = LLM_MAX_TOKENS
            max_tokens = min(max_tokens, _max_tokens_cap)

        # Note: JSON mode (response_format=json_object) is handled natively by
        # llama-cpp-python's create_chat_completion via response_format parameter.
        # The grammar parameter is left as None — do NOT build a custom GBNF string here.

        if stream:
            _STREAM_TOTAL_TIMEOUT = 600  # max 10 min totali per streaming

            async def async_generator():
                async with PriorityLockContextManager(lock, priority=priority):
                    try:
                        generator = await asyncio.wait_for(
                            loop.run_in_executor(
                                self.executor,
                                lambda: llm.create_chat_completion(
                                    messages=messages,
                                    tools=openai_tools,
                                    temperature=temperature,
                                    max_tokens=max_tokens,
                                    presence_penalty=presence_penalty,
                                    frequency_penalty=frequency_penalty,
                                    repeat_penalty=repeat_penalty,
                                    top_p=top_p,
                                    top_k=top_k,
                                    stream=True,
                                    response_format=response_format,
                                    grammar=None,
                                )
                            ),
                        timeout=300
                        )
                    except asyncio.TimeoutError:
                        logger.error("LLM streaming timed out after 300s (first chunk)")
                        yield {"error": "LLM inference timed out"}
                        return
                    def get_next(gen):
                        try:
                            return next(gen)
                        except StopIteration:
                            return None
                    
                    _stream_deadline = time.monotonic() + _STREAM_TOTAL_TIMEOUT
                    while True:
                        if time.monotonic() > _stream_deadline:
                            logger.error(f"LLM streaming total timeout after {_STREAM_TOTAL_TIMEOUT}s")
                            yield {"error": "LLM generation took too long"}
                            break
                        try:
                            chunk = await asyncio.wait_for(
                                loop.run_in_executor(self.executor, lambda: get_next(generator)),
                                timeout=60.0,  # per-chunk timeout: 60s tra token consecutivi
                            )
                            if chunk is None:
                                break
                            yield chunk
                        except asyncio.TimeoutError:
                            logger.error("LLM streaming per-chunk timed out (60s stall)")
                            yield {"error": "LLM generation stalled"}
                            break
                        except Exception as e:
                            logger.error(f"Errore generatore stream: {e}")
                            break
            return async_generator()
        else:
            async with PriorityLockContextManager(lock, priority=priority):
                try:
                    response = await asyncio.wait_for(
                        loop.run_in_executor(
                            self.executor,
                                lambda: llm.create_chat_completion(
                                    messages=messages,
                                    tools=openai_tools,
                                    temperature=temperature,
                                    max_tokens=max_tokens,
                                    presence_penalty=presence_penalty,
                                    frequency_penalty=frequency_penalty,
                                    repeat_penalty=repeat_penalty,
                                    top_p=top_p,
                                    top_k=top_k,
                                    stream=False,
                                    response_format=response_format,
                                    grammar=None,
                                )
                        ),
                        timeout=300
                    )
                    return response
                except asyncio.TimeoutError:
                    logger.error(f"LLM inference timed out after 300s (max_tokens={max_tokens})")
                    return {"error": "LLM inference timed out", "choices": [{"message": {"role": "assistant", "content": "Mi dispiace, la generazione della risposta ha superato il tempo limite. Prova con una domanda più specifica."}}]}  # noqa

    # ════════════════════════════════════════════════════════════════
    # 3-CLASS INTENT CLASSIFIER (Qwen3.5 su CPU con LlamaGrammar)
    # ════════════════════════════════════════════════════════════════

    async def classify_intent(self, user_message: str, context: dict) -> GatekeeperResult:
        """Classifica intento utente in project/meta/general usando Qwen3.5 (OpA).

        Usa 6 few-shot esempi con LlamaGrammar per output JSON vincolato.
        GATEKEEPER_N_CTX=4096 permette esempi + contesto senza troncare.

        Args:
            user_message: Query utente grezza.
            context: Dict con active_project, projects_available, recent_messages.

        Returns:
            GatekeeperResult con intent, project (se project), confidence.
        """
        active_project = context.get("active_project") or "nessuno"
        projects_str = ", ".join(context.get("projects_available", [])) or "nessuno"
        recent_msgs = context.get("recent_messages", [])
        recent_str = " | ".join(recent_msgs[-3:]) if recent_msgs else "nessuno"

        prompt = f"""Contesto:
- Progetto attivo: {active_project}
- Progetti disponibili: {projects_str}
- Messaggi recenti: {recent_str}

Esempi:
1. Richiesta: "aggiungi una funzione di login"
   {{"intent":"project","project":"null","confidence":0.95}}
2. Richiesta: "quali progetti hai in memoria?"
   {{"intent":"meta","project":"null","confidence":0.98}}
3. Richiesta: "ciao come stai?"
   {{"intent":"general","project":"null","confidence":0.99}}
4. Richiesta: "c'è un bug in auth.py"
   {{"intent":"project","project":"null","confidence":0.90}}
5. Richiesta: "raccontami una barzelletta"
   {{"intent":"general","project":"null","confidence":0.95}}
6. Richiesta: "cosa contiene il progetto Jarvis?"
   {{"intent":"project","project":"Jarvis","confidence":0.92}}

Richiesta: "{user_message[:1000]}"

Classifica: project (codice/file/progetto), meta (lista/capacità/chi sei), general (conversazione).
JSON esatto: {{"intent":"project|meta|general","project":"null|Nome","confidence":0.95}}
"""
        from llama_cpp import LlamaGrammar
        grammar_str = r'''root ::= "{\"intent\": " intent ", \"project\": " projval ", \"confidence\": " number "}"
intent ::= "\"project\"" | "\"meta\"" | "\"general\""
projval ::= string | "null"
string ::= "\"" word "\""
word ::= [a-zA-Z] ([a-zA-Z0-9_.-])*
number ::= [0-1] "." digit+ | "1" "." "0"+
digit ::= [0-9]'''

        try:
            grammar_obj = LlamaGrammar.from_string(grammar_str)
            messages = [{"role": "user", "content": prompt}]
            response = await self.generate_chat(
                messages, stream=False,
                options={"temperature": 0.0, "num_predict": 60},
                grammar=grammar_obj,
                model="gatekeeper",
            )
            if "error" in response:
                logger.warning(f"Gatekeeper: errore LLM → fallback general ({response['error']})")
                return GatekeeperResult(intent="general", confidence=0.0)

            content = extract_content(response)
            match = re.search(r'\{.*\}', content, re.DOTALL)
            if not match:
                logger.warning(f"Gatekeeper: JSON non trovato in '{content[:60]}...' → fallback general")
                return GatekeeperResult(intent="general", confidence=0.0)

            result = json.loads(match.group(0))
            intent = result.get("intent", "general")
            project = result.get("project")
            confidence = float(result.get("confidence", 0.0))

            available = context.get("projects_available", [])
            if project and project not in available:
                project = None
            if intent not in ("project", "meta", "general"):
                intent = "general"

            logger.info(f"🧠 Gatekeeper Qwen3.5: {intent} | project={project} | conf={confidence:.2f}")
            return GatekeeperResult(
                intent=intent,
                project=project if intent == "project" else None,
                confidence=confidence,
            )
        except Exception as e:
            logger.warning(f"Gatekeeper: eccezione → fallback general ({repr(e)})")
            return GatekeeperResult(intent="general", confidence=0.0)

    # ════════════════════════════════════════════════════════════════
    # GEMMA 4 INTENT CLASSIFIER (OpB — senza grammatica GBNF)
    # ════════════════════════════════════════════════════════════════

    async def classify_intent_with_gemma(self, user_message: str, context: dict) -> GatekeeperResult:
        """Classifica intento usando Gemma 4 (modello chat già in VRAM).

        Vantaggi rispetto a Qwen3.5:
        - 0 VRAM extra (riusa modello già caricato su GPU)
        - Qualità superiore (Gemma 4 2.1B vs Qwen3.5 0.8B)
        - Generazione velocissima: 1-5 token di output
        - Nessuna grammatica GBNF — parsing diretto della risposta

        Lo svantaggio: condivide la coda del modello chat (PriorityLock). Ma
        generando solo 1-5 token, il tempo di attesa è trascurabile anche
        durante una generazione concorrente.

        Args:
            user_message: Query utente grezza.
            context: Dict con active_project, projects_available, recent_messages.

        Returns:
            GatekeeperResult con intent, project (se project), confidence.
        """
        active_project = context.get("active_project") or "nessuno"
        projects_str = ", ".join(context.get("projects_available", [])) or "nessuno"

        prompt = f"""Contesto:
- Progetto attivo: {active_project}
- Progetti disponibili: {projects_str}

Richiesta: "{user_message[:800]}"

Classifica in UNA SOLA parola (project|meta|general):
- project: richiede contesto progetto (codice, file, bug, API, deployment)
- meta: richiede lista progetti, capacità, help su Jarvis
- general: conversazione generica (saluti, data/ora, meteo, barzellette, definizioni, posizione)

project|meta|general:"""

        try:
            messages = [{"role": "user", "content": prompt}]
            response = await asyncio.wait_for(
                self.generate_chat(
                    messages, stream=False,
                    options={"temperature": 0.0, "num_predict": 5, "max_tokens": 10, "stop": ["\n", "|"]},
                    model="chat", priority=1,
                ),
                timeout=15.0,  # gatekeeper: max 15s per classification
            )
            if "error" in response:
                logger.warning(f"Gemma Gatekeeper: errore → fallback general ({response['error']})")
                return GatekeeperResult(intent="general", confidence=0.0)

            content = (extract_content(response) or "").strip().lower()

            # Parsing diretto: cerca la parola chiave nella risposta
            if "project" in content:
                intent = "project"
                confidence = 0.95
            elif "meta" in content:
                intent = "meta"
                confidence = 0.95
            else:
                intent = "general"
                confidence = 0.80

            # Se è project, prova a estrarre il nome progetto dal messaggio
            project = None
            if intent == "project":
                available = context.get("projects_available", [])
                msg_lower = user_message.lower()
                for proj in available:
                    for variant in (proj.lower(), proj.lower().replace('_', '-'), proj.lower().replace('_', ' ')):
                        if variant in msg_lower:
                            project = proj
                            confidence = min(confidence + 0.03, 0.99)
                            break
                    if project:
                        break

            logger.info(f"🧠 Gatekeeper Gemma 4: {intent} | project={project} | conf={confidence:.2f}")
            return GatekeeperResult(
                intent=intent,
                project=project if intent == "project" else None,
                confidence=confidence,
            )
        except Exception as e:
            logger.warning(f"Gemma Gatekeeper: eccezione → fallback general ({repr(e)})")
            return GatekeeperResult(intent="general", confidence=0.0)

    # ════════════════════════════════════════════════════════════════
    # CAVEMAN PROMPT COMPRESSOR (Qwen3.5 su CPU)
    # ════════════════════════════════════════════════════════════════

    async def compress_prompt(
        self,
        user_query: str,
        rag_context: str = "",
        history: str = "",
        active_project: Optional[str] = None,
    ) -> str:
        """Comprime dati grezzi (query + RAG + history) in prompt caveman
        usando Qwen3.5 su CPU. Output deve essere più corto dell'input.
        Se la compressione fallisce o aumenta la dimensione, usa raw fallback.

        Args:
            user_query: Query utente originale.
            rag_context: Frammenti RAG (codice, documenti) come stringa.
            history: Cronologia recente sessione.
            active_project: Nome progetto attivo o None.

        Returns:
            Stringa compressa (raw fallback se compressione non riduce).
        """
        # Assembla il blocco raw da comprimere
        raw_parts = []
        if active_project:
            raw_parts.append(f"[PROJECT: {active_project}]")
        if history:
            raw_parts.append(f"[HISTORY]\n{history[:1500]}")
        if rag_context:
            raw_parts.append(f"[RAG_CONTEXT]\n{rag_context[:3000]}")
        raw_parts.append(f"[USER_QUERY]\n{user_query}")

        raw_data = "\n\n".join(raw_parts)

        # ── Token-aware context window guard ──
        # Gatekeeper model has GATEKEEPER_N_CTX (default 4096, configurabile).
        # System prompt ~100-450 token, response ~50 token, overhead ~50 token.
        # Budget for raw_data: 2000 chars (safe for CJK at 1 char/token).
        _GK_MAX_CHARS = 2000
        if len(raw_data) > _GK_MAX_CHARS:
            logger.info(f"🗜️ Compress input {len(raw_data)}ch > {_GK_MAX_CHARS}ch, truncating for gatekeeper context window")
            raw_data = raw_data[:_GK_MAX_CHARS]

        messages = [
            {"role": "system", "content": CAVEMAN_COMPRESSOR_SYSTEM_PROMPT},
            {"role": "user", "content": raw_data},
        ]

        try:
            response = await self.generate_chat(
                messages,
                stream=False,
                options={"temperature": 0.0, "num_predict": 128},
                model="gatekeeper",
            )
            if "error" in response:
                logger.warning(f"Compressore: errore → fallback raw ({response['error']})")
                return raw_data[:4096]

            compressed = extract_content(response)
            if not compressed or len(compressed) < 10:
                logger.warning("Compressore: output vuoto → fallback raw")
                return raw_data[:4096]

            # Strip eventuali tag di pensiero (thinking/reasoning) che il gatekeeper
            # potrebbe emettere — impedisce che meta-cognizioni inquinino Gemma 4.
            compressed = _strip_thinking(compressed)

            # Log compression ratio
            raw_len = len(raw_data)
            comp_len = len(compressed)
            ratio = (1 - comp_len / raw_len) * 100 if raw_len > 0 else 0

            # Se la compressione NON riduce (ratio ≤ 0), usa raw fallback
            if ratio <= 0:
                logger.warning(f"⚠️ Caveman compression negativa ({ratio:.0f}%): {raw_len}→{comp_len}, fallback raw")
                return raw_data[:4096]

            logger.info(f"🗜️ Caveman compression: {raw_len} → {comp_len} char ({ratio:.0f}% riduzione)")
            return compressed.strip()

        except Exception as e:
            logger.warning(f"Compressore: eccezione → fallback raw ({repr(e)})")
            return raw_data[:4096]

    async def get_embeddings(self, texts, priority=10):
        """Genera embedding via FastEmbed (ONNX CPU, multilingual-e5-base).

        Output format: {"data": [{"embedding": [float,...], "index": int}, ...]}
        768d nativi (nessuna truncation necessaria).
        """
        if isinstance(texts, str):
            texts = [texts]

        if not self.fastembed_model:
            return {"error": "FastEmbed non disponibile", "data": []}

        def _do_fastembed(texts):
            # fastembed restituisce generator di numpy array
            embeddings_np = list(self.fastembed_model.embed(texts))
            data = []
            for i, emb in enumerate(embeddings_np):
                # Convert numpy array to list
                emb_list = emb.tolist() if hasattr(emb, 'tolist') else list(emb)
                data.append({"embedding": emb_list, "index": i})
            return {"data": data, "object": "list"}

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            self.executor,
            lambda: _do_fastembed(texts)
        )

        return result

    # ==========================================================================
    # PROVIDER ROUTER INTEGRATION
    # ==========================================================================

    def init_provider_router(self):
        """Inizializza il ProviderRouter per provider esterni (Gemini, ecc.)."""
        try:
            from external.providers import init_router, ROUTE_STRATEGY_DISABLED
            from core.config import PROVIDER_CONFIG
            strategy = PROVIDER_CONFIG.get("strategy", ROUTE_STRATEGY_DISABLED)
            if strategy == ROUTE_STRATEGY_DISABLED:
                logger.info("ProviderRouter: disabilitato da EXTERNAL_PROVIDER_STRATEGY=disabled")
                self.provider_router = None
                return None
            router = init_router(PROVIDER_CONFIG)
            router.set_local_engine(self)
            self.provider_router = router
            logger.info(f"ProviderRouter: inizializzato (strategia={strategy})")
            return router
        except Exception as e:
            logger.warning(f"ProviderRouter: inizializzazione fallita: {e}")
            self.provider_router = None
            return None

    async def generate_chat_with_router(
        self,
        messages,
        tools=None,
        options=None,
        stream=False,
        grammar=None,
        preferred_provider=None,
        force_cloud=False
    ):
        """
        Genera risposta usando il ProviderRouter.
        Se il router non è disponibile, usa il normale generate_chat.
        """
        if not getattr(self, 'provider_router', None):
            return await self.generate_chat(messages, tools, options, stream, grammar)

        return await self.provider_router.route_chat(
            messages,
            options=options,
            stream=stream,
            preferred_provider=preferred_provider,
            force_cloud=force_cloud
        )


def _strip_thinking(text: str) -> str:
    """Rimuove tag di pensiero (thinking/reasoning) dall'output del LLM.
    
    Copre formati noti: <think>...</think>, <think> senza chiusura,
    [Thinking]..., <|think|>...</|think|>, e meta-ragionamenti
    numerati di compressione (es. "1.  **Analyze the Request:**").
    """
    import re as _re
    # <think>...</think> con chiusura
    text = _re.sub(r'<think>.*?</think>', '', text, flags=_re.DOTALL).strip()
    # <think> senza chiusura (tutto da <think> in poi)
    text = _re.sub(r'<think>.*', '', text, flags=_re.DOTALL).strip()
    # <|think|>...</|think|>
    text = _re.sub(r'<\|think\|>.*?<\|/think\|>', '', text, flags=_re.DOTALL).strip()
    # [Thinking]...[/Thinking]
    text = _re.sub(r'\[Thinking\].*?\[/Thinking\]', '', text, flags=_re.DOTALL).strip()
    # "Thinking:" all'inizio di una riga
    text = _re.sub(r'(?m)^Thinking:\s*\n?', '', text).strip()
    # Blocchi di analisi numerata: "1.  **Analyze**...", "2.  **Scan**...",
    # "3.  **Identify**...", "4.  **Determine**...", "5.  **Drafting**..."
    text = _re.sub(r'(?m)^\d+\.\s+\*\*.*?\*\*:.*', '', text).strip()
    # Righi tipo "*   **Project Name:** SlotBuilder" (liste di analisi)
    text = _re.sub(r'(?m)^\s*\*\s+\*\*.*?\*\*:.*', '', text).strip()
    return text


def extract_content(response: dict, default: str = "") -> str:
    """Estrae il contenuto testuale da una risposta LLM in formato OpenAI."""
    try:
        content = response["choices"][0]["message"].get("content", default)
        return _strip_thinking(content)
    except (KeyError, IndexError, TypeError):
        return default


def extract_tool_calls(response: dict) -> list:
    """Estrae le tool calls da una risposta LLM in formato OpenAI."""
    try:
        return response["choices"][0]["message"].get("tool_calls", []) or []
    except (KeyError, IndexError, TypeError):
        return []


def parse_qwen_tool_calls(text: str) -> list[dict]:
    """
    Parsa chiamate a funzione in formato nativo Qwen dal testo della risposta.
    
    La Qwen con chat_format=None emette i tool call come testo invece di
    usarli nel campo strutturato tool_calls della API. Questa funzione
    rileva il pattern <|tool_call|>...<|tool_call|> e lo converte in
    formato tool_call standard.
    
    IMPORTANTE: Se il chat_format è configurato correttamente (es. "chatml"),
    llama-cpp-python gestisce tool_calls strutturati automaticamente e
    questo fallback non serve. Attivato solo per modelli raw (chat_format=None).
    
    Formati supportati:
      <|tool_call|>call:function_name{param:"value"}<|tool_call|>
      <|tool_call|>{"name":"fn","arguments":{...}}<|tool_call|>
      <|tool_call|>call:function(param="value")<|tool_call|>
    """
    if not text:
        return []
    
    pattern = re.compile(
        r'<\|tool_call\|>(.*?)<\|tool_call\|>',
        re.DOTALL | re.IGNORECASE
    )
    
    tool_calls = []
    for match in pattern.finditer(text):
        raw = match.group(1).strip()
        try:
            # Prova prima JSON format: {"name":"fn","arguments":{...}}
            if raw.startswith("{"):
                parsed = json.loads(raw)
                tc = {
                    "id": f"call_{len(tool_calls)}",
                    "type": "function",
                    "function": {
                        "name": parsed.get("name", ""),
                        "arguments": json.dumps(parsed.get("arguments", {}))
                    }
                }
                tool_calls.append(tc)
                continue
            
            # Formato call:function_name{...} o call:function_name(...)
            if raw.startswith("call:"):
                fn_part = raw[5:].strip()
                # Estrai nome funzione (fino a primo { o ()
                paren_idx = -1
                brace_idx = -1
                if "(" in fn_part:
                    paren_idx = fn_part.index("(")
                if "{" in fn_part:
                    brace_idx = fn_part.index("{")
                
                split_idx = min(
                    [i for i in (paren_idx, brace_idx) if i >= 0],
                    default=len(fn_part)
                )
                
                fn_name = fn_part[:split_idx].strip()
                
                # Estrai argomenti se presenti
                args = {}
                if paren_idx >= 0:
                    args_str = fn_part[paren_idx+1:fn_part.rindex(")")] if ")" in fn_part else fn_part[paren_idx+1:]
                    # Parsa key=value o key="value"
                    for arg in args_str.split(","):
                        if "=" in arg:
                            k, v = arg.split("=", 1)
                            args[k.strip()] = v.strip().strip('"\'')
                elif brace_idx >= 0:
                    args_str = fn_part[brace_idx+1:fn_part.rindex("}")] if "}" in fn_part else fn_part[brace_idx+1:]
                    # Prova JSON parse
                    try:
                        args = json.loads("{" + args_str + "}")
                    except json.JSONDecodeError:
                        # Fallback: key:value parsing
                        for arg in args_str.split(","):
                            if ":" in arg:
                                k, v = arg.split(":", 1)
                                args[k.strip().strip('"\'')] = v.strip().strip('"\'')
                
                tc = {
                    "id": f"call_{len(tool_calls)}",
                    "type": "function",
                    "function": {
                        "name": fn_name,
                        "arguments": json.dumps(args)
                    }
                }
                tool_calls.append(tc)
        except Exception:
            continue
    
    return tool_calls


# Inizializziamo l'istanza globale
engine = LlamaEngine()
