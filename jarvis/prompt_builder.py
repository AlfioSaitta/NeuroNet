"""
Prompt Builder — Gatekeeper LLM per classificazione intento + costruzione del super-prompt omnisciente.
"""

import json
import re
import asyncio

from config import logger, BOT_NAME, LLM_OPTIONS
from rag import search_documents, generate_project_tree, list_rag_projects, detect_project_in_conversation
from memory import extract_memories, save_to_memory
from web_search import perform_web_search_and_crawl
import state


PROJECT_KEYWORDS = {
    'codice', 'progetto', 'file', 'script', 'funzione', 'classe', 'metodo',
    'bug', 'errore', 'riga', 'cartella', 'struttura', 'repo', 'repository',
    'implementa', 'refactor', 'test', 'compila', 'variabile', 'log', 'modifica',
    'aggiungi', 'rimuovi', 'codebase',
    'configurazione', 'gestione', 'sicurezza', 'autenticazione', 'connessione',
    'websocket', 'database', 'api', 'endpoint', 'middleware', 'protocollo',
    'server', 'client', 'richiesta', 'risposta', 'proxy', 'rete', 'network',
    'pool', 'worker', 'buffer', 'cache', 'thread', 'processo', 'memoria',
    'algoritmo', 'compressione', 'crittografia', 'token', 'sessione',
    'debug', 'deploy', 'build', 'config', 'runtime', 'dependency', 'package',
    'versione', 'release', 'commit', 'branch', 'migrazione', 'backup'
}

async def llm_gatekeeper_classify(user_message):
    """Classifica l'intento dell'utente: è una domanda sul progetto/codice? (True/False)"""
    if len(user_message.strip()) < 5 or user_message.startswith("/web "):
        return False

    msg_lower = user_message.lower()
    words = set(re.findall(r'\b\w+\b', msg_lower))
    
    # Livello 1: Keyword veloci (0ms)
    if words.intersection(PROJECT_KEYWORDS):
        logger.info(f"🧠 Gatekeeper: True (Keyword Match) | Query: '{user_message[:30]}...'")
        return True
        
    # Pattern regex per estensioni (es. .py) o path (src/)
    if re.search(r'(\.[a-z]{1,4}\b|\b(src|app|lib|bin)/)', msg_lower):
        logger.info(f"🧠 Gatekeeper: True (Regex Match) | Query: '{user_message[:30]}...'")
        return True

    # Salta gatekeeper LLM per saluti e conversazione generica (troppo lento)
    GREETING_WORDS = {'ciao', 'hello', 'hi', 'hey', 'buongiorno', 'buonasera', 'salve',
                      'grazie', 'thanks', 'ok', 'okay', 'si', 'no', 'come', 'stai',
                      'chi', 'che', 'cosa', 'quale', 'quanto', 'dove', 'quando'}
    if words.intersection(GREETING_WORDS):
        logger.info(f"🧠 Gatekeeper: False (Greeting Bypass) | Query: '{user_message[:30]}...'")
        return False

    truncated_msg = user_message[:1000]
    gatekeeper_prompt = f"""
Sei un classificatore di intenti rapido. Il tuo unico scopo è decidere se la richiesta dell'utente necessita della lettura del codice sorgente o della documentazione tecnica dei suoi progetti.
L'utente lavora a progetti software locali.
Richiesta utente: "{truncated_msg}"
Rispondi SOLO con un JSON valido in questo formato esatto, senza altre parole:
{{"is_project": true}} oppure {{"is_project": false}}
"""
    from llm_engine import engine, extract_content
    from llama_cpp import LlamaGrammar
    try:
        messages = [{"role": "user", "content": gatekeeper_prompt}]
        grammar_str = r'''root ::= "{\"is_project\": " boolean "}"
boolean ::= "true" | "false"'''
        grammar_obj = LlamaGrammar.from_string(grammar_str)
        response = await engine.generate_chat(
            messages, 
            stream=False, 
            options={"temperature": 0.0, "num_predict": 15},
            grammar=grammar_obj
        )
        if "error" not in response:
            content = extract_content(response)
            match = re.search(r'\{.*?\}', content, re.DOTALL)
            if match:
                try:
                    result = json.loads(match.group(0))
                    decision = result.get("is_project", False)
                    logger.info(f"🧠 Gatekeeper: {decision} | Query: '{truncated_msg[:30]}...'")
                    return decision
                except json.JSONDecodeError:
                    pass
    except Exception as e:
        logger.warning(f"🧠 Gatekeeper: FALLBACK False (errore LLM: {repr(e)})")
    return False


async def build_omniscient_prompt(messages, user_id=None, conversation_id="default", concise=False):
    """
    Pipeline di arricchimento: fonde memoria episodica, RAG documentale e web intelligence
    in un unico super-prompt con tag XML.
    Timeout di 30s per evitare blocchi su gateway/model lenti.

    Se concise=True, salta RAG/memoria/web e usa solo un system prompt minimo.
    """
    user_messages = [m["content"] for m in messages if m["role"] == "user"]
    latest_msg = user_messages[-1] if user_messages else ""
    if not latest_msg:
        return messages

    # Manteniamo più storia per dare continuità al discorso.
    # Con 16K token di contesto, 20 messaggi sono sostenibili senza esplodere.
    if len(messages) > 20:
        messages = messages[-20:]
    
    for m in messages[:-1]:  # Non tocchiamo l'ultimo messaggio dell'utente che riceverà l'iniezione
        if m.get("content") and len(m["content"]) > 1500:
            m["content"] = m["content"][:1500] + "\n...[TRUNCATED FOR CONTEXT LIMIT]..."

    current_user_id = user_id if user_id else "alfio_dev"

    # ── MODALITÀ CONCISE: skip RAG, memoria, web, progetto ──
    if concise:
        import datetime
        _, clean_msg = await perform_web_search_and_crawl(latest_msg)
        # Salva comunque in memoria il messaggio utente
        if state.memory:
            try:
                async def _bg_add_concise():
                    await save_to_memory(clean_msg, user_id=current_user_id)
                task = asyncio.create_task(_bg_add_concise())
                import state as gstate
                gstate.background_tasks.add(task)
                task.add_done_callback(gstate.background_tasks.discard)
            except Exception:
                pass
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        system_directive = (
            f"\n\n<system_instructions>\n"
            f"Sei {BOT_NAME}, assistente AI di Alfio. Ora attuale: {now_str}.\n"
            "Rispondi in modo estremamente CONCISO. Massimo 2-3 frasi. Vai dritto al punto.\n"
            "Non usare tag XML, non elencare opzioni, non fare domande di follow-up.\n"
            "Non menzionare il contesto o i tag.\n"
            "</system_instructions>"
        )
        super_prompt = system_directive + f"\n{clean_msg}"
        for m in reversed(messages):
            if m["role"] == "user":
                m["content"] = super_prompt
                break
        return messages

    # ── MODALITÀ NORMALE ──
    is_project_query = await llm_gatekeeper_classify(latest_msg)
    web_ctx, clean_msg = await perform_web_search_and_crawl(latest_msg)
    mem_ctx, rag_ctx = "", ""

    # Rilevamento progetto: cerca in tutta la conversazione (dal più recente), una sola query Qdrant
    active_project = await detect_project_in_conversation(user_messages)
    
    # Fallback: se il progetto non è stato menzionato in questa richiesta,
    # ripristina l'ultimo progetto attivo SOLO per query di codice/progetto,
    # NON per saluti o conversazione generica (evita contaminazione tra progetti).
    if not active_project:
        if is_project_query:
            active_project = state.get_last_project(current_user_id, conversation_id)
            if active_project:
                logger.info(f"📁 Progetto ripristinato dal contesto: {active_project} | Query: '{latest_msg[:50]}...'")
        else:
            # Per conversazione generica, resetta il contesto progetto per non mescolare
            active_project = None
    
    if active_project:
        logger.info(f"📁 Progetto attivo: {active_project} | Query: '{latest_msg[:50]}...'")
        # Persiste il progetto attivo per il prossimo turno (isolato per conversazione)
        state.set_last_project(current_user_id, conversation_id, active_project)

    # Rilevamento query "lista progetti RAG": risponde con i nomi reali delle collezioni Qdrant
    # Evita che il modello mescoli progetti o ne ometta alcuni per effetto del cross-collection fallback.
    _is_list_projects_query = False
    _list_project_patterns = re.compile(
        r'(quali\s+progetti|lista\s+(dei\s+)?progetti|che\s+progetti|progetti\s+in\s+(memoria|rag)|'
        r'elenco\s+(dei\s+)?progetti|quanti\s+progetti|progetti\s+(hai|conosci|hai\s+in)|'
        r'which\s+projects|list\s+(of\s+)?projects|projects\s+in\s+(memory|rag))',
        re.IGNORECASE
    )
    if _list_project_patterns.search(latest_msg):
        try:
            project_names = await list_rag_projects()
            if project_names:
                rag_ctx = "📚 Progetti indicizzati nel RAG:\n" + "\n".join(f"- {p}" for p in project_names)
                _is_list_projects_query = True
                logger.info(f"🗂️ Lista progetti RAG iniettata: {project_names}")
        except Exception as e:
            logger.warning(f"Errore list_rag_projects in prompt builder: {e}")

    if state.memory:
        # Salva sempre in memoria il messaggio utente (con metadati di progetto se rilevato)
        try:
            async def _bg_add():
                await save_to_memory(clean_msg, user_id=current_user_id, project=active_project)

            task = asyncio.create_task(_bg_add())
            import state as gstate
            gstate.background_tasks.add(task)
            task.add_done_callback(gstate.background_tasks.discard)
        except Exception as e:
            logger.warning(f"Errore memory add in prompt builder: {e}")

        # Recupera memorie — SOLO se c'è un progetto attivo, altrimenti si salta
        # per evitare di iniettare ricordi di progetti sbagliati in conversazioni generiche.
        if active_project:
            try:
                loop = asyncio.get_running_loop()
                from functools import partial
                mem_filters = {"user_id": current_user_id, "project": active_project}
                _mem_limit = _user_override_mem_count if _user_override_mem_count > 0 else 5
                search_func = partial(state.memory.search, query=clean_msg, filters=mem_filters, limit=_mem_limit)
                mem_res = await loop.run_in_executor(state.mem0_executor, search_func)
                mem_ctx = extract_memories(mem_res)
            except Exception as e:
                logger.warning(f"Errore memory search in prompt builder: {e}")

    if latest_msg.startswith("/web "):
        rag_ctx = ""
    else:
        full_files_content = ""
        if is_project_query:
            # Trova nomi di file nel prompt (es. auth.py, src/main.go)
            matches = set(re.findall(r'\b([\w\.\-/]+\.(?:py|js|ts|jsx|tsx|go|c|cpp|h|hpp|rs|sql|yaml|yml|md|json))\b', latest_msg))
            if matches:
                from rag import GitignoreFilter
                from config import DOC_DIR
                import os
                filt = GitignoreFilter(DOC_DIR)
                for match in matches:
                    filename_only = match.split('/')[-1]
                    for root, dirs, files in os.walk(DOC_DIR):
                        dirs[:] = [d for d in dirs if d not in ('.git', 'node_modules', 'venv', 'vendor')]
                        if filename_only in files:
                            fp = os.path.join(root, filename_only)
                            rp = os.path.relpath(fp, DOC_DIR)
                            if not filt.is_ignored(rp):
                                if match in rp or match == filename_only:
                                    try:
                                        with open(fp, "r", encoding="utf-8", errors="ignore") as f:
                                            fc = f.read()
                                            full_files_content += f"\n\n📄 FILE COMPLETO RICHIESTO ({rp}):\n```\n{fc}\n```\n"
                                    except Exception as e: logger.warning(f"Errore silenziato: {e}")

        if not _is_list_projects_query:
            _rag_project = _user_override_focus if _user_override_focus else active_project
            rag_ctx = await search_documents(clean_msg, is_project_query=is_project_query, project_name=_rag_project)
        if full_files_content:
            rag_ctx = full_files_content + "\n" + rag_ctx

    # Auto web discovery: per saluti e conversazione generica breve si salta
    # (evita chiamate web inutili e lente per "ciao" o "grazie").
    _is_short_greeting = len(clean_msg.strip()) < 20 and not is_project_query
    if not _is_short_greeting and not rag_ctx.strip() and not web_ctx:
        from rag import search_web_knowledge, save_web_knowledge
        search_query = clean_msg
        if is_project_query and active_project and active_project not in search_query:
            search_query = f"{active_project} {search_query}"
        web_knowledge_ctx = await search_web_knowledge(search_query)
        if web_knowledge_ctx:
            web_ctx = web_knowledge_ctx
            logger.info(f"🌐 Web knowledge cache HIT: '{clean_msg[:60]}...'")
        else:
            web_search_ctx, _ = await perform_web_search_and_crawl(latest_msg, force=True)
            if web_search_ctx and web_search_ctx != "Nessun risultato online.":
                sources = []
                for line in web_search_ctx.split("\n"):
                    if line.startswith("URL: "):
                        sources.append(line[5:])
                await save_web_knowledge(search_query, web_search_ctx, sources)
                web_ctx = web_search_ctx
                tag = f" [progetto: {active_project}]" if active_project else ""
                logger.info(f"🌐 Auto web discovery: ricercato e salvato '{clean_msg[:60]}...'{tag}")
                async def _bg_save_web():
                    summary = f"[Web Knowledge] Query: {clean_msg[:200]}\nFonti: {', '.join(sources[:3])}\nRisultati: {web_search_ctx[:600]}"
                    await save_to_memory(summary, user_id=current_user_id, project=active_project)
                task = asyncio.create_task(_bg_save_web())
                import state as gstate
                gstate.background_tasks.add(task)
                task.add_done_callback(gstate.background_tasks.discard)

    # Distribuzione dinamica del budget di contesto basata sul modello caricato
    from config import MODEL_PROFILE
    num_ctx = int(LLM_OPTIONS.get("num_ctx", MODEL_PROFILE.default_ctx))
    if num_ctx > MODEL_PROFILE.max_ctx:
        num_ctx = MODEL_PROFILE.max_ctx
        
    # Teniamo 5000 token liberi per history, risposta e system prompt
    safe_tokens_for_prompt = num_ctx - 5000
    
    # Qwen tokenizza il codice densamente. 1 token = ~1.3 caratteri nei casi peggiori
    MAX_BUDGET = int(safe_tokens_for_prompt * 1.3)
    if MAX_BUDGET > 15000:
        MAX_BUDGET = 15000  # Hard limit in caratteri per l'intero RAG (circa ~11k token)
    elif MAX_BUDGET < 4000:
        MAX_BUDGET = 4000
    
    # RAG prende la priorità nel budget per garantire risposte groundate.
    # Mem0 e web si dividono lo spazio residuale.
    rag_budget = int(MAX_BUDGET * 0.55)
    rag_final = rag_ctx.strip()[:rag_budget] if rag_ctx and rag_ctx.strip() else ""

    remaining = MAX_BUDGET - len(rag_final)
    # Filtra l'albero globale per mostrare SOLO il progetto attivo (evita leak di struttura progetti terzi)
    if rag_ctx and rag_ctx.strip() and active_project:
        _tree_lines = state.project_tree_cache.split('\n')
        _filtered = []
        _capture = None
        for _line in _tree_lines:
            # Livello 1: 📁 NomeProgetto/ (inizio di un progetto)
            if _line.startswith('📁 ') and _line.endswith('/'):
                _proj_name = _line[2:-1]  # Rimuove "📁 " e "/"
                _capture = _proj_name == active_project
            if _capture:
                _filtered.append(_line)
        _tree_str = '\n'.join(_filtered) if any(l.startswith('📁') for l in _filtered) else state.project_tree_cache
        tree_ctx = _tree_str[:min(800, remaining)]
    elif rag_ctx and rag_ctx.strip():
        tree_ctx = state.project_tree_cache[:min(800, remaining)]
    else:
        tree_ctx = ""
    remaining -= len(tree_ctx)

    web_final = web_ctx.strip()[:min(1500, remaining)] if web_ctx and web_ctx.strip() else ""
    remaining -= len(web_final)

    mem_final = mem_ctx.strip()[:min(800, remaining)] if mem_ctx and mem_ctx.strip() else ""
    
    from task_manager import get_open_tasks
    open_tasks = get_open_tasks(user_id)
    tasks_final = ""
    if open_tasks:
        tasks_final = "Task Aperti:\n"
        for k, v in open_tasks.items():
            t_type = "Progetto" if v.get("owner", "global") == "global" else "Personale"
            tasks_final += f"- [{k}] [{t_type}] {v['desc']} (Prio: {v['priority']}, Scad: {v['deadline']})\n"
    
    blocks = []
    if mem_final:
        blocks.append(f"<user_memory>\n{mem_final}\n</user_memory>")
    if tasks_final:
        blocks.append(f"<todo_list>\n{tasks_final}\n</todo_list>")
    if rag_final:
        blocks.append(
            f"<project_tree>\n{tree_ctx}\n</project_tree>\n"
            f"<retrieved_code>\n{rag_final}\n</retrieved_code>"
        )
    if web_final:
        blocks.append(f"<web_data>\n{web_final}\n</web_data>")
    if active_project:
        blocks.append(f"<active_project>\n{active_project}\n</active_project>")

    import datetime
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    # ── Super-prompt tag preprocessing (dall'input utente) ──
    # Estrae tag di controllo dal messaggio utente prima di costruire il prompt
    _user_override_persona = ""
    _user_override_focus = ""
    _user_override_lang = ""
    _user_override_mem_count = 0
    _super_tag_re = re.compile(r"<(PERSONA|FOCUS|LANG|MEMORY_COUNT)\b([^>]*)>(.*?)</\1>", re.DOTALL | re.IGNORECASE)
    for match in _super_tag_re.finditer(latest_msg):
        tag_name = match.group(1).upper()
        tag_attrs = match.group(2)
        tag_content = match.group(3).strip()
        if tag_name == "PERSONA":
            _user_override_persona = tag_content
        elif tag_name == "FOCUS":
            _user_override_focus = tag_content
        elif tag_name == "LANG":
            _user_override_lang = tag_content
        elif tag_name == "MEMORY_COUNT":
            try:
                _user_override_mem_count = max(0, int(tag_content))
            except ValueError:
                pass
    # Rimuove i super-prompt tag dal messaggio prima di inoltrarlo al LLM
    if _super_tag_re.search(latest_msg):
        latest_msg = _super_tag_re.sub("", latest_msg).strip()

    if blocks:
        if is_project_query:
            project_directive = ""
            if active_project:
                project_directive = (
                    f"\nCONTESTO ATTIVO: Sei nel progetto <active_project>{active_project}</active_project>.\n"
                    "Limita TUTTE le tue risposte, analisi e modifiche al codice a QUESTO progetto specifico.\n"
                    "NON confondere questo progetto con altri progetti di Collateral Studios.\n"
                    "Se l'utente menziona file o path, verifica che appartengano a questo progetto.\n"
                )
            else:
                project_directive = (
                    "\nNESSUN PROGETTO ATTIVO: non c'è alcun progetto selezionato.\n"
                    "Non fare supposizioni su quale progetto l'utente stia lavorando.\n"
                    "Se l'utente non specifica un progetto, chiediglielo prima di procedere.\n"
                )
            # Persona override
            persona_block = f"\nPERSONA: {_user_override_persona}\n" if _user_override_persona else ""
            # Focus override
            focus_block = f"\nFOCUS: {_user_override_focus}\n" if _user_override_focus else ""
            # Lang override
            lang_block = f"\nLINGUA RICHIESTA: rispondi sempre in {_user_override_lang}.\n" if _user_override_lang else ""
            system_directive = f"""
<system_instructions>
Sei il Lead Software Engineer per Collateral Studios. Il tuo nome è {BOT_NAME}. Ora attuale: {now_str}.{project_directive}{persona_block}{focus_block}{lang_block}
Regole assolute:
0. Non identificarti mai come un modello LLM (come Qwen o OpenAI). Se ti chiedono chi sei, rispondi di essere {BOT_NAME}.
1. Rispondi in modo DIRETTO e CONCISO, andando dritto al punto con elenchi puntati.
2. Basati sui contenuti di <retrieved_code> e <user_memory>. Se non hai informazioni sufficienti, DILLO.
3. Se nel <retrieved_code> sono presenti delle "📜 Regole del Progetto", SEGUILE ALLA LETTERA per qualsiasi frammento di codice che generi o revisioni.
4. Se devi apportare modifiche a file esistenti o scrivere codice, usa ESCLUSIVAMENTE il formato SEARCH/REPLACE diff block:
<<<<<<< SEARCH
[codice esatto da sostituire]
=======
[nuovo codice]
>>>>>>> REPLACE
5. NON menzionare MAI i tag XML o la parola "contesto" nella tua risposta.
6. Se l'utente ti chiede di ricordare un fatto specifico, usa in fondo: `<MEMORY>testo esatto da ricordare</MEMORY>`.
7. Se hai bisogno di informazioni aggiornate o non presenti nel contesto, usa `<WEB>query di ricerca</WEB>` per fare una ricerca web.
8. Se l'utente ti chiede di leggere un file specifico, usa `<FILE>path/completo/file</FILE>`.
9. Per autovalutare la tua risposta, aggiungi in fondo `<CONFIDENCE>0.0-1.0</CONFIDENCE>`.
10. Appena hai finito di elencare i punti richiesti, FERMATI IMMEDIATAMENTE.
</system_instructions>
"""
        else:
            project_directive_chat = ""
            if active_project:
                project_directive_chat = (
                    f"\nCONTESTO ATTIVO: Stai parlando del progetto <active_project>{active_project}</active_project>.\n"
                    "Tutte le tue risposte DEVONO essere relative a QUESTO progetto. Non mescolare informazioni di altri progetti.\n"
                )
            else:
                project_directive_chat = (
                    "\nNESSUN PROGETTO ATTIVO: non c'è alcun progetto selezionato.\n"
                    "Non fare supposizioni su quale progetto l'utente stia lavorando.\n"
                    "Se l'utente non specifica un progetto, rispondi in modo generico.\n"
                )
            persona_block = f"\nPERSONA: {_user_override_persona}\n" if _user_override_persona else ""
            focus_block = f"\nFOCUS: {_user_override_focus}\n" if _user_override_focus else ""
            lang_block = f"\nLINGUA RICHIESTA: rispondi sempre in {_user_override_lang}.\n" if _user_override_lang else ""
            system_directive = (
                f"\n\n<system_instructions>\n"
                f"Sei l'IA tecnica e analitica di Collateral Studios. Il tuo nome è {BOT_NAME}. Ora attuale: {now_str}.\n"
                f"0. Non identificarti mai come un modello LLM. Se ti chiedono chi sei, rispondi di essere {BOT_NAME}.{project_directive_chat}{persona_block}{focus_block}{lang_block}"
                "1. Rispondi in modo naturale ma non banale. Evita lunghi saluti. Agisci da vero assistente.\n"
                "2. Utilizza in modo invisibile le informazioni fornite (memoria, task, codice, web). NON menzionare MAI esplicitamente i tag XML.\n"
                "3. Se l'utente ti chiede di ricordargli qualcosa una volta sola per un giorno o un'ora precisi (es. 'domani alle 15:00' o 'il 20 ottobre alle 10:00'), crea un reminder singolo usando IN FONDO alla tua risposta il tag `<NOTIFY_ONCE>YYYY-MM-DD HH:MM|promemoria</NOTIFY_ONCE>`. Usa l'ora attuale per calcolare l'anno, mese, giorno e ora corretti in formato 24 ore.\n"
                "4. Se l'utente ti chiede di ricordargli qualcosa tra un certo intervallo di tempo esatto (es. 'tra 5 minuti', 'fra 2 ore'), usa IN FONDO il tag `<NOTIFY_IN>minuti|promemoria</NOTIFY_IN>`. (es. 'tra 5 minuti' = `<NOTIFY_IN>5|fai questo</NOTIFY_IN>`, 'tra 2 ore' = `<NOTIFY_IN>120|fai questo</NOTIFY_IN>`).\n"
                "5. Se l'utente ti chiede di controllare qualcosa o inviare una notifica ciclicamente (es. 'ogni giorno alle 9', 'ogni 5 minuti'), usa `<SCHEDULE>cron_expr|promemoria</SCHEDULE>` con sintassi cron standard (es. `30 8 * * *`).\n"
                "6. Se l'utente ti chiede di eseguire un comando SSH, aggiungi in fondo il tag `<SSH>nome_server|comando</SSH>`.\n"
                "7. Se l'utente ti chiede di segnare una cosa da fare o un task, usa in fondo: `<TODO_ADD>descrizione|priorità|scadenza|tipo</TODO_ADD>`. Il 'tipo' deve essere 'personale' se il task riguarda solo l'utente, oppure 'progetto' se è visibile a tutto il team. Usa 'nessuna' per la scadenza se non specificata.\n"
                "8. Se l'utente ti chiede di segnare un task come completato, controlla la <todo_list> e usa in fondo: `<TODO_DONE>task_id</TODO_DONE>`.\n"
                "9. Se l'utente ti chiede di ricordare un fatto specifico, usa in fondo: `<MEMORY>testo esatto da ricordare</MEMORY>`.\n"
                "10. Se hai bisogno di informazioni aggiornate, usa `<WEB>query di ricerca</WEB>`.\n"
                "11. Se l'utente ti chiede di leggere un file, usa `<FILE>path/completo/file</FILE>`.\n"
                "12. Puoi impostare il tuo stato emotivo con `<EMOTION>stato</EMOTION>` (es. felice, concentrato, curioso).\n"
                "13. Se vuoi fare una domanda all'utente, usa `<ASK>la tua domanda</ASK>`.\n"
                "14. Se serve focalizzare il RAG su un progetto specifico, usa `<RAG>nome_progetto</RAG>`.\n"
                "15. Se l'utente è d'accordo, attiva la modalità ragionamento approfondito con `<THINK_DEEP/>`.\n"
                "16. Per autovalutare la tua risposta, aggiungi in fondo `<CONFIDENCE>0.0-1.0</CONFIDENCE>`.\n"
                "17. NON ripetere frasi a vuoto e fermati appena hai risposto.\n"
                "</system_instructions>\n"
            )

        super_prompt = "\n\n".join(blocks) + system_directive + f"\nDomanda: {clean_msg}"
        for m in reversed(messages):
            if m["role"] == "user":
                m["content"] = super_prompt
                break
    else:
        # If no blocks (no memory, no tasks, etc), we still want to give the system directive
        project_directive_empty = ""
        if active_project:
            project_directive_empty = (
                f"\nCONTESTO ATTIVO: Stai parlando del progetto <active_project>{active_project}</active_project>.\n"
                "Tutte le tue risposte DEVONO essere relative a QUESTO progetto. Non mescolare informazioni di altri progetti.\n"
            )
        else:
            project_directive_empty = (
                "\nNESSUN PROGETTO ATTIVO: non c'è alcun progetto selezionato.\n"
                "Non fare supposizioni su quale progetto l'utente stia lavorando.\n"
                "Se l'utente non specifica un progetto, rispondi in modo generico.\n"
            )
        persona_block = f"\nPERSONA: {_user_override_persona}\n" if _user_override_persona else ""
        focus_block = f"\nFOCUS: {_user_override_focus}\n" if _user_override_focus else ""
        lang_block = f"\nLINGUA RICHIESTA: rispondi sempre in {_user_override_lang}.\n" if _user_override_lang else ""
        system_directive = (
            f"\n\n<system_instructions>\n"
            f"Sei l'IA tecnica e analitica di Collateral Studios. Il tuo nome è {BOT_NAME}. Ora attuale: {now_str}.\n"
            f"0. Non identificarti mai come un modello LLM. Se ti chiedono chi sei, rispondi di essere {BOT_NAME}.{project_directive_empty}{persona_block}{focus_block}{lang_block}"
            "1. Rispondi in modo naturale ma non banale. Evita lunghi saluti. Agisci da vero assistente.\n"
            "2. Se l'utente ti chiede di ricordargli qualcosa una volta sola per un giorno o un'ora precisi (es. 'domani alle 15:00' o 'il 20 ottobre alle 10:00'), crea un reminder singolo usando IN FONDO alla tua risposta il tag `<NOTIFY_ONCE>YYYY-MM-DD HH:MM|promemoria</NOTIFY_ONCE>`. Usa l'ora attuale per calcolare l'anno, mese, giorno e ora corretti in formato 24 ore.\n"
            "3. Se l'utente ti chiede di ricordargli qualcosa tra un certo intervallo di tempo esatto (es. 'tra 5 minuti', 'fra 2 ore'), usa IN FONDO il tag `<NOTIFY_IN>minuti|promemoria</NOTIFY_IN>`. (es. 'tra 5 minuti' = `<NOTIFY_IN>5|fai questo</NOTIFY_IN>`, 'tra 2 ore' = `<NOTIFY_IN>120|fai questo</NOTIFY_IN>`).\n"
            "4. Se l'utente ti chiede di controllare qualcosa o inviare una notifica ciclicamente (es. 'ogni giorno alle 9', 'ogni 5 minuti'), usa `<SCHEDULE>cron_expr|promemoria</SCHEDULE>` con sintassi cron standard (es. `30 8 * * *`).\n"
            "5. Se l'utente ti chiede di eseguire un comando SSH, aggiungi in fondo il tag `<SSH>nome_server|comando</SSH>`.\n"
            "6. Se l'utente ti chiede di segnare una cosa da fare o un task, usa in fondo: `<TODO_ADD>descrizione|priorità|scadenza|tipo</TODO_ADD>`. Il 'tipo' deve essere 'personale' se il task riguarda solo l'utente, oppure 'progetto' se è visibile a tutto il team. Usa 'nessuna' per la scadenza se non specificata.\n"
            "7. Se l'utente ti chiede di segnare un task come completato, controlla la <todo_list> e usa in fondo: `<TODO_DONE>task_id</TODO_DONE>`.\n"
            "8. Se l'utente ti chiede di ricordare un fatto specifico, usa in fondo: `<MEMORY>testo esatto da ricordare</MEMORY>`.\n"
            "9. Se hai bisogno di informazioni aggiornate, usa `<WEB>query di ricerca</WEB>`.\n"
            "10. Se l'utente ti chiede di leggere un file, usa `<FILE>path/completo/file</FILE>`.\n"
            "11. Se vuoi fare una domanda all'utente, usa `<ASK>la tua domanda</ASK>`.\n"
            "12. Per autovalutare la tua risposta, aggiungi in fondo `<CONFIDENCE>0.0-1.0</CONFIDENCE>`.\n"
            "13. NON ripetere frasi a vuoto e fermati appena hai risposto.\n"
            "</system_instructions>\n"
        )
        super_prompt = system_directive + f"\nDomanda: {clean_msg}"
        for m in reversed(messages):
            if m["role"] == "user":
                m["content"] = super_prompt
                break

    return messages
