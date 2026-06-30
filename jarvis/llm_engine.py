import asyncio
import os
import re
import json
import subprocess
import logging
import httpx
from concurrent.futures import ThreadPoolExecutor

from config import LLM_THINKING_MODE, MODEL_PROFILE, EXTERNAL_GPU_URL, MODEL_ID, LLM_MAX_TOKENS, EMBEDDING_DIMS

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
            
        self.chat_model = None
        self.embed_model = None
        # Thread pool per non bloccare l'event loop di FastAPI (concurrency safe)
        self.executor = ThreadPoolExecutor(max_workers=8)
        # Lock separati: chat e embedding usano modelli Llama diversi,
        # non devono bloccarsi a vicenda
        self.chat_lock = PriorityLock()
        self.embed_lock = PriorityLock()
        self.initialized = True

    def load_models(self):
        if Llama is None:
            logger.error("Impossibile caricare i modelli: llama-cpp-python mancante.")
            return

        from config import LLAMA_MODEL_PATH as _cfg_model_path, LLAMA_EMBED_MODEL_PATH as _cfg_embed_path
        chat_model_path = _cfg_model_path
        embed_model_path = _cfg_embed_path

        # 1. Caricamento del Modello Chat Principale (Spinge al limite la GPU)
        if os.path.exists(chat_model_path):
            from config import N_GPU_LAYERS as _cfg_gpu, LLM_NUM_CTX as _cfg_ctx, LLM_BATCH_SIZE as _cfg_batch
            from config import LLM_UBATCH_SIZE as _cfg_ubatch, LLM_FLASH_ATTN as _cfg_flash
            n_gpu_layers = _cfg_gpu
            n_ctx = _cfg_ctx
            n_batch = _cfg_batch
            n_ubatch = _cfg_ubatch
            flash_attn = _cfg_flash
            logger.info(f"Caricamento Chat Model: {chat_model_path}")
            logger.info(f"⚙️ n_gpu_layers={n_gpu_layers} n_ctx={n_ctx} n_batch={n_batch} n_ubatch={n_ubatch} flash_attn={flash_attn}")
            # chat_format: determinato automaticamente dai metadati GGUF in model_profiles.py
            from config import MODEL_PROFILE as _init_profile
            _chat_format = _init_profile.chat_format
            logger.info(f"⚙️ chat_format={_chat_format} (family={_init_profile.family})")

            self.chat_model = Llama(
                model_path=chat_model_path,
                n_gpu_layers=n_gpu_layers,
                n_ctx=n_ctx,
                n_batch=n_batch,
                n_ubatch=n_ubatch,
                n_threads=6,
                flash_attn=flash_attn,
                use_mmap=True,
                chat_format=_chat_format,
                verbose=False
            )
            log_vram_usage("Dopo caricamento Chat Model")

            # ── Estrazione metadati GGUF e aggiornamento MODEL_PROFILE ──
            try:
                _metadata = {}
                if hasattr(self.chat_model, 'metadata') and self.chat_model.metadata:
                    _metadata = dict(self.chat_model.metadata)
                if hasattr(self.chat_model, 'model_metadata') and self.chat_model.model_metadata:
                    _metadata = dict(self.chat_model.model_metadata)

                if _metadata:
                    from model_profiles import detect_from_metadata
                    from config import MODEL_PROFILE as _old_profile
                    _new_profile = detect_from_metadata(_metadata, _old_profile)
                    # Aggiorna globalmente config.MODEL_PROFILE
                    import config as _cfg
                    _cfg.MODEL_PROFILE = _new_profile
                    # Aggiorna LLM_THINKING_MODE se non sovrascritto da env var
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
        else:
            logger.warning(f"File {chat_model_path} non trovato!")

        # 2. Caricamento del Modello Embedding (Mini-modello delegato alla CPU o piccola porzione VRAM)
        if os.path.exists(embed_model_path):
            logger.info(f"Caricamento Embed Model: {embed_model_path}")
            self.embed_model = Llama(
                model_path=embed_model_path,
                embedding=True,
                n_gpu_layers=2,
                n_ctx=8192,
                n_batch=256,
                n_threads=6,
                verbose=False,
                pooling=2
            )
            log_vram_usage("Dopo caricamento Embed Model")

            # Warmup: prima chiamata di embedding per CUDA JIT compilation
            # (evita ritardo di 30+s sulla prima richiesta utente)
            try:
                logger.info(f"🔄 Warmup Embed Model (CUDA JIT compilation)...")
                self.embed_model.create_embedding(["warmup"])
                logger.info(f"✅ Embed Model warmup completato")
            except Exception as e:
                logger.warning(f"⚠️ Embed Model warmup fallito (non critico): {e}")
        else:
            logger.warning(f"File {embed_model_path} non trovato!")

    async def generate_chat(self, messages, tools=None, options=None, stream=False, grammar=None):
        if not self.chat_model:
            return {"error": "Modello chat non caricato"}

        opts = options or {}

        # --- Thinking Mode (Gemma/DeepSeek/QwQ) ---
        # Verifica dal model profile se il modello caricato supporta <|think|>
        _thinking_tag = "<|think|>"
        # Se il chat_format è gemma, usa [Thinking] invece di <|think|>
        if MODEL_PROFILE.chat_format == "gemma":
            _thinking_tag = "[Thinking]"
        if LLM_THINKING_MODE and MODEL_PROFILE.thinking_support and messages:
            processed_messages = []
            for msg in messages:
                if isinstance(msg, dict) and msg.get("role") == "system":
                    content = msg.get("content", "")
                    if _thinking_tag not in content:
                        msg = {**msg, "content": f"{_thinking_tag}\n" + content}
                processed_messages.append(msg)
            messages = processed_messages
        # ----------------------------

        temperature = opts.get("temperature", 1.0)
        max_tokens = opts.get("num_predict", 2048)
        presence_penalty = opts.get("presence_penalty", 0.1)
        frequency_penalty = opts.get("frequency_penalty", 0.1)
        repeat_penalty = opts.get("repeat_penalty", 1.1)
        top_p = opts.get("top_p", 0.9)
        top_k = opts.get("top_k", 40)
        
        # Mappiamo i tools Ollama in tools OpenAI compatibili con llama-cpp-python
        openai_tools = None
        if tools:
            openai_tools = []
            for t in tools:
                if isinstance(t, dict) and "function" in t:
                    openai_tools.append(t)
                elif isinstance(t, dict) and "name" in t:
                    # Formato tools semplificato Ollama -> OpenAI
                    openai_tools.append({
                        "type": "function",
                        "function": {
                            "name": t.get("name"),
                            "description": t.get("description", ""),
                            "parameters": t.get("parameters", {"type": "object", "properties": {}})
                        }
                    })

        # --- DELEGAZIONE EXTERNAL GPU (High-Availability Fallback) ---
        if EXTERNAL_GPU_URL:
            try:
                payload = {
                    "model": MODEL_ID,
                    "messages": messages,
                    "stream": stream,
                    "options": {"skip_rag": True, **opts}
                }
                if tools: payload["tools"] = tools

                # Ping veloce per verificare se il nodo GPU è online
                async with httpx.AsyncClient(timeout=1.5) as client:
                    # Effettuiamo una GET veloce per capire se il tunnel è su
                    await client.get(f"{EXTERNAL_GPU_URL.rstrip('/')}/")
                
                logger.info(f"🚀 Nodo GPU Esterno Raggiungibile! Offloading inferenza a {EXTERNAL_GPU_URL}...")
                
                if stream:
                    async def external_async_generator():
                        _role_sent = False
                        async with httpx.AsyncClient(timeout=120.0) as client:
                            async with client.stream("POST", f"{EXTERNAL_GPU_URL.rstrip('/')}/api/chat", json=payload) as response:
                                response.raise_for_status()
                                async for line in response.aiter_lines():
                                    if line:
                                        try:
                                            data = json.loads(line)
                                            delta = {"role": "assistant", "content": data.get("response", "")} if not _role_sent else {"content": data.get("response", "")}
                                            _role_sent = True
                                            done = data.get("done", False)
                                            chunk = {"choices": [{"delta": delta, "finish_reason": "stop" if done else None}]}
                                            yield chunk
                                        except Exception:
                                            pass
                    return external_async_generator()
                else:
                    async def external_sync():
                        async with httpx.AsyncClient(timeout=120.0) as client:
                            response = await client.post(f"{EXTERNAL_GPU_URL.rstrip('/')}/api/chat", json=payload)
                            response.raise_for_status()
                            data = response.json()
                            return {"choices": [{"message": {"role": "assistant", "content": data.get("response", "")}}]}
                    return await external_sync()

            except Exception as e:
                logger.warning(f"⚠️ Nodo GPU Esterno offline o irraggiungibile ({e}). Fallback su Motore C++ Locale (CPU)...")
        # -----------------------------------------------------------

        loop = asyncio.get_running_loop()

        # Cap ragionevole: evita generazioni eccessivamente lunghe.
        # Il default 2048 garantisce che i tag di coda (MEMORY, CONFIDENCE, ecc.)
        # non vengano troncati prima della chiusura.
        # Sovrascrivibile via LLM_MAX_TOKENS env var nel .env
        _max_tokens_cap = LLM_MAX_TOKENS
        max_tokens = min(max_tokens, _max_tokens_cap)

        if stream:
            async def async_generator():
                async with PriorityLockContextManager(self.chat_lock, priority=0):
                    try:
                        generator = await asyncio.wait_for(
                            loop.run_in_executor(
                                self.executor,
                                lambda: self.chat_model.create_chat_completion(
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
                                    grammar=grammar
                                )
                            ),
                        timeout=300
                        )
                    except asyncio.TimeoutError:
                        logger.error("LLM streaming timed out after 300s")
                        yield {"error": "LLM inference timed out"}
                        return
                    def get_next(gen):
                        try:
                            return next(gen)
                        except StopIteration:
                            return None
                    
                    while True:
                        try:
                            chunk = await loop.run_in_executor(self.executor, lambda: get_next(generator))
                            if chunk is None:
                                break
                            yield chunk
                        except Exception as e:
                            logger.error(f"Errore generatore stream: {e}")
                            break
            return async_generator()
        else:
            async with PriorityLockContextManager(self.chat_lock, priority=0):
                try:
                    response = await asyncio.wait_for(
                        loop.run_in_executor(
                            self.executor,
                            lambda: self.chat_model.create_chat_completion(
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
                                grammar=grammar
                            )
                        ),
                        timeout=300
                    )
                    return response
                except asyncio.TimeoutError:
                    logger.error(f"LLM inference timed out after 300s (max_tokens={max_tokens})")
                    return {"error": "LLM inference timed out", "choices": [{"message": {"role": "assistant", "content": "Mi dispiace, la generazione della risposta ha superato il tempo limite. Prova con una domanda più specifica."}}]}  # noqa

    async def get_embeddings(self, texts, priority=10):
        if not self.embed_model:
            return {"error": "Modello embedding non caricato"}
        
        async with PriorityLockContextManager(self.embed_lock, priority=priority):
            loop = asyncio.get_running_loop()
            
            # Se text è una singola stringa, lo incapsuliamo
            if isinstance(texts, str):
                texts = [texts]
                
            # llama-cpp-python processa array nativamente
            embeddings = await loop.run_in_executor(
                self.executor,
                lambda: self.embed_model.create_embedding(texts)
            )
            
            # MRL (Matryoshka): tronca a EMBEDDING_DIMS dim per retrocompatibilità con le collection Qdrant esistenti
            if "data" in embeddings:
                for item in embeddings["data"]:
                    emb = item.get("embedding", [])
                    if len(emb) > EMBEDDING_DIMS:
                        item["embedding"] = emb[:EMBEDDING_DIMS]
            
            return embeddings

    # ==========================================================================
    # PROVIDER ROUTER INTEGRATION
    # ==========================================================================

    def init_provider_router(self):
        """Inizializza il ProviderRouter per provider esterni (Gemini, ecc.)."""
        try:
            from external_providers import init_router, ROUTE_STRATEGY_DISABLED
            from config import PROVIDER_CONFIG
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


def extract_content(response: dict, default: str = "") -> str:
    """Estrae il contenuto testuale da una risposta LLM in formato OpenAI."""
    try:
        return response["choices"][0]["message"].get("content", default)
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
