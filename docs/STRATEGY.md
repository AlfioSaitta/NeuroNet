# Strategia Integrazione Provider Esterni

## Stato Attuale
Jarvis è **100% locale** — nessuna dipendenza da provider cloud. L'unica eccezione è l'offloading GPU verso un Worker sulla VPN Tailscale.

## Perché Integrare Provider Esterni?

| Scenario | Problema Locale | Soluzione Esterna |
|---|---|---|
| **Conoscenza enciclopedica** | Modello 4B non sa tutto | Gemini 2.5 Pro ha conoscenza aggiornata |
| **Multimodalità** | Nessun supporto immagini | Gemini accetta immagini, audio, video |
| **Contesto lunghissimo** | Max 32K token (locale) | Gemini 1M+ token |
| **Code review incrociata** | Unico punto di vista | Confronto con modello diverso |
| **Fallback disponibilità** | Worker GPU offline = CPU lenta | Cloud sempre disponibile |
| **Traduzioni multilingua** | Qualità variabile | Gemini eccelle in multilingua |

## Architettura Proposta: Provider Router

```
┌─────────────────────────────────────────────────────────────┐
│  ProviderRouter                                              │
│                                                              │
│  Strategy:                                                   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  1. Locale (llama-cpp-python) ← PRIORITARIO          │   │
│  │     - Sempre disponibile, zero latenza di rete        │   │
│  │     - Privacy assoluta                                │   │
│  │                                                       │   │
│  │  2. Worker GPU (EXTERNAL_GPU_URL)                     │   │
│  │     - Accelerazione GPU remota                        │   │
│  │     - Failover automatico (1.5s ping)                 │   │
│  │                                                       │   │
│  │  3. Gemini API (cloud)                                │   │
│  │     - Fallback per conoscenza mancante                │   │
│  │     - Richieste multimodali (immagini)                │   │
│  │     - Contesto lunghissimo (>32K)                     │   │
│  │     - Routing selettivo per specifiche task            │   │
│  └──────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

## Implementazione Suggerita

**Nuovi file:**
- `jarvis/external_providers.py` — Classe base astratta `BaseProvider` + `ProviderRouter`
- `jarvis/gemini_provider.py` — Implementazione Google Gemini via `google-generativeai`

**Modifiche a file esistenti:**
- `jarvis/config.py` — Variabili `GEMINI_API_KEY`, `GEMINI_MODEL`, `EXTERNAL_PROVIDER_STRATEGY`
- `jarvis/llm_engine.py` — Integrazione `ProviderRouter` nel flusso `generate_chat`
- `jarvis/prompt_builder.py` — Routing selettivo per web knowledge / classificazione
- `.env.example` — Aggiunta variabili Gemini

## Strategie di Routing

1. `fallback_only` — Usa provider esterno solo se locale fallisce
2. `selective` — Routing basato su tipo richiesta (es. web knowledge → Gemini)
3. `parallel` — Chiama entrambi, sceglie il meglio (lento, alta qualità)
4. `multimodal` — Solo per richieste con immagini/allegati

## Considerazioni Privacy

- Mai inviare codice proprietario a provider cloud
- Solo richieste di conoscenza generale / web research
- Opzione `PRIVACY_MODE=strict` per bloccare routing esterno su codice

## Piano di Integrazione

| Fase | Task | Priorità |
|---|---|---|
| **1** | Aggiungere `GEMINI_API_KEY` a `.env.example` e `config.py` | Alta |
| **2** | Creare `external_providers.py` con `BaseProvider` + `ProviderRouter` | Alta |
| **3** | Creare `gemini_provider.py` con wrapper Google Generative AI | Alta |
| **4** | Integrare `ProviderRouter` in `llm_engine.py` (fallback + selective) | Alta |
| **5** | Aggiungere routing selettivo in `prompt_builder.py` (web knowledge) | Media |
| **6** | Aggiungere supporto multimodale (immagini in input) | Media |
| **7** | Documentare strategia e privacy nella configurazione | Bassa |
