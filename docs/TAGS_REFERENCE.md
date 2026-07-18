# Tag d'Azione XML — Riferimento Completo

I tag XML vengono intercettati dalla risposta del LLM e processati da `tag_processor.py` prima che il testo pulito arrivi all'utente. La visibilità determina se il tag e il suo contenuto vengono rimossi (`hidden`/`action`) o lasciati nel testo (`kept`). I tag `action` generano feedback visibile all'utente.

## Registro Completo (21 tag)

| Tag | Formato | Visibilità | Self-Closing | Descrizione |
|---|---|---|---|---|
| `MEMORY` | `<MEMORY>testo</MEMORY>` | hidden | ❌ | Salva un fatto in memoria episodica (Mem0) |
| `SCHEDULE` | `<SCHEDULE>cron_expr\|promemoria</SCHEDULE>` | action | ❌ | Crea un promemoria schedulato (cron) |
| `NOTIFY_ONCE` | `<NOTIFY_ONCE>YYYY-MM-DD HH:MM\|testo</NOTIFY_ONCE>` | action | ❌ | Promemoria singolo a data fissa |
| `NOTIFYONCE` | `<NOTIFYONCE>...</NOTIFYONCE>` | action | ❌ | Alias per NOTIFY_ONCE (senza underscore) |
| `NOTIFY_IN` | `<NOTIFY_IN>minuti\|testo</NOTIFY_IN>` | action | ❌ | Timer relativo tra N minuti |
| `NOTIFYIN` | `<NOTIFYIN>...</NOTIFYIN>` | action | ❌ | Alias per NOTIFY_IN (senza underscore) |
| `SSH` | `<SSH>server\|comando</SSH>` | action | ❌ | Esecuzione comando SSH su server remoto |
| `TODO_ADD` | `<TODO_ADD>desc\|prio\|scad\|tipo</TODO_ADD>` | action | ❌ | Aggiunge un task alla todo list |
| `TODO_DONE` | `<TODO_DONE>id</TODO_DONE>` | action | ❌ | Segna un task come completato |
| `WEB` | `<WEB>query</WEB>` | action | ❌ | Esegue una ricerca web e include i risultati |
| `FILE` | `<FILE>path/file</FILE>` | action | ❌ | Legge e include contenuto di un file |
| `EMOTION` | `<EMOTION>stato</EMOTION>` | hidden | ❌ | Imposta stato emotivo per l'interfaccia UI |
| `THINK_DEEP` | `<THINK_DEEP/>` | hidden | ✅ | Attiva modalità ragionamento approfondito |
| `CACHE_CLEAR` | `<CACHE_CLEAR/>` | action | ✅ | Resetta la cache semantica |
| `CONFIDENCE` | `<CONFIDENCE>0.95</CONFIDENCE>` | hidden | ❌ | Autovalutazione confidenza della risposta |
| `ASK` | `<ASK>domanda</ASK>` | action | ❌ | Il LLM fa una domanda all'utente (reverse interaction) |
| `RAG` | `<RAG>project_name</RAG>` | action | ❌ | Forza RAG su un progetto specifico |
| `SUMMARY` | `<SUMMARY target="user_id">testo</SUMMARY>` | action | ❌ | Salva un riepilogo nella memoria di un altro utente |
| `BRANCH` | `<BRANCH>project\|branch</BRANCH>` | action | ❌ | Cambia branch git in un progetto |
| `COMMIT` | `<COMMIT>message</COMMIT>` | action | ❌ | Crea un commit git con i cambiamenti locali |
| `EXEC` | `<EXEC>timeout\|comando</EXEC>` | action | ❌ | Esegue un comando shell readonly (whitelist) |

## TagSafeStream — Anti-Leak in Streaming

Nello streaming, i tag che si estendono su più chunk vengono gestiti da `TagSafeStream` (stato `_in_tag`/`_sc_pending`), che trattiene il contenuto in buffer fino al completamento del tag. A fine stream, `process_response_tags()` elabora il testo completo (con tag) per gli effetti collaterali (memoria, scheduling, notifiche).

## Estendibilità

Nuovi tag possono essere registrati a runtime via `register_tag(TagDef)`.
