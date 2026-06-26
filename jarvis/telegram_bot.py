"""
Bot Telegram — Handler per comandi e messaggi via Telegram.
"""

from config import (
    logger, LLM_OPTIONS,
    ALLOWED_USERS, ADMIN_USERS, TELEGRAM_ENABLED
)
from prompt_builder import build_omniscient_prompt
from llm_engine import engine
import state
import time
import asyncio
import re
from collections import deque
import tempfile
import os
import sys

user_sessions = {}
user_locks = {}
SESSION_TTL = 600
whisper_model = None

if TELEGRAM_ENABLED:
    from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
    from telegram.ext import ContextTypes
    from gtts import gTTS
    import uuid

    callback_store = {}
    
    def get_main_menu(user_id=None):
        buttons = [
            [KeyboardButton("📁 Esplora Progetti"), KeyboardButton("📋 Task, ToDo & Notifiche")],
            [KeyboardButton("🌐 Info Ricerca Web"), KeyboardButton("❓ Aiuto / Guida")],
            [KeyboardButton("🤖 Mio Userbot")]
        ]
        if user_id is not None and str(user_id) in ADMIN_USERS:
            buttons.append([KeyboardButton("⚙️ Admin"), KeyboardButton("🖥️ Infrastruttura")])
        return ReplyKeyboardMarkup(
            buttons,
            resize_keyboard=True,
            is_persistent=True
        )
    
    def get_callback_id(action, path):
        cid = uuid.uuid4().hex[:8]
        callback_store[cid] = (action, path)
        return cid

    def build_ls_keyboard(folders, files, current_path):
        keyboard = []
        
        if current_path:
            parent_path = os.path.dirname(current_path) if '/' in current_path else None
            cid = get_callback_id('ls', parent_path)
            keyboard.append([InlineKeyboardButton("🔙 Su di un livello", callback_data=cid)])
            
        row = []
        for f in folders[:40]:
            target = f"{current_path}/{f}" if current_path else f
            cid = get_callback_id('ls', target)
            row.append(InlineKeyboardButton(f"📁 {f}", callback_data=cid))
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)
            row = []
            
        for f in files[:40]:
            target = f"{current_path}/{f}" if current_path else f
            cid = get_callback_id('dl', target)
            row.append(InlineKeyboardButton(f"📄 {f}", callback_data=cid))
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)
            
        return InlineKeyboardMarkup(keyboard)

    async def auth_middleware(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Middleware di sicurezza globale: blocca qualsiasi update da utenti non autorizzati."""
        if update.effective_user and str(update.effective_user.id) not in ALLOWED_USERS:
            logger.warning(f"Accesso negato da utente non autorizzato: {update.effective_user.id} ({update.effective_user.username})")
            from telegram.ext import ApplicationHandlerStop
            raise ApplicationHandlerStop()

    async def telegram_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handler per il comando /start."""
        msg = update.message or update.edited_message
        if not msg: return
        await msg.reply_text("👋 Collateral Studios Agent attivo. Scegli un'opzione dal menu in basso o scrivimi un messaggio.", reply_markup=get_main_menu(update.effective_user.id))

    async def telegram_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handler per i click sui bottoni inline (esplorazione/download)."""
        query = update.callback_query
        await query.answer()
        
        cid = query.data
        if cid not in callback_store:
            await query.edit_message_text("❌ Azione scaduta o non valida.")
            return
            
        action, path = callback_store[cid]
        
        if action.startswith('admin_') and str(query.from_user.id) not in ADMIN_USERS:
            await query.edit_message_text("❌ Accesso negato al pannello di amministrazione.")
            return

        if action == 'ls':
            from rag import generate_telegram_ls_data
            data = generate_telegram_ls_data(path)
            if "error" in data:
                await query.edit_message_text(data["error"])
                return
            
            kb = build_ls_keyboard(data["folders"], data["files"], data["current_path"])
            await query.edit_message_text(data["text"], reply_markup=kb, parse_mode="Markdown")
            
        elif action == 'dl':
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Sì, scarica", callback_data=get_callback_id('dl_conf', path)),
                 InlineKeyboardButton("❌ Annulla", callback_data=get_callback_id('ls', os.path.dirname(path) if '/' in path else None))]
            ])
            await query.edit_message_text(f"Vuoi scaricare il file `📄 {os.path.basename(path)}`?", reply_markup=kb, parse_mode="Markdown")
            
        elif action == 'dl_conf':
            from config import DOC_DIR
            full_path = os.path.join(DOC_DIR, path)
            if not os.path.exists(full_path):
                await query.edit_message_text("❌ File non trovato.")
                return
                
            await query.edit_message_text(f"📤 Invio di `📄 {os.path.basename(path)}` in corso...", parse_mode="Markdown")
            with open(full_path, 'rb') as f:
                await context.bot.send_document(chat_id=update.effective_chat.id, document=f)

        elif action == 'admin_list':
            from config import ALLOWED_USERS
            users_str = "\n".join([f"- `{uid}`" for uid in ALLOWED_USERS])
            await query.edit_message_text(f"📋 **Lista Utenti Autorizzati:**\n{users_str}", parse_mode="Markdown")

        elif action == 'admin_dashboard':
            import state
            import os
            import subprocess
            from llm_engine import engine
            from config import ALLOWED_USERS
            try:
                process = open('/proc/self/statm').read().split()[1]
                ram_mb = round((int(process) * os.sysconf('SC_PAGE_SIZE')) / (1024 * 1024), 1)
            except: ram_mb = 0
            
            active_todos, active_crons = 0, 0
            try:
                from task_manager import load_tasks
                active_todos = len([t for t in load_tasks().values() if t.get('status') != 'done'])
                from cron_agent import load_jobs
                active_crons = len(load_jobs())
            except: pass
            
            total_chunks = sum(len(f_data.get('chunks', [])) for f_data in state.rag_state.values())
            pending_events = state.file_event_queue.qsize() if hasattr(state, "file_event_queue") and state.file_event_queue else 0
            
            models_str_parts = []
            total_vram_mb = 0
            try:
                gpu = subprocess.run(
                    ["nvidia-smi", "--query-gpu=memory.used,memory.total",
                     "--format=csv,noheader,nounits"],
                    capture_output=True, text=True, timeout=5
                )
                if gpu.returncode == 0:
                    parts = gpu.stdout.strip().split(", ")
                    if len(parts) >= 2:
                        used, total = parts[0], parts[1]
                        total_vram_mb = int(used)
                        pct = int(used) / int(total) * 100 if int(total) > 0 else 0
                        models_str_parts.append(f"• GPU VRAM: `{used} MB / {total} MB ({pct:.0f}%)`")
            except: pass
            
            chat_status = "✅ Caricato" if engine.chat_model else "❌ Non caricato"
            embed_status = "✅ Caricato" if engine.embed_model else "❌ Non caricato"
            chat_name = os.path.basename(os.environ.get("LLAMA_MODEL_PATH", "?"))
            embed_name = os.path.basename(os.environ.get("LLAMA_EMBED_MODEL_PATH", "?"))
            models_str_parts.append(f"• Chat: `{chat_name}` — {chat_status}")
            models_str_parts.append(f"• Embed: `{embed_name}` — {embed_status}")
            models_str = "\n".join(models_str_parts)
            
            msg = (
                f"📊 **Collateral Matrix Telemetry**\n\n"
                f"🧠 **Vector KB:**\n"
                f"• File Tracked: `{len(state.rag_state)}`\n"
                f"• Chunks Vettoriali: `{total_chunks}`\n"
                f"• Coda Watchdog: `{pending_events}`\n\n"
                f"🤖 **Agent State:**\n"
                f"• Task in Sospeso: `{active_todos}`\n"
                f"• Cron Jobs: `{active_crons}`\n"
                f"• Task Asincroni (Python): `{len(state.background_tasks)}`\n\n"
                f"⚡ **Neural Engine (GPU VRAM):**\n"
                f"{models_str}\n\n"
                f"🩺 **System:**\n"
                f"• Mem0 Proxy RAM: `{ram_mb} MB`\n"
                f"• Utenti Telegram ACL: `{len(ALLOWED_USERS)}`"
            )
            await query.edit_message_text(msg, parse_mode="Markdown")

        elif action == 'admin_add':
            user_sessions[query.from_user.id] = user_sessions.get(query.from_user.id, {"messages": deque(maxlen=10), "last_active": time.time()})
            user_sessions[query.from_user.id]["admin_state"] = "awaiting_add"
            await query.edit_message_text("➕ Scrivi l'ID Telegram dell'utente da aggiungere:")

        elif action == 'admin_rm':
            user_sessions[query.from_user.id] = user_sessions.get(query.from_user.id, {"messages": deque(maxlen=10), "last_active": time.time()})
            user_sessions[query.from_user.id]["admin_state"] = "awaiting_rm"
            await query.edit_message_text("➖ Scrivi l'ID Telegram dell'utente da rimuovere:")

        elif action == 'admin_backup':
            await query.edit_message_text("💾 Esportazione memoria in corso...")
            from memory_backup import export_memory_to_json
            success, result = await export_memory_to_json()
            if success:
                with open(result, 'rb') as f:
                    await context.bot.send_document(chat_id=update.effective_chat.id, document=f, caption="✅ Backup Memoria completato.")
                os.remove(result)
            else:
                await query.edit_message_text(f"❌ Errore backup: {result}")

        elif action == 'admin_restore':
            user_sessions[query.from_user.id] = user_sessions.get(query.from_user.id, {"messages": deque(maxlen=10), "last_active": time.time()})
            user_sessions[query.from_user.id]["admin_state"] = "awaiting_restore"
            await query.edit_message_text("✍️ Scrivi in chat il nome del file di backup da ripristinare (es. `20231024_153000`).", parse_mode="Markdown")

        elif action == 'admin_reset_memory':
            if query.from_user.id in user_sessions:
                del user_sessions[query.from_user.id]
            await query.edit_message_text("🧹 Memoria di sessione (buffer messaggi recenti) cancellata.")

        elif action == 'admin_reset_db_req':
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Conferma (WIPE)", callback_data=get_callback_id('admin_reset_db_exec', ''))],
                [InlineKeyboardButton("❌ Annulla", callback_data=get_callback_id('admin_reset_cancel', ''))]
            ])
            await query.edit_message_text("⚠️ **ATTENZIONE** ⚠️\nSei sicuro di voler svuotare il database vettoriale RAG? Tutti i file verranno re-indicizzati da zero.\n\nVuoi procedere?", reply_markup=kb, parse_mode="Markdown")
            
        elif action == 'admin_reset_cancel':
            await query.edit_message_text("❌ Operazione annullata.")

        elif action == 'admin_reset_db_exec':
            await query.edit_message_text("⏳ Richiesta di WIPE in corso, riavvio ingestion...")
            try:
                import httpx
                async with httpx.AsyncClient() as client:
                    resp = await client.post("http://127.0.0.1:8000/api/reset-all", timeout=20.0)
                if resp.status_code == 200:
                    await query.edit_message_text("✅ Reset totale completato. Ingestion RAG ripartita da zero.")
                else:
                    await query.edit_message_text(f"❌ Errore durante il reset: HTTP {resp.status_code}")
            except Exception as e:
                await query.edit_message_text(f"❌ Errore durante il reset: {e}")

        # ── Istruisci Jarvis (agy flow) ──
        elif action == 'admin_agy_start':
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🧠 Mem0 (Ricordo)", callback_data=get_callback_id('admin_agy_mode', 'mem0'))],
                [InlineKeyboardButton("📄 RAG (Documento)", callback_data=get_callback_id('admin_agy_mode', 'rag'))],
                [InlineKeyboardButton("❌ Annulla", callback_data=get_callback_id('admin_agy_cancel', ''))]
            ])
            await query.edit_message_text(
                "🧠 **Istruisci Jarvis**\n\n"
                "Come vuoi salvare l'informazione?\n\n"
                "• **Mem0** → ricordo personale (richiamato nel super-prompt, max ~800 char)\n"
                "• **RAG** → documento indicizzato (ricerca vettoriale, chunks illimitati)",
                reply_markup=kb, parse_mode="Markdown"
            )

        elif action == 'admin_agy_cancel':
            await query.edit_message_text("❌ Operazione annullata.")

        elif action == 'admin_agy_mode':
            mode = path  # "mem0" or "rag"
            user_sessions[query.from_user.id] = user_sessions.get(query.from_user.id, {"messages": deque(maxlen=10), "last_active": time.time()})
            user_sessions[query.from_user.id]["agy_mode"] = mode

            # Recupera lista progetti dal RAG
            try:
                projects = await list_rag_projects()
            except Exception:
                projects = []

            kb = []
            for p in projects:
                kb.append([InlineKeyboardButton(f"📁 {p}", callback_data=get_callback_id('admin_agy_project', p))])
            kb.append([InlineKeyboardButton("📁 Nessun progetto", callback_data=get_callback_id('admin_agy_project', ''))])
            kb.append([InlineKeyboardButton("🔙 Indietro", callback_data=get_callback_id('admin_agy_start', ''))])

            mode_label = "Ricordo (Mem0)" if mode == "mem0" else "Documento (RAG)"
            await query.edit_message_text(
                f"🧠 **Istruisci Jarvis** → {mode_label}\n\n"
                "A quale progetto associare l'informazione?",
                reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown"
            )

        elif action == 'admin_agy_project':
            project = path  # project name or empty
            user_sessions[query.from_user.id]["agy_project"] = project
            user_sessions[query.from_user.id]["admin_state"] = "awaiting_agy_content"

            project_label = f"progetto **{project}**" if project else "nessun progetto"
            mode = user_sessions[query.from_user.id].get("agy_mode", "mem0")
            mode_label = "ricordo (Mem0)" if mode == "mem0" else "documento (RAG)"

            await query.edit_message_text(
                f"✍️ **Istruisci Jarvis**\n\n"
                f"Modalità: {mode_label}\n"
                f"Progetto: {project_label}\n\n"
                f"Scrivi ora il contenuto da far imparare a Jarvis.\n"
                f"{'Più è dettagliato e strutturato, meglio sarà indicizzato.' if mode == 'rag' else 'Sarà ricordato come preferenza personale.'}\n\n"
                f"_Oppure invia /annulla per tornare indietro._",
                parse_mode="Markdown"
            )
            return

        elif action == 'admin_agy_confirm':
            session = user_sessions.get(query.from_user.id, {})
            mode = session.get("agy_mode", "mem0")
            project = session.get("agy_project", "")
            content = session.get("agy_content", "")

            if not content:
                await query.edit_message_text("❌ Nessun contenuto da salvare.")
                return

            await query.edit_message_text("⏳ Salvataggio in corso...")

            try:
                if mode == "rag":
                    # Scrivi file nella directory RAG
                    from config import DOC_DIR
                    safe_name = content[:60].strip().lower().replace(' ', '-')
                    safe_name = ''.join(c for c in safe_name if c.isalnum() or c in '-_')
                    if not safe_name:
                        safe_name = "document"
                    ts = time.strftime("%Y%m%d-%H%M%S")
                    target_dir = os.path.join(DOC_DIR, project) if project else DOC_DIR
                    os.makedirs(target_dir, exist_ok=True)
                    filepath = os.path.join(target_dir, f"{ts}-{safe_name}.md")
                    with open(filepath, "w", encoding="utf-8") as f:
                        f.write(content)

                    # Salva anche in Mem0 come riferimento
                    summary = content[:700].rstrip()
                    if len(content) > 700:
                        summary += "..."
                    container_path = f"/app/documents/{project}/" if project else ""
                    mem = f"<MEMORY>📄 Documento: {container_path}{ts}-{safe_name}.md"
                    if project:
                        mem += f" (progetto: {project})"
                    mem += f"\n{summary}\nFonte completa disponibile nel RAG.</MEMORY>"

                    async with httpx.AsyncClient() as client:
                        await client.post("http://127.0.0.1:8000/api/chat", json={
                            "model": "local",
                            "messages": [{"role": "user", "content": mem}],
                            "options": {"skip_rag": True, "concise": True},
                            "stream": False
                        }, timeout=30.0)

                    await query.edit_message_text(
                        f"✅ **Documento salvato**\n\n"
                        f"📄 `{filepath}`\n"
                        f"🧠 Memoria di riferimento impressa.\n"
                        f"Il watchdog RAG indicizzerà il file in Qdrant.",
                        parse_mode="Markdown"
                    )

                else:  # mem0
                    mem = f"<MEMORY>{content}</MEMORY>"
                    async with httpx.AsyncClient() as client:
                        resp = await client.post("http://127.0.0.1:8000/api/chat", json={
                            "model": "local",
                            "messages": [{"role": "user", "content": mem}],
                            "options": {"skip_rag": True, "concise": True},
                            "stream": False
                        }, timeout=30.0)

                    result = resp.json()
                    reply = result.get("message", {}).get("content", "") or result.get("response", "")
                    out = "✅ **Ricordo salvato in Mem0**"
                    if reply:
                        out += f"\n\nJarvis: {reply[:300]}"
                    await query.edit_message_text(out, parse_mode="Markdown")

            except Exception as e:
                await query.edit_message_text(f"❌ Errore: {e}")

            # Pulisci stato
            session["admin_state"] = None
            session.pop("agy_mode", None)
            session.pop("agy_project", None)
            session.pop("agy_content", None)

        elif action == 'admin_agy_reject':
            session = user_sessions.get(query.from_user.id, {})
            session["admin_state"] = None
            session.pop("agy_mode", None)
            session.pop("agy_project", None)
            session.pop("agy_content", None)
            await query.edit_message_text("❌ Operazione annullata.")

        elif action == 'userbot_wl_list':
            from telegram_userbot_manager import load_user_config
            cfg = load_user_config(query.from_user.id)
            wl = cfg.get("whitelist", [])
            if not wl:
                await query.edit_message_text("📋 Whitelist vuota. Nessuno può usare il tuo Userbot.")
            else:
                wl_str = "\n".join([f"- `{c}`" for c in wl])
                await query.edit_message_text(f"📋 **Whitelist Userbot:**\n{wl_str}", parse_mode="Markdown")

        elif action == 'userbot_wl_add':
            user_sessions[query.from_user.id] = user_sessions.get(query.from_user.id, {"messages": deque(maxlen=10), "last_active": time.time()})
            user_sessions[query.from_user.id]["userbot_state"] = "userbot_wl_add"
            await query.edit_message_text("➕ Scrivi l'ID Telegram o l'Username (senza @) del contatto da autorizzare:")

        elif action == 'userbot_wl_rm':
            user_sessions[query.from_user.id] = user_sessions.get(query.from_user.id, {"messages": deque(maxlen=10), "last_active": time.time()})
            user_sessions[query.from_user.id]["userbot_state"] = "userbot_wl_rm"
            await query.edit_message_text("➖ Scrivi l'ID Telegram o l'Username da rimuovere:")

        elif action == 'userbot_disconnect':
            from telegram_userbot_manager import active_clients, get_session_path, get_config_path
            client = active_clients.pop(query.from_user.id, None)
            if client:
                await client.disconnect()
            try:
                os.remove(get_session_path(query.from_user.id))
            except OSError:
                pass
            await query.edit_message_text("🔌 Userbot disconnesso e file di sessione eliminato.")


        elif action == 'admin_cron':
            from cron_agent import load_jobs
            jobs = load_jobs()
            if not jobs:
                await query.edit_message_text("⏱️ Nessun task schedulato attivo.")
                return
            msg = "⏱️ **Task Schedulati Attivi:**\n"
            for jid, data in jobs.items():
                msg += f"- `{jid}`: `{data['cron']}` -> _{data['prompt']}_\n"
            await query.edit_message_text(msg, parse_mode="Markdown")

        elif action == 'task_list':
            from task_manager import get_open_tasks
            tasks = get_open_tasks(query.from_user.id)
            filtered = {}
            for k, v in tasks.items():
                owner = v.get("owner", "global")
                if path == "personal" and owner == str(query.from_user.id):
                    filtered[k] = v
                elif path == "project" and owner == "global":
                    filtered[k] = v
            
            if not filtered:
                title = "Personali" if path == "personal" else "di Progetto"
                await query.edit_message_text(f"🎉 Nessun task {title} in sospeso!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Indietro", callback_data=get_callback_id('task_back', ''))]]))
            else:
                title = "Personali" if path == "personal" else "di Progetto"
                out = f"📋 **To-Do List ({title}):**\nSeleziona un task per gestirlo:"
                kb = []
                for k, v in filtered.items():
                    label = v['desc'][:40] + "..." if len(v['desc']) > 40 else v['desc']
                    kb.append([InlineKeyboardButton(label, callback_data=get_callback_id('task_view', k))])
                kb.append([InlineKeyboardButton("🔙 Indietro", callback_data=get_callback_id('task_back', ''))])
                await query.edit_message_text(out, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
                
        elif action == 'task_view':
            from task_manager import get_open_tasks
            tasks = get_open_tasks(query.from_user.id)
            if path not in tasks:
                await query.edit_message_text("⚠️ Task non trovato.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Indietro", callback_data=get_callback_id('task_back', ''))]]))
                return
            t = tasks[path]
            out = f"📋 **Dettaglio Task**\n\n"
            out += f"**ID:** `{path}`\n"
            out += f"**Descrizione:** {t['desc']}\n"
            out += f"**Priorità:** {t['priority']}\n"
            out += f"**Scadenza:** {t['deadline']}\n"
            out += f"**Tipo:** {'Personale' if str(t.get('owner', 'global')) == str(query.from_user.id) else 'Progetto'}\n"
            
            kb = [
                [InlineKeyboardButton("✅ Completa Task", callback_data=get_callback_id('task_done', path))],
                [InlineKeyboardButton("🗑️ Rimuovi Task", callback_data=get_callback_id('task_rm', path))],
                [InlineKeyboardButton("🔙 Torna alla Lista", callback_data=get_callback_id('task_list', 'personal' if str(t.get('owner', 'global')) == str(query.from_user.id) else 'project'))]
            ]
            await query.edit_message_text(out, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
                
        elif action == 'task_done':
            from task_manager import mark_done
            success = mark_done(path, query.from_user.id)
            if success:
                await query.edit_message_text(f"✅ Task completato!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Indietro", callback_data=get_callback_id('task_back', ''))]]))
            else:
                await query.edit_message_text(f"❌ Impossibile completare il task.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Indietro", callback_data=get_callback_id('task_back', ''))]]))
        elif action == 'task_rm':
            from task_manager import remove_todo
            success = remove_todo(path, query.from_user.id)
            if success:
                await query.edit_message_text(f"🗑️ Task rimosso!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Indietro", callback_data=get_callback_id('task_back', ''))]]))
            else:
                await query.edit_message_text(f"❌ Impossibile rimuovere il task.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Indietro", callback_data=get_callback_id('task_back', ''))]]))
                
        elif action == 'cron_list':
            from cron_agent import load_jobs
            jobs = load_jobs()
            filtered = {k: v for k, v in jobs.items() if str(v.get("chat_id", "")) == str(update.effective_chat.id)}
            if not filtered:
                await query.edit_message_text("⏱️ Nessuna notifica schedulata attiva.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Indietro", callback_data=get_callback_id('task_back', ''))]]))
                return
            out = "⏰ **Le tue Notifiche Schedulate (Seleziona per gestire):**\n"
            kb = []
            for jid, data in filtered.items():
                if "cron" in data:
                    label = f"{data['cron']} - {data['prompt']}"
                elif "date" in data:
                    label = f"Singola ({data['date']}) - {data['prompt']}"
                else:
                    label = data['prompt']
                label = label[:40] + "..." if len(label) > 40 else label
                kb.append([InlineKeyboardButton(label, callback_data=get_callback_id('cron_view', jid))])
            kb.append([InlineKeyboardButton("🔙 Indietro", callback_data=get_callback_id('task_back', ''))])
            await query.edit_message_text(out, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
            
        elif action == 'cron_view':
            from cron_agent import load_jobs
            jobs = load_jobs()
            if path not in jobs:
                await query.edit_message_text("⚠️ Notifica non trovata.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Indietro", callback_data=get_callback_id('task_back', ''))]]))
                return
            data = jobs[path]
            out = f"⏰ **Dettaglio Notifica Schedulata**\n\n"
            out += f"**ID:** `{path}`\n"
            if "cron" in data:
                out += f"**Cron:** `{data['cron']}`\n"
                out += f"**Tipo:** Ricorrente\n"
            elif "date" in data:
                out += f"**Data:** `{data['date']}`\n"
                out += f"**Tipo:** Singola\n"
            out += f"**Prompt/Azione:** {data['prompt']}\n"
            
            kb = [
                [InlineKeyboardButton("🗑️ Rimuovi Notifica", callback_data=get_callback_id('cron_rm', path))],
                [InlineKeyboardButton("🔙 Torna alla Lista", callback_data=get_callback_id('cron_list', ''))]
            ]
            await query.edit_message_text(out, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
            
        elif action == 'cron_rm':
            from cron_agent import remove_cron_job
            if remove_cron_job(path):
                await query.edit_message_text(f"🗑️ Notifica rimossa con successo!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Indietro", callback_data=get_callback_id('task_back', ''))]]))
            else:
                await query.edit_message_text("❌ Impossibile rimuovere la notifica.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Indietro", callback_data=get_callback_id('task_back', ''))]]))
                
        elif action == 'task_add_prompt':
            user_sessions[query.from_user.id] = user_sessions.get(query.from_user.id, {"messages": deque(maxlen=10), "last_active": time.time()})
            user_sessions[query.from_user.id]["admin_state"] = "awaiting_task_add"
            await query.edit_message_text("✍️ Scrivi in chat la descrizione del task (es. 'Comprare il latte | personale | alta | domani' o semplicemente 'Comprare il latte').\n\nFormato: `descrizione | tipo (personale/progetto) | priorità | scadenza`\nSe scrivi solo il testo, i default saranno: personale, media, nessuna.", parse_mode="Markdown")

        elif action == 'task_back':
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("👤 Task Personali", callback_data=get_callback_id('task_list', 'personal')),
                 InlineKeyboardButton("🌍 Task di Progetto", callback_data=get_callback_id('task_list', 'project'))],
                [InlineKeyboardButton("⏰ Le mie Notifiche", callback_data=get_callback_id('cron_list', ''))],
                [InlineKeyboardButton("➕ Aggiungi Task", callback_data=get_callback_id('task_add_prompt', ''))]
            ])
            await query.edit_message_text("Gestione Task e Notifiche:", reply_markup=kb)

    def per_user_lock(func):
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
            user_id = str(update.effective_user.id)
            async with user_locks.setdefault(user_id, asyncio.Lock()):
                return await func(update, context)
        return wrapper

    @per_user_lock
    async def handle_telegram_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handler per messaggi di testo e vocali: arricchimento omnisciente + risposta Ollama + Session Buffer."""
        msg = update.message or update.edited_message
        if not msg: return
        user_id = str(update.effective_user.id)
        
        user_text = msg.text
        is_voice = False
        
        # Intercettazione Menu a Pulsanti
        if user_text == "📁 Esplora Progetti":
            from rag import generate_telegram_ls_data
            data = generate_telegram_ls_data(None)
            if "error" in data:
                await msg.reply_text(data["error"], parse_mode="Markdown", reply_markup=get_main_menu(user_id))
                return
            kb = build_ls_keyboard(data["folders"], data["files"], data["current_path"])
            await msg.reply_text(data["text"], reply_markup=kb, parse_mode="Markdown")
            return
            
        elif user_text == "🌐 Info Ricerca Web":
            await msg.reply_text("💡 Per cercare sul web, basta che scrivi la tua richiesta in linguaggio naturale. Collateral Studios Agent capirà da solo se deve navigare online per risponderti!", reply_markup=get_main_menu(user_id))
            return
            
        elif user_text == "⚙️ Admin":
            if user_id not in ADMIN_USERS:
                await msg.reply_text("❌ Accesso negato.", reply_markup=get_main_menu(user_id))
                return
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("📊 Dashboard Telemetria", callback_data=get_callback_id('admin_dashboard', ''))],
                [InlineKeyboardButton("📋 Lista Utenti", callback_data=get_callback_id('admin_list', ''))],
                [InlineKeyboardButton("➕ Aggiungi Utente", callback_data=get_callback_id('admin_add', ''))],
                [InlineKeyboardButton("➖ Rimuovi Utente", callback_data=get_callback_id('admin_rm', ''))],
                [InlineKeyboardButton("⏱️ Task Schedulati", callback_data=get_callback_id('admin_cron', ''))],
                [InlineKeyboardButton("💾 Backup Memoria", callback_data=get_callback_id('admin_backup', '')), InlineKeyboardButton("📂 Restore", callback_data=get_callback_id('admin_restore', ''))],
                [InlineKeyboardButton("🧹 Reset Sessione", callback_data=get_callback_id('admin_reset_memory', ''))],
                [InlineKeyboardButton("🧠 Istruisci Jarvis", callback_data=get_callback_id('admin_agy_start', ''))],
                [InlineKeyboardButton("🧨 Reset Database RAG", callback_data=get_callback_id('admin_reset_db_req', ''))]
            ])
            await msg.reply_text("⚙️ **Pannello di Amministrazione**\nScegli un'operazione:", reply_markup=kb, parse_mode="Markdown")
            return
            
        elif user_text == "📋 Task, ToDo & Notifiche":
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("👤 Task Personali", callback_data=get_callback_id('task_list', 'personal')),
                 InlineKeyboardButton("🌍 Task di Progetto", callback_data=get_callback_id('task_list', 'project'))],
                [InlineKeyboardButton("⏰ Le mie Notifiche", callback_data=get_callback_id('cron_list', ''))],
                [InlineKeyboardButton("➕ Aggiungi Task", callback_data=get_callback_id('task_add_prompt', ''))]
            ])
            await msg.reply_text("Gestione Task e Notifiche:", reply_markup=kb)
            return

        elif user_text == "🖥️ Infrastruttura":
            if user_id not in ADMIN_USERS:
                await msg.reply_text("❌ Accesso negato.", reply_markup=get_main_menu(user_id))
                return
            from infrastructure import load_infra
            infra = load_infra()
            if not infra:
                await msg.reply_text("🖥️ **Infrastruttura Vuota**\nNessun server registrato nel vault.", parse_mode="Markdown", reply_markup=get_main_menu(user_id))
            else:
                out = "🖥️ **Server Registrati (SSH):**\n\n"
                for name, srv in infra.items():
                    out += f"🔹 **{name}** -> `{srv.get('user')}@{srv.get('ip')}`\n"
                out += "\n💡 _Puoi chiedermi di lanciare comandi su questi server scrivendomi un messaggio!_"
                await msg.reply_text(out, parse_mode="Markdown", reply_markup=get_main_menu(user_id))
            return

        elif user_text == "❓ Aiuto / Guida":
            help_msg = (
                "🤖 **Guida all'uso dell'Agente Collateral Studios**\n\n"
                "Sono un assistente IA proattivo. Puoi parlarmi in linguaggio naturale tramite testo o **messaggi vocali**.\n\n"
                "**Ecco i miei superpoteri:**\n"
                "🧠 **Memoria a Lungo Termine:** Ricordo dettagli e abitudini. Ogni notte consolido le informazioni più utili. Usa il menu Admin per i Backup.\n"
                "📋 **Gestione Task & Notifiche:** Parlami naturalmente per aggiungere task o promemoria (es. _\"Ricordami di comprare il pane alle 18:00\"_), oppure usa il menu **Task, ToDo & Notifiche** per listare, completare, o rimuovere i task (personali o di progetto) e gli allarmi tramite dei comodi pulsanti interattivi!\n"
                "🖥️ **Gestione Server (SSH):** Gli Admin possono chiedermi in chat: _\"Accedi a prod e lancia un htop\"_. Io lo farò e ti riporterò l'output qui in chat.\n"
                "📁 **RAG e Ricerca Locale:** Usa i pulsanti _Esplora_ e _Ricerca Web_ per farmi studiare intere codebase o link internet per te.\n\n"
                "💡 _Più dettagli e contesto mi dai, migliori saranno le mie risposte!_"
            )
            await msg.reply_text(help_msg, parse_mode="Markdown", reply_markup=get_main_menu(user_id))
            return

        elif user_text == "🤖 Mio Userbot":
            if "telegram_userbot_manager" not in globals() and "telegram_userbot_manager" not in sys.modules:
                try:
                    import telegram_userbot_manager
                except ImportError:
                    await msg.reply_text("❌ Funzionalità Userbot disabilitata dal server.")
                    return
                    
            from telegram_userbot_manager import active_clients
            if user_id in active_clients:
                client = active_clients[user_id]
                if not await client.is_user_authorized():
                    await client.disconnect()
                    del active_clients[user_id]
                else:
                    kb = InlineKeyboardMarkup([
                        [InlineKeyboardButton("📋 Lista Whitelist", callback_data=get_callback_id('userbot_wl_list', ''))],
                        [InlineKeyboardButton("➕ Aggiungi a Whitelist", callback_data=get_callback_id('userbot_wl_add', ''))],
                        [InlineKeyboardButton("➖ Rimuovi da Whitelist", callback_data=get_callback_id('userbot_wl_rm', ''))],
                        [InlineKeyboardButton("🔌 Disconnetti Userbot", callback_data=get_callback_id('userbot_disconnect', ''))]
                    ])
                    await msg.reply_text("✅ Il tuo Userbot è attivo e connesso!\nGestisci la lista di contatti a cui può rispondere:", reply_markup=kb)
                    return
                
            if user_id not in user_sessions:
                user_sessions[user_id] = {"messages": deque(maxlen=10), "last_active": time.time()}
                
            user_sessions[user_id]["userbot_state"] = "awaiting_phone"
            await msg.reply_text("📱 Inserisci il tuo **numero di telefono** (completo di prefisso, es. +39333...):", parse_mode="Markdown")
            return

        session = user_sessions.get(user_id)
        if session and session.get("userbot_state"):
            state_val = session["userbot_state"]
            try:
                from telegram_userbot_manager import request_otp, sign_in_otp
            except ImportError:
                session["userbot_state"] = None
                return
                
            if state_val == "awaiting_phone":
                phone = user_text.strip()
                session["userbot_phone"] = phone
                await msg.reply_text(f"⏳ Richiesta codice OTP a Telegram per il numero {phone}...")
                success, reason_or_hash = await request_otp(user_id, phone)
                if success:
                    session["userbot_state"] = "awaiting_otp"
                    session["userbot_hash"] = reason_or_hash
                    await msg.reply_text("🔑 Telegram ti ha inviato un codice OTP nell'app.\n\n⚠️ **ATTENZIONE:** Per motivi di sicurezza, Telegram blocca il login se scrivi il codice normale in chat! Devi scriverlo **con dei trattini in mezzo**, ad esempio se il codice è `12345`, scrivilo come: `1-2-3-4-5`.", parse_mode="Markdown")
                else:
                    session["userbot_state"] = None
                    await msg.reply_text(f"❌ Impossibile richiedere il codice: {reason_or_hash}")
                return
                
            elif state_val == "awaiting_otp":
                # Rimuove gli spazi, trattini o underscore inseriti per bypassare il blocco di sicurezza di Telegram
                raw_code = user_text.strip()
                code = "".join(filter(str.isdigit, raw_code))
                session["userbot_otp"] = code
                await msg.reply_text("⏳ Autenticazione in corso...")
                success, reason = await sign_in_otp(user_id, session["userbot_phone"], code, session.get("userbot_hash"))
                if success:
                    session["userbot_state"] = None
                    await msg.reply_text("🎉 **Autenticazione Completata!**\nIl tuo Userbot è ora in ascolto. Ricordati che ignorerà tutti i messaggi finché non aggiungerai gli ID/Username alla tua whitelist privata (modifica il file `userbot_USERID.json` o chiedi all'Admin).", parse_mode="Markdown")
                else:
                    if "password" in reason.lower():
                        session["userbot_state"] = "awaiting_password"
                        await msg.reply_text("🔐 Autenticazione a due fattori attiva. Inserisci la tua password:")
                    else:
                        session["userbot_state"] = None
                        await msg.reply_text(f"❌ Errore di autenticazione: {reason}")
                return
                
            elif state_val == "awaiting_password":
                password = user_text.strip()
                await msg.reply_text("⏳ Verifica password in corso...")
                success, reason = await sign_in_otp(user_id, session["userbot_phone"], session["userbot_otp"], session.get("userbot_hash"), password=password)
                session["userbot_state"] = None
                if success:
                    await msg.reply_text("🎉 **Autenticazione Completata!**\nIl tuo Userbot è ora in ascolto. Ricordati che ignorerà tutti i messaggi finché non aggiungerai gli ID/Username alla tua whitelist privata.", parse_mode="Markdown")
                else:
                    await msg.reply_text(f"❌ Errore password: {reason}")
                return

            elif state_val == "userbot_wl_add":
                from telegram_userbot_manager import load_user_config, save_user_config
                contact = user_text.strip()
                if contact.lstrip('-').isdigit(): contact = int(contact)
                cfg = load_user_config(user_id)
                if contact not in cfg["whitelist"]:
                    cfg["whitelist"].append(contact)
                    save_user_config(user_id, cfg)
                    await msg.reply_text(f"✅ Contatto {contact} aggiunto alla whitelist.")
                else:
                    await msg.reply_text("⚠️ Contatto già in whitelist.")
                session["userbot_state"] = None
                return
                
            elif state_val == "userbot_wl_rm":
                from telegram_userbot_manager import load_user_config, save_user_config
                contact = user_text.strip()
                if contact.lstrip('-').isdigit(): contact = int(contact)
                cfg = load_user_config(user_id)
                if contact in cfg["whitelist"]:
                    cfg["whitelist"].remove(contact)
                    save_user_config(user_id, cfg)
                    await msg.reply_text(f"✅ Contatto {contact} rimosso dalla whitelist.")
                else:
                    await msg.reply_text("⚠️ Contatto non trovato.")
                session["userbot_state"] = None
                return

        if session and session.get("admin_state"):
            if user_id not in ADMIN_USERS:
                session["admin_state"] = None
                return

            # ── await_agy_content: ricevi il contenuto da salvare ──
            if session["admin_state"] == "awaiting_agy_content":
                if user_text.strip().lower() == "/annulla":
                    session["admin_state"] = None
                    session.pop("agy_mode", None)
                    session.pop("agy_project", None)
                    session.pop("agy_content", None)
                    await msg.reply_text("❌ Operazione annullata.", reply_markup=get_main_menu(user_id))
                    return

                session["agy_content"] = user_text.strip()
                mode = session.get("agy_mode", "mem0")
                project = session.get("agy_project", "")
                project_label = f"progetto **{project}**" if project else "nessun progetto"
                mode_label = "Ricordo (Mem0)" if mode == "mem0" else "Documento (RAG)"

                preview = user_text[:300].rstrip()
                if len(user_text) > 300:
                    preview += "..."

                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ Conferma", callback_data=get_callback_id('admin_agy_confirm', '')),
                     InlineKeyboardButton("❌ Annulla", callback_data=get_callback_id('admin_agy_reject', ''))]
                ])
                await msg.reply_text(
                    f"📝 **Anteprima contenuto:**\n\n"
                    f"{preview}\n\n"
                    f"Modalità: {mode_label}\n"
                    f"Progetto: {project_label}\n\n"
                    f"Confermi?",
                    reply_markup=kb, parse_mode="Markdown"
                )
                return

            from config import ALLOWED_USERS, save_allowed_users
            state_val = session["admin_state"]
            session["admin_state"] = None
            try:
                if state_val == "awaiting_task_add":
                    parts = [p.strip() for p in user_text.split("|")]
                    desc = parts[0]
                    t_type = parts[1].lower() if len(parts) > 1 else "personale"
                    prio = parts[2] if len(parts) > 2 else "media"
                    dead = parts[3] if len(parts) > 3 else "nessuna"
                    from task_manager import add_todo
                    tid = add_todo(desc, prio, dead, t_type, user_id)
                    await msg.reply_text(f"✅ Task [{tid}] aggiunto con successo come {t_type}.")
                    return
                
                target_id = int(user_text.strip())
                target_id_str = str(target_id)
                if state_val == "awaiting_add":
                    if target_id_str not in ALLOWED_USERS:
                        ALLOWED_USERS.append(target_id_str)
                        save_allowed_users()
                        await msg.reply_text(f"✅ Utente `{target_id}` aggiunto con successo.", parse_mode="Markdown")
                    else:
                        await msg.reply_text("⚠️ Utente già presente.")
                elif state_val == "awaiting_rm":
                    if target_id_str in ALLOWED_USERS:
                        ALLOWED_USERS.remove(target_id_str)
                        save_allowed_users()
                        await msg.reply_text(f"✅ Utente `{target_id}` rimosso con successo.", parse_mode="Markdown")
                    else:
                        await msg.reply_text("⚠️ Utente non trovato.")
            except ValueError:
                await msg.reply_text("❌ Input non valido. Operazione annullata.")
            return

        # Gestione messaggi vocali (8.7)
        if msg.voice or msg.audio:
            is_voice = True
            voice_file = await context.bot.get_file(msg.voice.file_id if msg.voice else msg.audio.file_id)
            
            with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tf:
                temp_path = tf.name
            
            await voice_file.download_to_drive(temp_path)
            
            # Caricamento lazy del modello Whisper
            global whisper_model
            if not whisper_model:
                from faster_whisper import WhisperModel
                logger.info("Caricamento modello Whisper in corso...")
                whisper_model = WhisperModel("tiny", device="cpu", compute_type="int8")
            
            def transcribe_sync(path):
                segments, _ = whisper_model.transcribe(path, language="it")
                return "".join([s.text for s in segments]).strip()
                
            user_text = await asyncio.to_thread(transcribe_sync, temp_path)
            os.remove(temp_path)
            
            if not user_text:
                await msg.reply_text("⚠️ Impossibile comprendere l'audio.")
                return
                
            await msg.reply_text(f"🎤 *Trascrizione:* {user_text}", parse_mode="Markdown")

        # Gestione Documenti (Restore Memoria)
        if msg.document:
            session = user_sessions.get(user_id)
            if session and session.get("admin_state") == "awaiting_restore":
                if not msg.document.file_name.endswith('.json'):
                    await msg.reply_text("❌ Formato non valido. Invia un file .json.")
                    session["admin_state"] = None
                    return
                
                file = await context.bot.get_file(msg.document.file_id)
                with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
                    temp_path = tf.name
                
                await file.download_to_drive(temp_path)
                
                from memory_backup import import_memory_from_json
                success, result = await import_memory_from_json(temp_path)
                os.remove(temp_path)
                
                session["admin_state"] = None
                if success:
                    await msg.reply_text(f"✅ Restore completato: {result}")
                else:
                    await msg.reply_text(f"❌ Errore durante il restore: {result}")
                return

        if not user_text:
            return

        # Intercept pending confirmation responses (Fix 9.1)
        from agent_tools import pending_confirmations
        chat_id = update.effective_chat.id
        if chat_id in pending_confirmations:
            future = pending_confirmations[chat_id]
            if not future.done():
                text_lower = user_text.strip().lower()
                if text_lower in ['y', 'yes', 'si', 'sì']:
                    future.set_result(True)
                elif text_lower in ['n', 'no']:
                    future.set_result(False)
                else:
                    await update.message.reply_text("Rispondi con **Y** per confermare o **N** per rifiutare.", parse_mode="Markdown")
                
                if text_lower in ['y', 'yes', 'si', 'sì', 'n', 'no']:
                    pending_confirmations.pop(chat_id, None)
                return

        now = time.time()
        
        # Buffer session management
        if user_id not in user_sessions or now - user_sessions[user_id]["last_active"] > SESSION_TTL:
            state.last_project_context.pop(user_id, None)
            user_sessions[user_id] = {"messages": deque(maxlen=40), "last_active": now}
            
        session = user_sessions[user_id]
        session["last_active"] = now
        session["messages"].append({"role": "user", "content": user_text})

        # Typing indicator asincrono continuo
        async def keep_typing():
            try:
                while True:
                    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action='typing')
                    await asyncio.sleep(4)
            except asyncio.CancelledError:
                pass
                
        typing_task = asyncio.create_task(keep_typing())

        try:
            from agent_tools import TOOLS_SCHEMA, execute_tool_call
            from memory import save_to_memory
            
            enriched_messages = await build_omniscient_prompt(list(session["messages"]), user_id, conversation_id=str(update.effective_chat.id))
            current_messages = enriched_messages.copy()
            
            max_iterations = 5
            iterations = 0
            bot_reply = ""
            
            while iterations < max_iterations:
                iterations += 1
                options = dict(LLM_OPTIONS)

                response = await engine.generate_chat(
                    current_messages,
                    tools=TOOLS_SCHEMA,
                    options=options,
                    stream=False
                )

                if "error" in response:
                    raise RuntimeError(response["error"])

                choice = response["choices"][0]["message"]
                message_data = dict(choice)
                
                # Aggiungiamo il messaggio dell'assistente alla cronologia per il prossimo loop (necessario per Ollama tools)
                current_messages.append(message_data)
                session["messages"].append(message_data)
                
                tool_calls = message_data.get("tool_calls", []) or []
                
                # Fallback: Qwen con chat_format=None emette tool call come testo
                # invece che nel campo strutturato tool_calls della API
                if not tool_calls:
                    from llm_engine import parse_qwen_tool_calls
                    content = message_data.get("content", "")
                    tool_calls = parse_qwen_tool_calls(content)
                    if tool_calls:
                        # Rimuovi i tag tool_call dal contenuto
                        import re as _re
                        clean_content = _re.sub(
                            r'<\|tool_call\|>.*?<\|tool_call\|>',
                            '', content, flags=_re.DOTALL
                        ).strip()
                        message_data["content"] = clean_content
                        # Aggiorna anche la cronologia
                        current_messages[-1]["content"] = clean_content
                        session["messages"][-1]["content"] = clean_content
                
                if not tool_calls:
                    bot_reply = message_data.get("content", "")
                    # Salva risposta AI in Mem0 per continuità conversazionale
                    if bot_reply:
                        asyncio.create_task(save_to_memory(f"AI: {bot_reply[:500]}", user_id=user_id))
                    break
                
                # Eseguiamo i tool call
                for tool_call in tool_calls:
                    tool_res = await execute_tool_call(tool_call, bot=context.bot, chat_id=update.effective_chat.id)
                    tool_msg = {
                        "role": "tool",
                        "content": tool_res,
                        "name": tool_call.get("function", {}).get("name", "unknown")
                    }
                    current_messages.append(tool_msg)
                    session["messages"].append(tool_msg)
                    # Mandiamo un feedback live su Telegram per far vedere che sta lavorando
                    await context.bot.send_message(chat_id=update.effective_chat.id, text=f"🔧 {tool_res}", disable_notification=True)

            # — Delegato a tag_processor.py: parsing centralizzato di TUTTI i tag —
            from tag_processor import process_all_tags, TagContext, strip_thinking_blocks, telegram_safe_format, telegram_prepare_markdown, close_orphaned_tags
            
            # Pre-processing: chiudi tag orfani (es. <MEMORY> troncato senza </MEMORY>)
            bot_reply = close_orphaned_tags(bot_reply)
            bot_reply = strip_thinking_blocks(bot_reply)
            tag_ctx = TagContext(
                user_id=user_id,
                chat_id=update.effective_chat.id,
            )
            parsed_reply, feedback = await process_all_tags(bot_reply, tag_ctx)
            for msg in feedback:
                parsed_reply += f"\n\n{msg}"

            # — Preparazione markdown comune: UNA VOLTA sul testo completo —
            parsed_reply = telegram_prepare_markdown(parsed_reply)

            # Note: session["messages"] has already been updated dynamically during the tool loop

            # Generazione vocale TTS se input era vocale
            if is_voice:
                def tts_sync(text, path):
                    # Rimuoviamo TUTTI i simboli markdown per il TTS
                    clean_text = text
                    # Rimuovi blocchi codice
                    clean_text = re.sub(r'```[\s\S]*?```', '', clean_text)
                    # Rimuovi formattazione inline
                    clean_text = re.sub(r'\*\*(.+?)\*\*', r'\1', clean_text)
                    clean_text = re.sub(r'\*(.+?)\*', r'\1', clean_text)
                    clean_text = re.sub(r'_(.+?)_', r'\1', clean_text)
                    clean_text = re.sub(r'`(.+?)`', r'\1', clean_text)
                    clean_text = re.sub(r'~~(.+?)~~', r'\1', clean_text)
                    clean_text = re.sub(r'\|\|(.+?)\|\|', r'\1', clean_text)
                    # Rimuovi link: [text](url) → text
                    clean_text = re.sub(r'\[(.+?)\]\(.+?\)', r'\1', clean_text)
                    # Rimuovi heading markers
                    clean_text = re.sub(r'^#{1,6}\s+', '', clean_text, flags=re.MULTILINE)
                    # Rimuovi bullet e numerazione
                    clean_text = re.sub(r'^[\*\-\+]\s+', '', clean_text, flags=re.MULTILINE)
                    clean_text = re.sub(r'^\d+\.\s+', '', clean_text, flags=re.MULTILINE)
                    # Rimuovi horizontal rules
                    clean_text = re.sub(r'^[-*_]{3,}\s*$', '', clean_text, flags=re.MULTILINE)
                    # Thematic breaks
                    clean_text = clean_text.replace('|', ', ')
                    clean_text = re.sub(r'\n{3,}', '\n\n', clean_text)
                    clean_text = clean_text.strip()
                    tts = gTTS(text=clean_text, lang='it')
                    tts.save(path)
                    
                with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tf:
                    tts_path = tf.name
                
                await asyncio.to_thread(tts_sync, parsed_reply, tts_path)
                with open(tts_path, 'rb') as audio_file:
                    await context.bot.send_voice(chat_id=update.effective_chat.id, voice=audio_file)
                os.remove(tts_path)

            # Funzione chunking sicura: non spezza blocchi markdown a metà
            def chunk_message_safe(text, chunk_size=4000):
                """
                Divide il testo in chunk che rispettano i confini dei blocchi markdown.
                Non spezza mai:
                  - *bold* o _italic_ a metà
                  - `code` o ```code blocks``` a metà
                  - [text](url) a metà
                  - Liste o heading a metà
                """
                chunks = []
                while len(text) > chunk_size:
                    # Trova il punto di split guardando indietro da chunk_size
                    # Cerca preferenzialmente: doppio newline, poi newline singolo, poi spazio
                    split_at = -1
                    
                    # 1. Cerca doppio newline (fine paragrafo)
                    dbl_nl = text.rfind('\n\n', 0, chunk_size)
                    if dbl_nl > chunk_size // 2:
                        split_at = dbl_nl + 2
                    else:
                        # 2. Cerca newline singolo (ma non dentro blockquote, lista, heading)
                        for candidate in range(min(chunk_size, len(text)), 0, -1):
                            if text[candidate] == '\n':
                                # Verifica che non sia dentro un blocco markdown
                                before = text[max(0, candidate - 3):candidate]
                                if not any(before.startswith(p) for p in ('---', '===', '```')):
                                    split_at = candidate + 1
                                    break
                    
                    # 3. Fallback: spazio
                    if split_at <= 0 or split_at > chunk_size:
                        split_at = text.rfind(' ', 0, chunk_size)
                        if split_at > 0:
                            split_at += 1
                        else:
                            split_at = chunk_size
                    
                    chunks.append(text[:split_at])
                    text = text[split_at:].lstrip()
                
                if text:
                    chunks.append(text)
                return chunks

            # Tentativo multiplo di parse_mode: prima MarkdownV2, poi Markdown, poi plain text
            for chunk in chunk_message_safe(parsed_reply):
                sent = False
                # Tentativo 1: MarkdownV2 (più moderno, supporta strikethrough/spoiler)
                try:
                    chunk_v2 = telegram_safe_format(chunk, use_markdown_v2=True)
                    await msg.reply_text(chunk_v2, parse_mode="MarkdownV2")
                    sent = True
                except Exception:
                    pass
                
                if not sent:
                    # Tentativo 2: Markdown legacy
                    try:
                        chunk_legacy = telegram_safe_format(chunk, use_markdown_v2=False)
                        await msg.reply_text(chunk_legacy, parse_mode="Markdown")
                        sent = True
                    except Exception as e:
                        logger.warning(f"Errore parsing Markdown su Telegram: {e}.")
                
                if not sent:
                    # Tentativo 3: Plain text (strip all markdown)
                    plain = re.sub(r'\*\*(.+?)\*\*', r'\1', chunk)
                    plain = re.sub(r'\*(.+?)\*', r'\1', plain)
                    plain = re.sub(r'_(.+?)_', r'\1', plain)
                    plain = re.sub(r'`(.+?)`', r'\1', plain)
                    plain = re.sub(r'\[(.+?)\]\(.+?\)', r'\1', plain)
                    plain = re.sub(r'~~(.+?)~~', r'\1', plain)
                    plain = re.sub(r'---+\s*', '', plain)
                    await msg.reply_text(plain)

        except Exception as e:
            logger.error(f"Errore Telegram [{type(e).__module__}.{type(e).__name__}]: {e}")
            import traceback
            logger.error(f"Traceback:\n{''.join(traceback.format_exc())}")
            await context.bot.send_message(chat_id=update.effective_chat.id, text="⚠️ Si è verificato un errore nell'elaborazione locale.")
        finally:
            typing_task.cancel()
