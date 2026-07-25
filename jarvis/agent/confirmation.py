"""
Confirmation Manager — Sistema astratto di conferma per operazioni distruttive.

Fornisce un'interfaccia unificata per richiedere conferma all'utente prima di
eseguire operazioni potenzialmente pericolose (write_file, delete_file, git_commit, ecc.).

Provider disponibili:
- TelegramProvider: Conferma via bot Telegram (usato dal bot Telegram)
- DesktopProvider: Dialog Qt nativo (usato dalla desktop chat)
- ApiTokenProvider: Conferma token-based (usato da Cherry Studio / API OpenAI)
- AutoProvider: Approvazione automatica (per operazioni read-only)
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from abc import ABC, abstractmethod
from typing import Optional, Callable, Dict, Any

logger = logging.getLogger("confirmation")

# ──────────────────────────────────────────────
# Provider astratto
# ──────────────────────────────────────────────

class ConfirmationProvider(ABC):
    """Interfaccia base per tutti i provider di conferma."""

    @abstractmethod
    async def ask(self, action_desc: str, timeout: int = 300) -> bool:
        """
        Richiede conferma per un'azione distruttiva.

        Args:
            action_desc: Descrizione dell'azione (es. "Scrittura file: /path/file.py")
            timeout: Tempo massimo di attesa in secondi (default: 300)

        Returns:
            True se approvato, False se rifiutato o timeout
        """
        ...


# ──────────────────────────────────────────────
# Telegram Provider (esistente)
# ──────────────────────────────────────────────

# Dizionario condiviso: chat_id → asyncio.Future
# Usato da telegram_bot.py per rispondere alle conferme
pending_confirmations: Dict[int, asyncio.Future] = {}


class TelegramProvider(ConfirmationProvider):
    """Conferma via messaggio Telegram. Rimpiazza ask_confirmation()."""

    def __init__(self, bot, chat_id: int):
        self.bot = bot
        self.chat_id = chat_id

    async def ask(self, action_desc: str, timeout: int = 300) -> bool:
        if not self.bot or not self.chat_id:
            return True  # bypass if no bot available

        future = asyncio.Future()
        pending_confirmations[self.chat_id] = future

        await self.bot.send_message(
            chat_id=self.chat_id,
            text=f"⚠️ **ATTENZIONE: Richiesta Autorizzazione**\n"
                 f"L'LLM sta per eseguire:\n`{action_desc}`\n\n"
                 f"Vuoi autorizzare? Rispondi con **Y** o **N**.",
            parse_mode="Markdown"
        )

        try:
            approved = await asyncio.wait_for(future, timeout=timeout)
            return approved
        except asyncio.TimeoutError:
            pending_confirmations.pop(self.chat_id, None)
            return False


# ──────────────────────────────────────────────
# Desktop Provider (Qt nativo)
# ──────────────────────────────────────────────

class DesktopProvider(ConfirmationProvider):
    """Conferma via dialog Qt nativo (QMessageBox)."""

    def __init__(self, notify_callback: Optional[Callable] = None):
        """
        Args:
            notify_callback: Funzione chiamata per mostrare il dialog.
                            Deve accettare (title, message) e restituire True/False.
                            Se None, usa una fallback che logga e approva.
        """
        self.notify_callback = notify_callback

    async def ask(self, action_desc: str, timeout: int = 300) -> bool:
        if not self.notify_callback:
            logger.warning(f"DesktopProvider: nessun callback, auto-approvo: {action_desc}")
            return True

        # Esegue il callback in un executor per non bloccare l'event loop
        loop = asyncio.get_running_loop()
        approved = await loop.run_in_executor(
            None,
            self.notify_callback,
            "⚠️ Conferma Richiesta",
            f"L'LLM sta per eseguire:\n\n{action_desc}\n\nVuoi autorizzare?"
        )
        return bool(approved)


# ──────────────────────────────────────────────
# API Token Provider (Cherry Studio / OpenAI API)
# ──────────────────────────────────────────────

class ApiTokenProvider(ConfirmationProvider):
    """
    Conferma token-based per client API (Cherry Studio, OpenAI SDK, curl, ecc.).

    Meccanismo:
    1. execute_tool_call restituisce un messaggio CONFIRM_REQ:<token>:<action_desc>
    2. L'LLM chiede all'utente di approvare: "Devo scrivere file X. "
       "Reinvia con 'confirm:<token>' per autorizzare."
    3. L'utente reinvia il messaggio contenente "confirm:<token>"
    4. Il backend riconosce il token e completa l'operazione
    """

    # token → (asyncio.Future, action_desc, timestamp)
    pending: Dict[str, tuple[asyncio.Future, str, float]] = {}

    def __init__(self, request_id: str = ""):
        self._request_id = request_id or f"req_{int(time.time())}"

    async def ask(self, action_desc: str, timeout: int = 300) -> bool:
        token = hashlib.sha256(
            f"{self._request_id}:{action_desc}:{time.time()}:{id(self)}".encode()
        ).hexdigest()[:12]

        future = asyncio.Future()
        self.pending[token] = (future, action_desc, time.time())

        # Pianifica cleanup dopo timeout
        loop = asyncio.get_running_loop()
        loop.call_later(timeout, self._cleanup_expired, token)

        # Il risultato del tool sarà un messaggio che l'LLM mostrerà all'utente
        # Questa è una stringa speciale che viene intercettata in execute_tool_call
        raise PendingConfirmation(token, action_desc)

    @classmethod
    def resolve(cls, token: str, approved: bool) -> bool:
        """Risolve una conferma pendente. Chiamato quando l'utente reinvia confirm:TOKEN."""
        entry = cls.pending.pop(token, None)
        if entry is None:
            logger.warning(f"ApiTokenProvider: token '{token}' non trovato o già scaduto")
            return False
        future, action_desc, _ = entry
        if not future.done():
            future.set_result(approved)
            logger.info(f"ApiTokenProvider: conferma {'approvata' if approved else 'rifiutata'} per '{action_desc}'")
            return True
        return False

    @classmethod
    def _cleanup_expired(cls, token: str):
        """Rimuove token scaduti."""
        entry = cls.pending.pop(token, None)
        if entry is not None:
            future, action_desc, _ = entry
            if not future.done():
                future.set_result(False)
                logger.info(f"ApiTokenProvider: token '{token}' scaduto per '{action_desc}'")


class PendingConfirmation(Exception):
    """
    Eccezione sollevata da ApiTokenProvider.ask() per segnalare
    che è necessaria una conferma prima di procedere.

    Il chiamante (execute_tool_call) deve catturarla e restituire
    un messaggio all'LLM che descrive come approvare.
    """

    def __init__(self, token: str, action_desc: str):
        self.token = token
        self.action_desc = action_desc
        super().__init__(f"Conferma necessaria: {action_desc} [token: {token}]")


# ──────────────────────────────────────────────
# Auto Provider (read-only)
# ──────────────────────────────────────────────

class AutoProvider(ConfirmationProvider):
    """Approva automaticamente tutte le richieste. Per tool read-only."""

    async def ask(self, action_desc: str, timeout: int = 300) -> bool:
        return True


# ──────────────────────────────────────────────
# ConfirmationManager — Context Manager
# ──────────────────────────────────────────────

class ConfirmationManager:
    """
    Gestore delle conferme. Seleziona automaticamente il provider appropriato
    in base al contesto della richiesta.

    Usage:
        mgr = ConfirmationManager()
        mgr.set_provider(TelegramProvider(bot, chat_id))
        approved = await mgr.ask("Scrittura file: /path")
    """

    def __init__(self):
        self._provider: Optional[ConfirmationProvider] = AutoProvider()

    def set_provider(self, provider: ConfirmationProvider):
        """Imposta il provider di conferma per questo contesto."""
        self._provider = provider

    @property
    def provider(self) -> ConfirmationProvider:
        return self._provider

    async def ask(self, action_desc: str, timeout: int = 300) -> bool:
        """Richiede conferma tramite il provider attivo."""
        if not self._provider:
            logger.warning("ConfirmationManager: nessun provider configurato, auto-approvo")
            return True
        return await self._provider.ask(action_desc, timeout)

    @classmethod
    def from_bot(cls, bot, chat_id: Optional[int] = None) -> "ConfirmationManager":
        """Crea un manager configurato per Telegram bot."""
        mgr = cls()
        if bot and chat_id:
            mgr.set_provider(TelegramProvider(bot, chat_id))
        return mgr

    @classmethod
    def from_request(cls, request_id: str = "") -> "ConfirmationManager":
        """Crea un manager configurato per richieste API (token-based)."""
        mgr = cls()
        mgr.set_provider(ApiTokenProvider(request_id))
        return mgr

    @classmethod
    def from_desktop(cls, notify_callback: Optional[Callable] = None) -> "ConfirmationManager":
        """Crea un manager configurato per desktop chat."""
        mgr = cls()
        mgr.set_provider(DesktopProvider(notify_callback))
        return mgr
