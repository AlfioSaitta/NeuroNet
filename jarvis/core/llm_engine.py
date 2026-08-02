import asyncio
import os
import re
import json
import time
import subprocess
import logging
import inspect
from concurrent.futures import ThreadPoolExecutor
import numpy as np

import core.state as state
from core.config import LLM_THINKING_MODE, MODEL_PROFILE, EXTERNAL_GPU_URL, MODEL_ID, LLM_MAX_TOKENS, EMBEDDING_DIMS
from typing import Literal, Optional

try:
    from llama_cpp import Llama
except ImportError:
    Llama = None
    logging.warning("llama-cpp-python non installato. Il motore LLM locale non funzionerà.")

logger = logging.getLogger(__name__)

# Detect if this version of llama-cpp-python supports chat_template_kwargs
_CHAT_TEMPLATE_KWARGS_SUPPORTED = False
if Llama is not None:
    try:
        sig = inspect.signature(Llama.create_chat_completion)
        _CHAT_TEMPLATE_KWARGS_SUPPORTED = "chat_template_kwargs" in sig.parameters
    except Exception:
        pass
if not _CHAT_TEMPLATE_KWARGS_SUPPORTED:
    logger.info("llama-cpp-python: chat_template_kwargs non supportato, disabilitato")

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
            COMPRESSOR_N_CTX as _gk_ctx,
            COMPRESSOR_N_THREADS as _gk_threads,
            COMPRESSOR_N_GPU_LAYERS as _gk_gpu,
        )
        _dev = "GPU" if _gk_gpu != 0 else "CPU"
        logger.info(f"Caricamento Gatekeeper Model ({_dev}): {path}")
        logger.info(f"⚙️ n_gpu_layers={_gk_gpu} n_ctx={_gk_ctx} n_threads={_gk_threads}")
        self.gatekeeper_model = Llama(
            model_path=path,
            n_gpu_layers=_gk_gpu, n_ctx=_gk_ctx,
            n_batch=512, n_ubatch=128, n_threads=_gk_threads,
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
            COMPRESSOR_MODEL_PATH as _cfg_gk_path,
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
                "Imposta COMPRESSOR_MODEL_PATH nel .env per abilitare."
            )

        log_vram_usage("VRAM finale dopo caricamento tutti i modelli")

    def _resolve_model(self, model: str):
        """Seleziona il modello Llama in base al nome logico."""
        if model == "gatekeeper":
            if not self.gatekeeper_model:
                raise RuntimeError("Gatekeeper model (Qwen3.5) non caricato — imposta COMPRESSOR_MODEL_PATH")
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
                     Supporta anche:
                       chat_template_kwargs: dict da passare a create_chat_completion
                           (es. {"enable_thinking": True/False})
                       logit_bias: dict {token_id: bias} per bloccare/forzare token
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

        temperature = opts.get("temperature", 1.0)
        max_tokens = opts.get("num_predict", 2048)
        presence_penalty = opts.get("presence_penalty", 0.1)
        frequency_penalty = opts.get("frequency_penalty", 0.1)
        repeat_penalty = opts.get("repeat_penalty", 1.1)
        top_p = opts.get("top_p", 0.9)
        top_k = opts.get("top_k", 40)
        response_format = opts.get("response_format")
        # Reasoning: chat_template_kwargs (es. {"enable_thinking": True}) e
        # logit_bias per bloccare fisicamente il token di pensiero
        chat_template_kwargs = opts.get("chat_template_kwargs") or {}
        logit_bias = opts.get("logit_bias") or {}
        
        openai_tools = self._normalize_tools(tools, model)
        logger.info(f"⛏️ generate_chat: tools={'YES' if tools else 'NO'} openai_tools={'SET' if openai_tools else 'NONE'} tool_choice={opts.get('tool_choice', 'NOT_SET')!r} has_tool_role={any(isinstance(m, dict) and m.get('role') == 'tool' for m in messages)}")

        # ── Tool calling: inject tool definitions into messages ──
        # Qwen emette tool call in formato XML <tool_call>, non JSON.
        # Quando passiamo `tools` a llama-cpp-python, formatta i tool in
        # JSON grammar che CONFLITTA con l'XML nativo di Qwen — il modello
        # smette di generare tool call e risponde solo in testo.
        # Soluzione: iniettiamo le definizioni dei tool direttamente nei
        # messaggi come testo (formato naturale per Qwen) e NON passiamo
        # `tools` a llama-cpp-python.
        # Usiamo _has_openai_tools per ricordare che tools erano presenti
        # (serve alla logica di buffering nello streaming).
        _has_openai_tools = bool(openai_tools)
        tool_choice = opts.get("tool_choice")
        if openai_tools and tool_choice != "none":
            has_tool_role = any(isinstance(m, dict) and m.get("role") == "tool" for m in messages)
            if not has_tool_role:
                # Format tool definitions for XML-native models
                # Per modelli piccoli: istruzioni brevi, esempio concreto, rinforzo alla fine.
                _tools_text = "## Available Tools\n\n"
                for t in openai_tools:
                    func = t.get("function", t)
                    _tools_text += json.dumps({
                        "name": func.get("name", "unknown"),
                        "description": func.get("description", ""),
                        "parameters": func.get("parameters", {"type": "object", "properties": {}}),
                    }, indent=2) + "\n\n"
                # Mostra il PRIMO tool come esempio concreto
                _first_tool_name = "tool_name"
                _first_tool_args = '{"arg1": "value1"}'
                for t in openai_tools:
                    func = t.get("function", t)
                    _first_tool_name = func.get("name", "tool_name")
                    props = func.get("parameters", {}).get("properties", {})
                    example_args = {}
                    for pk in list(props.keys())[:2]:
                        example_args[pk] = f"<{pk}_value>"
                    if example_args:
                        _first_tool_args = json.dumps(example_args)
                    break
                _tools_text += (
                    "## Tool Calling Format\n\n"
                    "To call a tool, output ONLY this XML (no other text):\n"
                    "<tool_call>\n"
                    f'{{"name": "{_first_tool_name}", "arguments": {_first_tool_args}}}\n'
                    "</tool_call>\n\n"
                    "Example: if the user asks about the current time, you would output:\n"
                    '<tool_call>\n'
                    '{"name": "get_current_time", "arguments": {"format": "full"}}\n'
                    '</tool_call>\n'
                    "Then STOP — wait for the tool result."
                )

                # For specific function forcing
                _force_name = ""
                if isinstance(tool_choice, dict) and tool_choice.get("type") == "function":
                    _force_name = tool_choice.get("function", {}).get("name", "")

                modified_msgs = [dict(m) if isinstance(m, dict) else m for m in messages]
                if _force_name:
                    # specific tool: inject force instruction + temperature=0 + concrete example
                    _force_example = _build_tool_example(_force_name, openai_tools)
                    for i in range(len(modified_msgs) - 1, -1, -1):
                        if modified_msgs[i].get("role") == "user":
                            modified_msgs[i]["content"] = (
                                _tools_text + "\n\n"
                                + str(modified_msgs[i].get("content", ""))
                                + f"\n\n[INSTRUCTION]\n"
                                f"You MUST use the tool '{_force_name}' now.\n"
                                f"Output ONLY this XML — no explanation, no text:\n"
                                f"{_force_example}\n"
                                f"[/INSTRUCTION]"
                            )
                            break
                    temperature = 0.0
                else:
                    # auto: tool definitions + reinforcement at the END of user message
                    _reinforcement = (
                        "\n\n[INSTRUCTION]\n"
                        "You have tools available. If the user's request matches a tool, "
                        "output ONLY the <tool_call> XML. Otherwise respond normally.\n"
                        "[/INSTRUCTION]"
                    )
                    for i in range(len(modified_msgs) - 1, -1, -1):
                        if modified_msgs[i].get("role") == "user":
                            modified_msgs[i]["content"] = (
                                str(modified_msgs[i].get("content", ""))
                                + "\n\n"
                                + _tools_text
                                + _reinforcement
                            )
                            break
                messages = modified_msgs
            # Never pass tools to llama-cpp-python — it breaks Qwen's XML format
            openai_tools = None
        elif tool_choice == "none":
            _has_openai_tools = False
            openai_tools = None

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
        # Il parametro `grammar` (GBNF) viene PROPAGATO se fornito dal caller
        # (es. _llm_classify in agent/intent_router.py). Se None → nessun vincolo.
        # FIX 2026-08-02: prima era hardcoded a None — la GBNF veniva ignorata
        # e il modello generava testo libero invece del JSON strutturato.

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
                                    grammar=grammar,
                                    **({"chat_template_kwargs": chat_template_kwargs} if chat_template_kwargs and _CHAT_TEMPLATE_KWARGS_SUPPORTED else {}),
                                    logit_bias=logit_bias or None,
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
                    
                    # ── Streaming live immediato (nessun buffer) ──
                    # Strumenti/nessuno strumento: yield chunks immediatamente.
                    # Il tool calling XML viene rilevato post-stream da main.py.
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
                                    grammar=grammar,
                                    **({"chat_template_kwargs": chat_template_kwargs} if chat_template_kwargs and _CHAT_TEMPLATE_KWARGS_SUPPORTED else {}),
                                    logit_bias=logit_bias or None,
                                )
                        ),
                        timeout=300
                    )
                    # ── Tool call parsing: Qwen emette tool call come XML in content
                    #    anziché come tool_calls strutturati. Se abbiamo passato tools
                    #    e la risposta contiene XML, lo convertiamo. ──
                    if openai_tools and "error" not in response:
                        choice = response.get("choices", [{}])[0]
                        content = choice.get("message", {}).get("content", "") or ""
                        if content and ("<tool_call" in content.lower() or "<|tool_call|>" in content):
                            parsed = parse_qwen_tool_calls(content)
                            if parsed:
                                choice["message"]["tool_calls"] = parsed
                                choice["message"]["content"] = None
                                choice["finish_reason"] = "tool_calls"
                    return response
                except asyncio.TimeoutError:
                    logger.error(f"LLM inference timed out after 300s (max_tokens={max_tokens})")
                    return {"error": "LLM inference timed out", "choices": [{"message": {"role": "assistant", "content": "Mi dispiace, la generazione della risposta ha superato il tempo limite. Prova con una domanda più specifica."}}]}  # noqa

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
        # Compressor model has COMPRESSOR_N_CTX (default 4096, configurabile).
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
            # FIX 2026-08-02: blocco fisico del reasoning sul compressore.
            # Qwen3.5-0.8B è famiglia qwen con thinking_support=true: senza
            # logit_bias emette SOLO <think>...</think> e dopo _strip_thinking
            # l'output è vuoto → fallback raw ad ogni richiesta. Stesso
            # pattern di apply_reasoning_config() sul chat model.
            _options: dict = {"temperature": 0.0, "num_predict": 128}
            try:
                from core.reasoning import get_reasoning_meta
                _meta = get_reasoning_meta("qwen")
                if _meta.get("stop_token_id") is not None:
                    _options["logit_bias"] = {int(_meta["stop_token_id"]): -100}
            except Exception:
                pass
            response = await self.generate_chat(
                messages,
                stream=False,
                options=_options,
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
    # FIX 2026-08-02: chiusura </think> orfana senza apertura visibile.
    # Il modello Qwen emette <think> come token speciale del template chat
    # (non compare nel content), ma il testo reasoning + </think> restano.
    # Se le chiusure superano le aperture, tutto ciò che precede la prima
    # chiusura è reasoning da scartare.
    if text.count('</think>') > text.count('<think>'):
        text = _re.sub(r'^.*?</think>', '', text, flags=_re.DOTALL).strip()
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


def _build_tool_example(tool_name: str, openai_tools: list) -> str:
    """
    Costruisce un esempio XML concreto per un tool specifico,
    usando i nomi dei parametri reali (primi 2 parametri).
    """
    for t in openai_tools:
        func = t.get("function", t)
        if func.get("name") == tool_name:
            props = func.get("parameters", {}).get("properties", {})
            example_args = {}
            for pk in list(props.keys())[:2]:
                example_args[pk] = f"<{pk}>"
            if example_args:
                args_json = json.dumps(example_args)
            else:
                args_json = '{}'
            return (
                '<tool_call>\n'
                f'{{"name": "{tool_name}", "arguments": {args_json}}}\n'
                '</tool_call>'
            )
    # Fallback generico
    return (
        '<tool_call>\n'
        f'{{"name": "{tool_name}", "arguments": {{}}}}\n'
        '</tool_call>'
    )


def _recover_malformed_json_tool_call(inner: str) -> dict | None:
    """
    Recupera tool call da JSON malformato dove il modello omette la chiave "arguments".
    
    Casi gestiti:
    1. {"name": "x", {"command": "ls"}}          — unnamed nested object
    2. {"name": "x", {"arguments": {"cmd": "ls"}}} — arguments nested inside unnamed object
    3. {"name": "x", "key": "val"}                — args flattened at top level
    4. {"name": "x", "arguments": {"cmd": "ls"}}  — valid JSON (falls through to json.loads)
    
    Usa regex multilivello per estrarre nome e argomenti.
    """
    # Estrai "name": "..." o 'name': '...'
    name_match = re.search(r'"name"\s*:\s*"([^"]+)"', inner)
    if not name_match:
        name_match = re.search(r"'name'\s*:\s*'([^']+)'", inner)
    if not name_match:
        return None
    fn_name = name_match.group(1)
    
    # Cerca oggetti JSON annidati a vari livelli di profondita
    args = {}
    
    # Pattern multilivello: trova oggetti JSON con fino a 2 livelli di nidificazione
    for obj_match in re.finditer(
        r'\{(?:[^{}]|\{(?:[^{}]|\{[^{}]*\})*\})*\}',
        inner
    ):
        obj_str = obj_match.group(0)
        # Salta l'oggetto che contiene il nome del tool (e' l'outer)
        if '"name"' in obj_str:
            continue
        
        try:
            parsed_obj = json.loads(obj_str)
            if isinstance(parsed_obj, dict):
                # Se il nested object ha una chiave "arguments", estrai da li
                if "arguments" in parsed_obj and isinstance(parsed_obj["arguments"], dict):
                    args = dict(parsed_obj["arguments"])
                else:
                    # Altrimenti prendi tutto come args
                    args = dict(parsed_obj)
        except (json.JSONDecodeError, TypeError):
            # Se JSON fallisce, prova regex key:value
            for kv in re.finditer(r'"(\w+)"\s*:\s*"([^"]*?)"', obj_str):
                args[kv.group(1)] = kv.group(2)
    
    if not args:
        return None
    
    return {"name": fn_name, "arguments": json.dumps(args)}


def _parse_qwen_xml_tool_call(text: str) -> list[dict]:
    """
    Parsa tool call in formato XML nativo Qwen3.5:
      <tool_call>
      <function=NOME>
      <parameter=KEY>
      VALUE
      </parameter>
      </function>
      </tool_call>
    
    Supporta anche varianti:
      <tool_call><function name="NOME"><parameter name="KEY">VALUE</parameter></function></tool_call>
      <tool_call><function=NOME><parameter=KEY>VALUE</parameter></function></tool_call>
      <tool_call>{JSON}</tool_call>
    """
    if not text:
        return []
    
    tool_calls = []
    
    # Pattern flessibile: cattura blocchi <tool_call>...</tool_call>
    # (self-closing compresi)
    xml_pattern = re.compile(
        r'<tool_call[^>]*>(.*?)</tool_call\s*>',
        re.DOTALL | re.IGNORECASE
    )
    
    for match in xml_pattern.finditer(text):
        inner = match.group(1).strip()
        if not inner:
            continue
        
        # Se l'interno È JSON, parsalo direttamente
        if inner.startswith("{") and inner.endswith("}"):
            try:
                parsed = json.loads(inner)
                fn_name = parsed.get("name", parsed.get("function", ""))
                fn_args = parsed.get("arguments", {})
                if isinstance(fn_args, str):
                    try:
                        fn_args = json.loads(fn_args)
                    except (json.JSONDecodeError, TypeError):
                        pass
                # Se manca "arguments", prendi TUTTO ciò che non è "name"/"function" come args
                if not fn_args and fn_name:
                    fn_args = {k: v for k, v in parsed.items()
                               if k not in ("name", "function", "type")}
                tool_calls.append({
                    "id": f"call_{len(tool_calls)}",
                    "type": "function",
                    "function": {"name": fn_name, "arguments": json.dumps(fn_args)},
                })
            except (json.JSONDecodeError, TypeError):
                # JSON malformato (es. {"name": "x", {"key": "val"}} senza chiave per il secondo oggetto).
                # Tenta recovery via regex: estrai name + primo oggetto annidato
                _recovered = _recover_malformed_json_tool_call(inner)
                if _recovered:
                    tool_calls.append({
                        "id": f"call_{len(tool_calls)}",
                        "type": "function",
                        "function": _recovered,
                    })
            continue
        
        # Estrai nome funzione da <function=VALUE>, <function name="VALUE">, <function = VALUE >
        fn_name = ""
        fn_tag = re.search(r'<function\s+name\s*=\s*["\']?([^>"\'\s]+)["\']?\s*/?\s*>', inner, re.IGNORECASE)
        if fn_tag:
            fn_name = fn_tag.group(1).strip()
        else:
            # <function=VALUE> o <function = VALUE >
            fn_tag = re.search(r'<function\s*=\s*["\']?([^>"\'\s]+)["\']?\s*/?\s*>', inner, re.IGNORECASE)
            if fn_tag:
                fn_name = fn_tag.group(1).strip()
            else:
                # <function>VALUE</function>
                fn_tag = re.search(r'<function[^>]*>\s*(.+?)\s*</function\s*>', inner, re.IGNORECASE)
                if fn_tag:
                    fn_name = fn_tag.group(1).strip()
        if not fn_name:
            continue
        
        # Estrai parametri: <parameter=KEY>VALUE</parameter> o <parameter name="KEY">VALUE</parameter>
        args = {}
        for pmatch in re.finditer(
            r'<parameter\s+(?:name\s*=\s*)?["\']?([^>"\'\s]+)["\']?\s*>(.*?)</parameter\s*>',
            inner, re.DOTALL | re.IGNORECASE
        ):
            key = pmatch.group(1).strip()
            val = pmatch.group(2).strip()
            if key and val:
                args[key] = val
        
        if not args:
            # <parameter=KEY>VALUE</parameter> (con = subito dopo parameter)
            for pmatch in re.finditer(
                r'<parameter\s*=\s*["\']?([^>"\'\s]+)["\']?\s*>\s*\n?\s*(.*?)\s*\n?\s*</parameter\s*>',
                inner, re.DOTALL | re.IGNORECASE
            ):
                key = pmatch.group(1).strip()
                val = pmatch.group(2).strip()
                if key and val:
                    args[key] = val
        
        # Fallback: <KEY>VALUE</KEY> per qualsiasi tag non-function
        if not args:
            for pmatch in re.finditer(
                r'<(\w+)>\s*(.+?)\s*</\1\s*>',
                inner, re.DOTALL | re.IGNORECASE
            ):
                key = pmatch.group(1).strip()
                val = pmatch.group(2).strip()
                if key.lower() != "function" and key and val:
                    args[key] = val
        
        tool_calls.append({
            "id": f"call_{len(tool_calls)}",
            "type": "function",
            "function": {"name": fn_name, "arguments": json.dumps(args)},
        })
    
    return tool_calls


def _parse_qwen_pipe_tool_calls(text: str) -> list[dict]:
    """
    Parsa formato <|tool_call|>...<|tool_call|> (con pipe, legacy).
    """
    pattern = re.compile(
        r'<\|tool_call\|>(.*?)<\|tool_call\|>',
        re.DOTALL | re.IGNORECASE
    )
    
    tool_calls = []
    for match in pattern.finditer(text):
        raw = match.group(1).strip()
        try:
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
            
            if raw.startswith("call:"):
                fn_part = raw[5:].strip()
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
                args = {}
                if paren_idx >= 0:
                    args_str = fn_part[paren_idx+1:fn_part.rindex(")")] if ")" in fn_part else fn_part[paren_idx+1:]
                    for arg in args_str.split(","):
                        if "=" in arg:
                            k, v = arg.split("=", 1)
                            args[k.strip()] = v.strip().strip('"\'')
                elif brace_idx >= 0:
                    args_str = fn_part[brace_idx+1:fn_part.rindex("}")] if "}" in fn_part else fn_part[brace_idx+1:]
                    try:
                        args = json.loads("{" + args_str + "}")
                    except json.JSONDecodeError:
                        for arg in args_str.split(","):
                            if ":" in arg:
                                k, v = arg.split(":", 1)
                                args[k.strip().strip('"\'')] = v.strip().strip('"\'')
                
                tool_calls.append({
                    "id": f"call_{len(tool_calls)}",
                    "type": "function",
                    "function": {"name": fn_name, "arguments": json.dumps(args)}
                })
        except Exception:
            continue
    
    return tool_calls


def parse_qwen_tool_calls(text: str) -> list[dict]:
    """
    Parsa tool call da testo risposta Qwen in QUALSIASI formato.
    
    Prova in ordine:
      1. Formato XML <tool_call> (nuovo Qwen3.5)
      2. Formato <|tool_call|> (legacy Qwen2.5)
    
    Returns:
      Lista di dict in formato OpenAI tool_calls, o [] se non trovato.
    """
    if not text:
        return []
    
    # 1. Prova formato XML <tool_call>...</tool_call>
    result = _parse_qwen_xml_tool_call(text)
    if result:
        return result
    
    # 2. Fallback: formato <|tool_call|>...<|tool_call|>
    result = _parse_qwen_pipe_tool_calls(text)
    if result:
        return result
    
    # 3. Prova regex generica: <TOOL_CALL> o <tool_call> in qualsiasi variante
    generic = re.findall(
        r'<tool_call[^>]*>.*?</tool_call\s*>',
        text, re.DOTALL | re.IGNORECASE
    )
    if generic:
        # Già coperto da _parse_qwen_xml_tool_call, ma per sicurezza
        return _parse_qwen_xml_tool_call(text)
    
    return []


# Inizializziamo l'istanza globale
engine = LlamaEngine()
