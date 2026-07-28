# 📊 Jarvis Performance Benchmarks

**Ultimo aggiornamento:** 2026-07-27 11:04:03 UTC
**Modello:** Qwen3.5-4B-UD-Q4_K_XL (n_gpu_layers=-1, flash_attn=true)
**GPU:** NVIDIA RTX 3050 Ti 4GB — VRAM ~3334/4096 MiB (81%)

## `/v1/chat/completions` (via Qwen3.5-4B direct)

| Scenario | TTFT | Total Time | tok/s | Output tk |
|---|---|---|---|---|
| A — Saluto | 7.4s | 634ms | 23.7 | 15 |
| B — Meta | 8.7s | 8.2s | 3.7 | 30 |
| E — Generale | 15.7s | 11.2s | 7.2 | 80 |
| C — Progetto Sempl | 16.1s | 26.4s | 4.6 | 120 |

---
## Storico Benchmark

| Data | Scenario | Endpoint | TTFT | Total Time | tok/s | Note |
|---|---|---|---|---|---|---|---|
| 2026-07-27 | A — Saluto | /v1 direct | 7.4s | 634ms | 23.7 | Benchmark v2 — /v1 only |
| 2026-07-27 | B — Meta | /v1 direct | 8.7s | 8.2s | 3.7 | Benchmark v2 — /v1 only |
| 2026-07-27 | E — Generale | /v1 direct | 15.7s | 11.2s | 7.2 | Benchmark v2 — /v1 only |
| 2026-07-27 | C — Progetto Sempl | /v1 direct | 16.1s | 26.4s | 4.6 | Benchmark v2 — /v1 only |

---
*Generato da `scripts/benchmark.py`*
