"""
Gestore Multi-Userbot — Gestisce le connessioni MTProto per i vari membri del team.
"""

import asyncio
import os
import json
import time
from collections import deque
from telethon import TelegramClient, events

from config import (
    logger, USERBOT_ENABLED, USERBOT_API_ID, USERBOT_API_HASH,
    OLLAMA_BASE, OLLAMA_MODEL, LLM_OPTIONS, GLOBAL_KEEP_ALIVE
)
import state

# active_clients: telegram_user_id -> TelegramClient
active_clients = {}
# userbot_sessions: chat_id -> deque (usato globalmente per rate limit o storia base)
userbot_sessions = {}
SESSION_TTL = 600

USERBOTS_DIR = "/app/mem0_data_v3/userbots"
os.makedirs(USERBOTS_DIR, exist_ok=True)

def get_session_path(user_id):
    return os.path.join(USERBOTS_DIR, f"userbot_{user_id}.session")

def get_config_path(user_id):
    return os.path.join(USERBOTS_DIR, f"userbot_{user_id}.json")

def load_user_config(user_id):
    path = get_config_path(user_id)
    if os.path.exists(path):
        with open(path, "r") as f:
            return json.load(f)
    return {"whitelist": []}

def save_user_config(user_id, config):
    with open(get_config_path(user_id), "w") as f:
        json.dump(config, f)

async def _handle_incoming_message(event, owner_id):
    """Gestisce un nuovo messaggio in arrivo sul client di un membro del team."""
    if not event.is_private:
        return
        
    chat_id = event.chat_id
    if chat_id == owner_id:
        return  # Non risponde a se stesso (Messaggi Salvati)
        
    sender = await event.get_sender()
    username = getattr(sender, 'username', '') or ''
    
    # Check Whitelist dell'owner
    config = load_user_config(owner_id)
    whitelist = config.get("whitelist", [])
    
    # Se la whitelist è vuota o il mittente non è in lista, ignoriamo
    if chat_id not in whitelist and username not in whitelist:
        return
        
    text = event.raw_text
    if not text:
        return
        
    logger.info(f"🤖 Userbot [{owner_id}]: Ricevuto msg da {username} ({chat_id})")
    
    # Nessun RAG, pura memoria di chat base per sicurezza
    now = time.time()
    session_key = f"{owner_id}_{chat_id}"
    if session_key not in userbot_sessions or now - userbot_sessions[session_key]["last_active"] > SESSION_TTL:
        userbot_sessions[session_key] = {"messages": deque(maxlen=6), "last_active": now}
        
    session = userbot_sessions[session_key]
    session["last_active"] = now
    session["messages"].append({"role": "user", "content": text})
    
    client = active_clients.get(owner_id)
    if not client:
        return

    try:
        async with client.action(chat_id, 'typing'):
            # Costruzione prompt minimal (NO RAG)
            system_prompt = (
                "Sei un assistente AI personale incaricato di rispondere ai messaggi "
                "per conto dell'utente. Rispondi in modo cordiale, molto conciso e naturale. "
                "NON rivelare dettagli tecnici, progetti interni o il funzionamento del RAG. "
                "Agisci come se fossi il segretario personale dell'utente."
            )
            
            messages = [{"role": "system", "content": system_prompt}]
            messages.extend(list(session["messages"]))
            
            payload = {
                "model": OLLAMA_MODEL,
                "messages": messages,
                "stream": False,
                "keep_alive": GLOBAL_KEEP_ALIVE,
                "options": LLM_OPTIONS
            }
            
            async with state.llm_semaphore:
                res = await state.http_client.post(f"{OLLAMA_BASE}/api/chat", json=payload, timeout=120.0)
            res.raise_for_status()
            bot_reply = res.json().get("message", {}).get("content", "")
            
        if bot_reply:
            delay = min(4.0, len(bot_reply) / 40.0)
            async with client.action(chat_id, 'typing'):
                await asyncio.sleep(delay)
                
            await event.reply(bot_reply)
            session["messages"].append({"role": "assistant", "content": bot_reply})
            logger.info(f"🤖 Userbot [{owner_id}]: Risposto a {username}.")
            
    except Exception as e:
        logger.error(f"Errore generazione Userbot per {owner_id}: {e}")


async def create_and_start_client(user_id):
    """Avvia il client se ha una sessione attiva."""
    if not USERBOT_API_ID or not str(USERBOT_API_ID).strip().isdigit() or not USERBOT_API_HASH:
        return False
        
    if user_id in active_clients:
        return True
        
    client = TelegramClient(get_session_path(user_id), int(str(USERBOT_API_ID).strip()), USERBOT_API_HASH)
    
    @client.on(events.NewMessage(incoming=True))
    async def msg_handler(event):
        await _handle_incoming_message(event, user_id)
        
    await client.connect()
    if await client.is_user_authorized():
        active_clients[user_id] = client
        logger.info(f"🤖 Userbot connesso automaticamente per l'utente {user_id}")
        return True
    else:
        await client.disconnect()
        return False

async def request_otp(user_id, phone):
    """Invia richiesta OTP."""
    if not USERBOT_API_ID or not str(USERBOT_API_ID).strip().isdigit() or not USERBOT_API_HASH:
        logger.error(f"OTP Request failed per {user_id}: TELEGRAM_API_ID o HASH non configurati o invalidi.")
        return False, "Configurazione API non valida"
        
    client = TelegramClient(get_session_path(user_id), int(str(USERBOT_API_ID).strip()), USERBOT_API_HASH)
    await client.connect()
    try:
        sent_code = await client.send_code_request(phone)
        active_clients[user_id] = client
        return True, sent_code.phone_code_hash
    except Exception as e:
        logger.error(f"OTP Request failed per {user_id}: {e}")
        await client.disconnect()
        return False, str(e)

async def sign_in_otp(user_id, phone, code, phone_code_hash, password=None):
    """Completa il login con l'OTP."""
    client = active_clients.get(user_id)
    if not client:
        return False, "Client non avviato per la richiesta OTP."
        
    try:
        await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash, password=password)
        
        # Riapplica l'handler ora che è autenticato
        @client.on(events.NewMessage(incoming=True))
        async def msg_handler(event):
            await _handle_incoming_message(event, user_id)
            
        logger.info(f"🤖 Userbot autenticato per l'utente {user_id}")
        return True, "Login completato con successo."
    except Exception as e:
        logger.error(f"Sign in failed per {user_id}: {e}")
        if "SessionPasswordNeededError" in str(type(e)):
            return False, "E' richiesta la password (2FA)."
        await client.disconnect()
        if user_id in active_clients:
            del active_clients[user_id]
        return False, str(e)

async def stop_all_userbots():
    for uid, client in active_clients.items():
        if client.is_connected():
            await client.disconnect()
    active_clients.clear()

async def auto_start_existing():
    """Avvia al boot tutti i client che hanno un .session esistente."""
    if not USERBOT_ENABLED: return
    for f in os.listdir(USERBOTS_DIR):
        if f.startswith("userbot_") and f.endswith(".session"):
            uid_str = f.replace("userbot_", "").replace(".session", "")
            if uid_str.lstrip('-').isdigit():
                await create_and_start_client(int(uid_str))
