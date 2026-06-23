import os
import json
import logging
import asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from datetime import datetime, timedelta
import pytz
import state
from config import logger, OLLAMA_MODEL, LLM_OPTIONS, OLLAMA_BASE, GLOBAL_KEEP_ALIVE

CRON_FILE = os.path.join(os.path.dirname(__file__), "cron_jobs.json")
scheduler = AsyncIOScheduler(timezone=pytz.utc)

def load_jobs():
    if os.path.exists(CRON_FILE):
        try:
            with open(CRON_FILE, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading cron jobs: {e}")
    return {}

def save_jobs(jobs):
    try:
        import os
        tmp = CRON_FILE + ".tmp"
        with open(tmp, 'w') as f:
            json.dump(jobs, f, indent=4)
        os.replace(tmp, CRON_FILE)
    except Exception as e:
        logger.error(f"Error saving cron jobs: {e}")

async def execute_cron_job(job_id, prompt, chat_id):
    logger.info(f"Executing cron job {job_id}: {prompt}")
    from prompt_builder import build_omniscient_prompt
    
    # Costruisce la conversazione fittizia per far scattare RAG e ricerca
    cron_instruction = (
        f"[SISTEMA: Esecuzione Task Schedulato]\n"
        f"È il momento di eseguire o notificare il seguente compito schedulato dall'utente:\n"
        f"Obiettivo/Promemoria: '{prompt}'\n\n"
        f"Se è una richiesta di azione o ricerca, fornisci il risultato. "
        f"Se è un semplice promemoria, scrivi un messaggio diretto all'utente ricordandogli il compito, senza saluti generici."
    )
    messages = [{"role": "user", "content": cron_instruction}]
    
    try:
        enriched_messages = await build_omniscient_prompt(messages)
        payload = {
            "model": OLLAMA_MODEL,
            "messages": enriched_messages,
            "stream": False,
            "keep_alive": GLOBAL_KEEP_ALIVE,
            "options": LLM_OPTIONS
        }
        async with state.llm_semaphore:
            res = await state.http_client.post(f"{OLLAMA_BASE}/api/chat", json=payload, timeout=300.0)
        res.raise_for_status()
        bot_reply = res.json().get("message", {}).get("content", "Errore nella generazione schedulata.")
        
        # Invio a Telegram
        if state.telegram_app and state.telegram_app.bot:
            await state.telegram_app.bot.send_message(chat_id=chat_id, text=f"🔔 **Notifica Task Schedulato**\n\n{bot_reply}", parse_mode="Markdown")
            
        if job_id.startswith("job_once_"):
            remove_cron_job(job_id)
            
    except Exception as e:
        logger.error(f"Cron Job execution failed: {e}")

def add_cron_job(cron_expr, prompt, chat_id):
    jobs = load_jobs()
    job_id = f"job_{len(jobs)+1}_{int(asyncio.get_event_loop().time())}"
    jobs[job_id] = {"cron": cron_expr, "prompt": prompt, "chat_id": chat_id}
    save_jobs(jobs)
    
    try:
        scheduler.add_job(
            execute_cron_job, 
            CronTrigger.from_crontab(cron_expr, timezone=pytz.utc), 
            id=job_id, 
            args=[job_id, prompt, chat_id]
        )
        return True, job_id
    except Exception as e:
        return False, str(e)

def add_date_job(date_str, prompt, chat_id):
    jobs = load_jobs()
    job_id = f"job_once_{len(jobs)+1}_{int(asyncio.get_event_loop().time())}"
    jobs[job_id] = {"date": date_str, "prompt": prompt, "chat_id": chat_id}
    save_jobs(jobs)
    
    try:
        run_date = datetime.strptime(date_str, "%Y-%m-%d %H:%M")
        run_date = pytz.utc.localize(run_date)
        scheduler.add_job(
            execute_cron_job, 
            DateTrigger(run_date=run_date, timezone=pytz.utc), 
            id=job_id, 
            args=[job_id, prompt, chat_id]
        )
        return True, job_id
    except Exception as e:
        return False, str(e)

def add_relative_job(minutes, prompt, chat_id):
    jobs = load_jobs()
    job_id = f"job_once_{len(jobs)+1}_{int(asyncio.get_event_loop().time())}"
    run_date = datetime.now() + timedelta(minutes=minutes)
    date_str = run_date.strftime("%Y-%m-%d %H:%M")
    
    jobs[job_id] = {"date": date_str, "prompt": prompt, "chat_id": chat_id}
    save_jobs(jobs)
    
    try:
        run_date = pytz.utc.localize(run_date)
        scheduler.add_job(
            execute_cron_job, 
            DateTrigger(run_date=run_date, timezone=pytz.utc), 
            id=job_id, 
            args=[job_id, prompt, chat_id]
        )
        return True, job_id, date_str
    except Exception as e:
        return False, str(e), None

def remove_cron_job(job_id):
    jobs = load_jobs()
    if job_id in jobs:
        del jobs[job_id]
        save_jobs(jobs)
        try:
            scheduler.remove_job(job_id)
        except Exception as e: logger.warning(f"Errore silenziato: {e}")
        return True
    return False

def init_scheduler():
    jobs = load_jobs()
    for jid, data in jobs.items():
        try:
            if "cron" in data:
                scheduler.add_job(
                    execute_cron_job, 
                    CronTrigger.from_crontab(data["cron"], timezone=pytz.utc), 
                    id=jid, 
                    args=[jid, data["prompt"], data["chat_id"]]
                )
            elif "date" in data:
                run_date = datetime.strptime(data["date"], "%Y-%m-%d %H:%M")
                if run_date > datetime.now():
                    run_date = pytz.utc.localize(run_date)
                    scheduler.add_job(
                        execute_cron_job, 
                        DateTrigger(run_date=run_date, timezone=pytz.utc), 
                        id=jid, 
                        args=[jid, data["prompt"], data["chat_id"]]
                    )
        except Exception as e:
            logger.warning(f"Failed to load cron job {jid}: {e}")
            
    # Task di sistema default
    from reflection_agent import nightly_memory_consolidation
    scheduler.add_job(nightly_memory_consolidation, CronTrigger.from_crontab("0 3 * * *", timezone=pytz.utc), id="sys_reflection")
    
    async def morning_recap():
        from task_manager import get_open_tasks
        tasks = get_open_tasks()
        if not tasks:
            return
        msg = "☀️ **Buongiorno! Ecco i task pendenti per oggi:**\n"
        for k, v in tasks.items():
            msg += f"- `{k}`: {v['desc']} (Priorità: {v['priority']}, Scadenza: {v['deadline']})\n"
        
        from config import ADMIN_USERS
        if state.telegram_app and state.telegram_app.bot and ADMIN_USERS:
            await state.telegram_app.bot.send_message(chat_id=ADMIN_USERS[0], text=msg, parse_mode="Markdown")

    scheduler.add_job(morning_recap, CronTrigger.from_crontab("0 9 * * *", timezone=pytz.utc), id="sys_morning_recap")
    
    scheduler.start()
    logger.info("🕒 Cron Scheduler avviato con successo.")
