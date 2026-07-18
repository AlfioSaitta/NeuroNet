"""
Chat Session Store — archiviazione e recupero sessioni chat complete.

Conserva in memoria l'intera cronologia dei turni per ogni conversazione,
organizzata per conversation_id. Supporta ricerca full-text, export JSON/Markdown
e persistenza su file JSON per sopravvivere ai restart.

Collegata al pipeline in main.py: dopo ogni risposta LLM, il turno
(user + assistant) viene salvato qui oltre che nel PipelineTrace.
Esposta via MCP v2 (mcp_server_v2.py) per analisi da parte di agenti AI esterni.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import time
from collections import deque
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# Message Turn
# ──────────────────────────────────────────────


@dataclasses.dataclass
class MessageTurn:
    """Un singolo turno di una conversazione: messaggio utente + risposta assistente.

    Salvato nel ChatSessionStore dopo ogni interazione completa.
    """
    role: str                        # "user" | "assistant" | "tool"
    content: str                     # Testo completo del messaggio
    timestamp: float                 # time.time()
    request_id: str                  # UUID del PipelineTrace associato
    conversation_id: str             # Raggruppa i turni in sessioni
    user_id: str                     # Autore del messaggio
    project: Optional[str] = None    # Progetto attivo al momento
    prompt_tokens: int = 0           # Token spesi per il prompt
    completion_tokens: int = 0       # Token generati come risposta
    duration_ms: float = 0.0         # Tempo di elaborazione
    model: str = ""                  # Modello LLM usato
    has_tool_calls: bool = False     # Se la risposta ha coinvolto tool
    tool_names: Optional[list[str]] = None  # Nomi tool chiamati
    error: Optional[str] = None      # Eventuale errore
    gatekeeper_intent: Optional[str] = None  # Intento classificato

    def to_dict(self) -> dict:
        d = {
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp,
            "datetime": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.timestamp)),
            "request_id": self.request_id,
            "conversation_id": self.conversation_id,
            "user_id": self.user_id,
            "project": self.project,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "duration_ms": round(self.duration_ms, 1),
            "model": self.model,
            "has_tool_calls": self.has_tool_calls,
            "tool_names": self.tool_names,
            "error": self.error,
            "gatekeeper_intent": self.gatekeeper_intent,
        }
        return d

    def to_dict_short(self) -> dict:
        """Versione compatta senza content (per liste)."""
        d = self.to_dict()
        d.pop("content", None)
        d["content_preview"] = self.content[:120] if self.content else ""
        return d


# ──────────────────────────────────────────────
# Chat Session Store
# ──────────────────────────────────────────────


class ChatSessionStore:
    """Archiviazione in memoria delle sessioni chat complete.

    Mantiene fino a ``max_sessions`` conversazioni, ognuna con un massimo
    di ``max_turns_per_session`` turni (FIFO). Supporta persistenza su file
    JSON per non perdere i dati al restart di Jarvis.

    Uso::

        store = ChatSessionStore()
        store.add_turn(turn)
        session = store.get_session("conv_123")
        results = store.search_sessions("query")
    """

    def __init__(self, max_sessions: int = 500, max_turns_per_session: int = 200):
        self.max_sessions = max_sessions
        self.max_turns_per_session = max_turns_per_session
        # conversation_id → deque[MessageTurn]
        self._sessions: dict[str, deque[MessageTurn]] = {}
        # conversation_id → metadata
        self._session_meta: dict[str, dict[str, Any]] = {}

    # ── Public API ─────────────────────────────

    def add_turn(self, turn: MessageTurn) -> None:
        """Aggiunge un turno a una conversazione (crea la sessione se nuova)."""
        conv_id = turn.conversation_id
        if conv_id not in self._sessions:
            # Nuova sessione: evita crescita incontrollata
            if len(self._sessions) >= self.max_sessions:
                self._evict_oldest()
            self._sessions[conv_id] = deque(maxlen=self.max_turns_per_session)
            self._session_meta[conv_id] = {
                "created_at": turn.timestamp,
                "user_id": turn.user_id,
                "turn_count": 0,
            }

        self._sessions[conv_id].append(turn)
        meta = self._session_meta[conv_id]
        meta["last_activity"] = turn.timestamp
        meta["turn_count"] = len(self._sessions[conv_id])
        # Aggiorna progetto se presente
        if turn.project:
            meta["project"] = turn.project
        logger.debug(f"📝 SessionStore: turno aggiunto a {conv_id} "
                      f"(turn #{meta['turn_count']})")

    def get_session(self, conv_id: str) -> list[dict]:
        """Restituisce tutti i turni di una conversazione."""
        if conv_id not in self._sessions:
            return []
        return [t.to_dict() for t in self._sessions[conv_id]]

    def list_sessions(
        self, limit: int = 20, sort_by: str = "last_activity",
        user_id: Optional[str] = None,
    ) -> list[dict]:
        """Lista delle sessioni disponibili con metadati."""
        items = []
        for conv_id, turns in self._sessions.items():
            meta = self._session_meta.get(conv_id, {})
            if user_id and meta.get("user_id") != user_id:
                continue
            # Primo turno per titolo automatico
            first_turn = turns[0] if turns else None
            title = ""
            if first_turn and first_turn.role == "user":
                title = first_turn.content[:100]

            items.append({
                "conversation_id": conv_id,
                "title": title,
                "turn_count": len(turns),
                "created_at": meta.get("created_at"),
                "last_activity": meta.get("last_activity"),
                "user_id": meta.get("user_id"),
                "project": meta.get("project"),
            })

        reverse = sort_by != "created_at"
        items.sort(key=lambda x: x.get(sort_by, 0) or 0, reverse=reverse)
        return items[:limit]

    def search_sessions(
        self, query: str, user_id: Optional[str] = None,
        limit: int = 20,
    ) -> list[dict]:
        """Cerca testo in tutte le sessioni. Restituisce snippet del primo match."""
        query_lower = query.lower()
        results = []
        for conv_id, turns in self._sessions.items():
            meta = self._session_meta.get(conv_id, {})
            if user_id and meta.get("user_id") != user_id:
                continue
            for turn in turns:
                if turn.content and query_lower in turn.content.lower():
                    # Trova la posizione del match per contesto
                    idx = turn.content.lower().find(query_lower)
                    start = max(0, idx - 60)
                    end = min(len(turn.content), idx + len(query) + 120)
                    snippet = (("…" if start > 0 else "") +
                               turn.content[start:end] +
                               ("…" if end < len(turn.content) else ""))
                    results.append({
                        "conversation_id": conv_id,
                        "request_id": turn.request_id,
                        "role": turn.role,
                        "content_snippet": snippet,
                        "timestamp": turn.timestamp,
                    })
                    break  # Un match per sessione

        results.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
        return results[:limit]

    def get_stats(self) -> dict:
        """Statistiche aggregate su tutte le sessioni."""
        total_turns = 0
        total_prompt = 0
        total_completion = 0
        total_duration = 0.0
        sessions_with_tools = 0
        sessions_with_errors = 0

        for conv_id, turns in self._sessions.items():
            total_turns += len(turns)
            has_tools = False
            has_errors = False
            for t in turns:
                total_prompt += t.prompt_tokens
                total_completion += t.completion_tokens
                total_duration += t.duration_ms
                if t.has_tool_calls:
                    has_tools = True
                if t.error:
                    has_errors = True
            if has_tools:
                sessions_with_tools += 1
            if has_errors:
                sessions_with_errors += 1

        num_sessions = len(self._sessions)
        return {
            "total_sessions": num_sessions,
            "total_turns": total_turns,
            "total_prompt_tokens": total_prompt,
            "total_completion_tokens": total_completion,
            "total_duration_seconds": round(total_duration / 1000, 1),
            "avg_turns_per_session": round(total_turns / max(num_sessions, 1), 1),
            "sessions_with_tool_calls": sessions_with_tools,
            "sessions_with_errors": sessions_with_errors,
        }

    def export_session(self, conv_id: str, format: str = "json") -> str:
        """Esporta una sessione in JSON o Markdown."""
        if conv_id not in self._sessions:
            return json.dumps({"error": f"Session '{conv_id}' not found"})

        turns = self._sessions[conv_id]

        if format == "markdown":
            lines = [f"# Chat Session: `{conv_id}`\n"]
            meta = self._session_meta.get(conv_id, {})
            if meta.get("project"):
                lines.append(f"**Project:** {meta['project']}  \n")
            if meta.get("user_id"):
                lines.append(f"**User:** {meta['user_id']}  \n")
            if meta.get("created_at"):
                dt = time.strftime("%Y-%m-%d %H:%M:%S",
                                   time.localtime(meta["created_at"]))
                lines.append(f"**Started:** {dt}  \n")
            lines.append(f"**Turns:** {len(turns)}  \n")
            lines.append("")
            lines.append("---")
            lines.append("")

            for i, turn in enumerate(turns, 1):
                header = f"### Turn {i}: {turn.role.title()}"
                meta_parts = []
                if turn.project:
                    meta_parts.append(f"📁 {turn.project}")
                if turn.model:
                    meta_parts.append(f"🤖 {turn.model}")
                meta_parts.append(
                    f"📊 {turn.prompt_tokens}↑ {turn.completion_tokens}↓ "
                    f"({turn.duration_ms:.0f}ms)")
                if turn.has_tool_calls:
                    meta_parts.append("🔧 " + ", ".join(turn.tool_names or []))
                if turn.error:
                    meta_parts.append(f"❌ {turn.error}")

                lines.append(header)
                lines.append("> " + " · ".join(meta_parts))
                lines.append("")
                lines.append(turn.content or "*[vuoto]*")
                lines.append("")
                lines.append("---")
                lines.append("")
            return "\n".join(lines)

        # JSON format (default)
        return json.dumps(
            [t.to_dict() for t in turns],
            indent=2, ensure_ascii=False, default=str,
        )

    def export_all(self, format: str = "json") -> str:
        """Esporta tutte le sessioni."""
        if format == "markdown":
            parts = []
            for conv_id in list(self._sessions.keys()):
                parts.append(self.export_session(conv_id, format="markdown"))
                parts.append("\n\n")
            return "".join(parts)

        result = {}
        for conv_id, turns in self._sessions.items():
            result[conv_id] = [t.to_dict() for t in turns]
        return json.dumps(result, indent=2, ensure_ascii=False, default=str)

    # ── Persistenza ────────────────────────────

    def persist(self, filepath: str) -> bool:
        """Salva tutte le sessioni su file JSON.

        Args:
            filepath: Percorso del file JSON.

        Returns:
            True se il salvataggio è riuscito.
        """
        try:
            data = {
                "version": 1,
                "exported_at": time.time(),
                "sessions": {},
            }
            for conv_id, turns in self._sessions.items():
                data["sessions"][conv_id] = {
                    "meta": self._session_meta.get(conv_id, {}),
                    "turns": [t.to_dict() for t in turns],
                }

            os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False, default=str)

            logger.info(f"💾 SessionStore: {len(self._sessions)} sessioni "
                        f"salvate su {filepath}")
            return True
        except Exception as e:
            logger.error(f"SessionStore persist error: {e}")
            return False

    def load(self, filepath: str) -> bool:
        """Carica sessioni da file JSON.

        Args:
            filepath: Percorso del file JSON.

        Returns:
            True se il caricamento è riuscito.
        """
        if not os.path.isfile(filepath):
            logger.info(f"SessionStore: nessun file da caricare ({filepath})")
            return False
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)

            sessions_data = data.get("sessions", {})
            loaded = 0
            for conv_id, sdata in sessions_data.items():
                meta = sdata.get("meta", {})
                turns_data = sdata.get("turns", [])
                if not turns_data:
                    continue

                turns = deque(maxlen=self.max_turns_per_session)
                for td in turns_data:
                    td.pop("datetime", None)  # Rimosso il campo calcolato
                    turns.append(MessageTurn(**td))

                self._sessions[conv_id] = turns
                self._session_meta[conv_id] = meta
                loaded += 1

            logger.info(f"📂 SessionStore: caricate {loaded} sessioni "
                        f"da {filepath}")
            return loaded > 0
        except Exception as e:
            logger.error(f"SessionStore load error: {e}")
            return False

    # ── Internal ───────────────────────────────

    def _evict_oldest(self) -> None:
        """Rimuove la sessione meno recente per fare spazio."""
        if not self._session_meta:
            return
        oldest_id = min(
            self._session_meta,
            key=lambda cid: self._session_meta[cid].get("last_activity", 0),
        )
        self._sessions.pop(oldest_id, None)
        self._session_meta.pop(oldest_id, None)
        logger.debug(f"🗑️ SessionStore: rimosso sessione {oldest_id} (limite raggiunto)")
