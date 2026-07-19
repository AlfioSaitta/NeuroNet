"""Admin Panel — modular web control panel for NeuroNet.

Serve il template HTML, file statici e coordina l'inizializzazione.
Usato da main.py tramite setup_admin_panel(app).
"""

import os
import logging
from fastapi import APIRouter
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from starlette.types import Scope, Receive, Send

logger = logging.getLogger(__name__)

ADMIN_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_PATH = os.path.join(ADMIN_DIR, "templates", "index.html")
STATIC_DIR = os.path.join(ADMIN_DIR, "static")

admin_router = APIRouter()

# ── Cache-Control: sempre ricarica i file statici ──
_NOCACHE_HEADERS = [(b"cache-control", b"no-cache, no-store, must-revalidate")]


class _NoCacheStaticFiles(StaticFiles):
    """StaticFiles che forza il ricaricamento del browser a ogni richiesta,
    così modifiche a JS/CSS/HTML sono visibili senza riavviare Jarvis."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        async def send_nocache(message):
            if message["type"] == "http.response.start":
                headers = message.get("headers", [])
                headers.extend(_NOCACHE_HEADERS)
                message["headers"] = headers
            await send(message)

        await super().__call__(scope, receive, send_nocache)


@admin_router.get("/")
@admin_router.get("/dashboard")
async def get_admin_panel():
    """Serve il pannello di amministrazione (SPA)."""
    return FileResponse(
        TEMPLATE_PATH,
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


def setup_admin_panel(app):
    """Mounta i file statici e registra il router. Chiamato da main.py."""
    # Mount static files (con NoCache per sviluppo senza riavvio)
    if os.path.isdir(STATIC_DIR):
        app.mount(
            "/admin/static",
            _NoCacheStaticFiles(directory=STATIC_DIR),
            name="admin_static",
        )
        logger.info("📁 Admin panel static files mounted at /admin/static (no-cache)")
    else:
        logger.warning("Admin panel static directory not found: %s", STATIC_DIR)

    # Register the admin HTML router
    app.include_router(admin_router)
    logger.info("🖥️  Admin panel router registered at / and /dashboard")
