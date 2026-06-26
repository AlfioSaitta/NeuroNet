---
name: rag-query
description: Ottimizza query RAG per massima pertinenza dei risultati
type: instruction
---

## Cosa fa
Aiuta a formulare query di ricerca RAG efficaci:
- Riscrive la domanda utente in formato ottimale per retrieval vettoriale
- Suggerisce keyword e sinonimi pertinenti
- Identifica il progetto e la collection Qdrant corretta

## Linee guida
1. Estrai i concetti chiave dalla domanda dell'utente
2. Formatta come query composta: "[contesto] [concetto principale] [dettaglio]"
3. Specifica il linguaggio di programmazione se pertinente
4. Per codice: includi il nome della funzione/classe/file se noto

## Esempio
Domanda: "Come gestisco le eccezioni?"
→ Query RAG: "gestione eccezioni try-catch error handling Python best practices"
