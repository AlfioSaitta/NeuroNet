"""
Provider Router — Astrazione per provider LLM esterni (Gemini, Claude, ecc.).
Permette routing strategico delle richieste tra motore locale e cloud.
"""

import json
import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Optional

logger = logging.getLogger("chameleon.providers")

# ==============================================================================
# STRATEGIE DI ROUTING
# ==============================================================================
ROUTE_STRATEGY_FALLBACK = "fallback_only"    # Cloud solo se locale fallisce
ROUTE_STRATEGY_SELECTIVE = "selective"       # Routing per tipo richiesta
ROUTE_STRATEGY_PARALLEL = "parallel"         # Entrambi, sceglie migliore
ROUTE_STRATEGY_MULTIMODAL = "multimodal"     # Solo per richieste con media

# ==============================================================================
# CLASSE BASE ASTRATTA
# ==============================================================================

class BaseProvider(ABC):
    """Classe base per tutti i provider LLM esterni."""

    def __init__(self, config: dict):
        self.config = config
        self.name = self.__class__.__name__

    @abstractmethod
    async def is_available(self) -> bool:
        """Verifica se il provider è raggiungibile e configurato correttamente."""
        pass

    @abstractmethod
    async def generate_chat(
        self,
        messages: list,
        options: Optional[dict] = None,
        stream: bool = False
    ):
        """Genera una risposta chat tramite il provider esterno."""
        pass

    @abstractmethod
    async def generate_with_timeout(
        self,
        messages: list,
        options: Optional[dict] = None,
        timeout: float = 30.0
    ) -> Optional[str]:
        """Genera con timeout. Restituisce None se scaduto o fallito."""
        pass


# ==============================================================================
# PROVIDER GEMINI
# ==============================================================================

class GeminiProvider(BaseProvider):
    """Provider Google Gemini via google-generativeai SDK."""

    def __init__(self, config: dict):
        super().__init__(config)
        self.api_key = config.get("api_key", "")
        self.model_name = config.get("model", "gemini-2.5-pro-exp-03-25")
        self._client = None

    async def _lazy_init(self):
        if self._client is not None:
            return True
        if not self.api_key:
            logger.warning("GeminiProvider: GEMINI_API_KEY non configurata")
            return False
        try:
            import google.generativeai as genai
            genai.configure(api_key=self.api_key)
            self._client = genai
            logger.info(f"GeminiProvider: inizializzato con modello {self.model_name}")
            return True
        except ImportError:
            logger.warning("GeminiProvider: google-generativeai non installato. `pip install google-generativeai`")
            return False
        except Exception as e:
            logger.warning(f"GeminiProvider: inizializzazione fallita: {e}")
            return False

    async def is_available(self) -> bool:
        return await self._lazy_init()

    def _map_messages(self, messages: list) -> list:
        """Converte i messaggi interni nel formato Gemini."""
        gemini_messages = []
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")

            # Gemini non supporta "system" — lo prependiamo come user
            if role == "system":
                gemini_messages.append({
                    "role": "user",
                    "parts": [f"[System Instruction]: {content}"]
                })
            elif role == "user":
                gemini_messages.append({
                    "role": "user",
                    "parts": [content]
                })
            elif role == "assistant":
                gemini_messages.append({
                    "role": "model",
                    "parts": [content]
                })
            elif role == "tool":
                gemini_messages.append({
                    "role": "user",
                    "parts": [f"[Tool Result]: {content}"]
                })
        return gemini_messages

    async def generate_chat(self, messages: list, options: Optional[dict] = None, stream: bool = False):
        if not await self._lazy_init():
            return {"error": "Gemini non disponibile"}

        opts = options or {}
        temperature = opts.get("temperature", 0.7)
        max_tokens = opts.get("num_predict", 2048)

        try:
            model = self._client.GenerativeModel(
                model_name=self.model_name,
                generation_config={
                    "temperature": temperature,
                    "max_output_tokens": max_tokens,
                    "top_p": opts.get("top_p", 0.9),
                }
            )

            gemini_messages = self._map_messages(messages)

            if stream:
                async def stream_gen():
                    response = await model.generate_content_async(
                        gemini_messages,
                        stream=True
                    )
                    async for chunk in response:
                        if chunk.text:
                            yield {"choices": [{"delta": {"content": chunk.text}}]}
                return stream_gen()
            else:
                response = await model.generate_content_async(gemini_messages)
                text = response.text if hasattr(response, 'text') else ""
                return {
                    "choices": [{"message": {"role": "assistant", "content": text}}],
                    "usage": {
                        "prompt_tokens": 0,
                        "completion_tokens": 0
                    }
                }
        except Exception as e:
            logger.error(f"GeminiProvider: generate_chat fallito: {e}")
            return {"error": f"Gemini: {str(e)}"}

    async def generate_with_timeout(
        self,
        messages: list,
        options: Optional[dict] = None,
        timeout: float = 30.0
    ) -> Optional[str]:
        try:
            result = await asyncio.wait_for(
                self.generate_chat(messages, options, stream=False),
                timeout=timeout
            )
            if "error" not in result:
                return result["choices"][0]["message"]["content"]
        except asyncio.TimeoutError:
            logger.warning(f"GeminiProvider: timeout dopo {timeout}s")
        except Exception as e:
            logger.warning(f"GeminiProvider: errore in generate_with_timeout: {e}")
        return None


# ==============================================================================
# PROVIDER ROUTER
# ==============================================================================

class ProviderRouter:
    """Router che gestisce la selezione del provider LLM in base alla strategia."""

    def __init__(self, config: dict):
        self.strategy = config.get("strategy", ROUTE_STRATEGY_FALLBACK)
        self.providers: dict[str, BaseProvider] = {}
        self._local_engine = None

    def register_provider(self, name: str, provider: BaseProvider):
        self.providers[name] = provider
        logger.info(f"ProviderRouter: registrato provider '{name}' ({provider.__class__.__name__})")

    def set_local_engine(self, engine):
        """Reference al LlamaEngine locale per fallback routing."""
        self._local_engine = engine

    def get_available_providers(self) -> list[str]:
        return list(self.providers.keys())

    async def route_chat(
        self,
        messages: list,
        options: Optional[dict] = None,
        stream: bool = False,
        preferred_provider: Optional[str] = None,
        force_cloud: bool = False
    ):
        """
        Seleziona e chiama il provider appropriato in base alla strategia.

        Args:
            messages: Lista messaggi in formato Ollama/OpenAI
            options: Opzioni di generazione
            stream: Flag streaming
            preferred_provider: Nome provider specifico (es. "gemini")
            force_cloud: Ignora la strategia, vai direttamente al cloud

        Returns:
            Risposta nel formato atteso da LlamaEngine.generate_chat
        """
        if force_cloud or preferred_provider:
            provider = self.providers.get(preferred_provider or "")
            if provider:
                return await provider.generate_chat(messages, options, stream)
            return {"error": f"Provider '{preferred_provider}' non trovato"}

        if self.strategy == ROUTE_STRATEGY_FALLBACK:
            return await self._route_fallback(messages, options, stream)

        elif self.strategy == ROUTE_STRATEGY_SELECTIVE:
            return await self._route_selective(messages, options, stream)

        elif self.strategy == ROUTE_STRATEGY_PARALLEL:
            return await self._route_parallel(messages, options, stream)

        elif self.strategy == ROUTE_STRATEGY_MULTIMODAL:
            return await self._route_multimodal(messages, options, stream)

        return {"error": f"Strategia sconosciuta: {self.strategy}"}

    async def _route_fallback(self, messages, options, stream):
        """Tenta locale, se fallisce → cloud."""
        if self._local_engine:
            result = await self._local_engine.generate_chat(messages=messages, options=options, stream=stream)
            if stream:
                return result  # async generator → pass through, no error check possible
            if "error" not in result:
                return result

        for name, provider in self.providers.items():
            result = await provider.generate_chat(messages, options, stream)
            if "error" not in result:
                logger.info(f"ProviderRouter: fallback a '{name}' dopo fallimento locale")
                return result

        return {"error": "Tutti i provider non disponibili"}

    async def _route_selective(self, messages, options, stream):
        """Routing basato sul contenuto (es. web knowledge → cloud, codice → locale)."""
        last_user_msg = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                last_user_msg = m.get("content", "")
                break

        is_code_query = any(kw in last_user_msg.lower() for kw in
            ["codice", "file", "funzione", "classe", "bug", "refactor", "script"])

        if is_code_query and self._local_engine:
            return await self._local_engine.generate_chat(messages=messages, options=options, stream=stream)

        for name, provider in self.providers.items():
            result = await provider.generate_chat(messages, options, stream)
            if "error" not in result:
                logger.info(f"ProviderRouter: routing selettivo a '{name}'")
                return result

        if self._local_engine:
            return await self._local_engine.generate_chat(messages=messages, options=options, stream=stream)
        return {"error": "Nessun provider disponibile"}

    async def _route_parallel(self, messages, options, stream):
        """Chiama tutti i provider in parallelo, sceglie il miglior risultato."""
        if stream:
            return await self._route_fallback(messages, options, stream)

        tasks = []
        if self._local_engine:
            tasks.append(self._local_engine.generate_chat(messages=messages, options=options, stream=False))
        for provider in self.providers.values():
            tasks.append(provider.generate_chat(messages, options, stream=False))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        best = None
        best_len = -1
        for r in results:
            if isinstance(r, dict) and "error" not in r:
                content = r.get("choices", [{}])[0].get("message", {}).get("content", "")
                if len(content) > best_len:
                    best_len = len(content)
                    best = r

        if best:
            return best
        return {"error": "Nessun provider ha prodotto risultati"}

    async def _route_multimodal(self, messages, options, stream):
        """Per richieste multimodali (immagini), usa provider cloud."""
        for name, provider in self.providers.items():
            result = await provider.generate_chat(messages, options, stream)
            if "error" not in result:
                logger.info(f"ProviderRouter: routing multimodale a '{name}'")
                return result

        if self._local_engine:
            logger.warning("ProviderRouter: nessun provider cloud per richiesta multimodale, uso locale")
            return await self._local_engine.generate_chat(messages=messages, options=options, stream=stream)
        return {"error": "Nessun provider disponibile per richiesta multimodale"}


# ==============================================================================
# SINGLETON GLOBALE
# ==============================================================================

_router: Optional[ProviderRouter] = None

def get_router() -> Optional[ProviderRouter]:
    return _router

def init_router(config: dict) -> ProviderRouter:
    global _router
    _router = ProviderRouter(config)

    gemini_key = config.get("gemini_api_key", "")
    if gemini_key:
        gemini = GeminiProvider({
            "api_key": gemini_key,
            "model": config.get("gemini_model", "gemini-2.5-pro-exp-03-25")
        })
        _router.register_provider("gemini", gemini)
        logger.info("ProviderRouter: Gemini registrato come provider esterno")
    else:
        logger.info("ProviderRouter: GEMINI_API_KEY non configurata — provider cloud disattivati")

    return _router
