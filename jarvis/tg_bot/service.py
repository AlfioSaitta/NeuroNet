"""
Telegram Service Manager.
Inizializzazione, gestione ciclo di vita e arresto del Bot Telegram e dei Userbot MTProto.
"""

import asyncio
import logging
from core.config import logger, TELEGRAM_ENABLED, TELEGRAM_TOKEN, ALLOWED_USERS
import core.state as state

try:
    from tg_bot.userbot import auto_start_existing, stop_all_userbots
except ImportError:
    auto_start_existing = None
    stop_all_userbots = None


async def init_telegram() -> None:
    """Inizializza e avvia il bot Telegram (se abilitato e configurato)."""
    if not (TELEGRAM_ENABLED and TELEGRAM_TOKEN and ALLOWED_USERS):
        logger.info("📱 Bot Telegram disabilitato (Manca Token o Utenti Autorizzati).")
        return

    try:
        from telegram import BotCommand, Update
        from telegram.ext import (
            ApplicationBuilder,
            CommandHandler,
            MessageHandler,
            filters,
            CallbackQueryHandler,
            TypeHandler,
        )
        from telegram.request import HTTPXRequest
        from telegram.error import BadRequest, NetworkError
        from tg_bot.bot import (
            telegram_start,
            handle_telegram_message,
            telegram_callback_handler,
            auth_middleware,
        )

        class _RetryHTTPXRequest(HTTPXRequest):
            """HTTPXRequest con retry 5x su errori di rete (DNS, timeout, OSError)."""

            async def _request_wrapper(self, url, method, **kw):
                for _attempt in range(5):
                    try:
                        return await super()._request_wrapper(url, method, **kw)
                    except (OSError, NetworkError) as _e:
                        if isinstance(_e, BadRequest):
                            raise
                        if _attempt < 4:
                            logger.warning(
                                f"DNS/Network error su Telegram API, retry {_attempt+2}/5: {_e}"
                            )
                            await asyncio.sleep(2**_attempt + 0.5 * _attempt)
                        else:
                            raise

        _base_req = _RetryHTTPXRequest(
            read_timeout=120.0,
            write_timeout=120.0,
            connect_timeout=60.0,
            pool_timeout=60.0,
            connection_pool_size=50,
        )
        logger.info("📡 Telegram HTTP client con retry DNS (5 tentativi) attivo")

        state.telegram_app = (
            ApplicationBuilder()
            .token(TELEGRAM_TOKEN)
            .request(_base_req)
            .build()
        )
        state.telegram_app.add_handler(
            TypeHandler(Update, auth_middleware), group=-1
        )
        state.telegram_app.add_handler(CommandHandler("start", telegram_start))
        state.telegram_app.add_handler(
            CallbackQueryHandler(telegram_callback_handler)
        )
        state.telegram_app.add_handler(
            MessageHandler(
                (filters.TEXT | filters.VOICE | filters.AUDIO | filters.Document.ALL)
                & (~filters.COMMAND),
                handle_telegram_message,
            )
        )

        await state.telegram_app.initialize()
        await state.telegram_app.bot.set_my_commands(
            [BotCommand("start", "Mostra il menu principale a pulsanti")]
        )
        await state.telegram_app.start()
        await state.telegram_app.updater.start_polling(drop_pending_updates=True)
        logger.info("📱 Bot Telegram avviato all'interno del Proxy.")
    except Exception as e:
        logger.error(f"⚠️ Impossibile avviare Telegram: {e}")


async def start_userbots() -> None:
    """Avvia i Multi-Userbot MTProto registrati."""
    if auto_start_existing is not None:
        try:
            await auto_start_existing()
        except Exception as e:
            logger.warning(f"Userbot auto-start error: {e}")


async def stop_telegram() -> None:
    """Arresta sia il Bot Telegram che i Userbot MTProto."""
    if state.telegram_app:
        try:
            if state.telegram_app.updater:
                await state.telegram_app.updater.stop()
            await state.telegram_app.stop()
            await state.telegram_app.shutdown()
            logger.info("📱 Bot Telegram fermato.")
        except Exception as e:
            logger.warning(f"Telegram bot stop error: {e}")

    if stop_all_userbots is not None:
        try:
            await stop_all_userbots()
            logger.info("📱 Userbots MTProto fermati.")
        except Exception as e:
            logger.warning(f"Userbot shutdown error: {e}")
