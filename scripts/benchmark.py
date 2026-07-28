#!/usr/bin/env python3
"""Benchmark Jarvis /v1/chat/completions — TTFT + tok/s.

Usage:
    python scripts/benchmark.py [runs=1]

Outputs to stdout + docs/BENCHMARKS.md.
"""

import asyncio, time, statistics, sys, httpx
from datetime import datetime, UTC

BASE_URL = "http://localhost:8000"
MODEL = "Qwen3.5-4B-UD-Q4_K_XL"

SCENARIOS = [
    ("A — Saluto",         "Ciao",                           15),
    ("B — Meta",           "Quali progetti hai?",            30),
    ("E — Generale",       "Cosa sono le reti neurali?",     80),
    ("C — Progetto Sempl", "Cosa fa il file main.py?",      120),
]


def fmt_time(s: float):
    return f"{s*1000:.0f}ms" if s < 1 else f"{s:.1f}s"


async def run_v1(msg: str, max_tk: int, stream: bool, timeout_s: int = 180):
    """(elapsed, tokens_or_chunks)"""
    start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as cli:
            if stream:
                chunks = 0
                async with cli.stream("POST", f"{BASE_URL}/v1/chat/completions", json={
                    "model": MODEL, "messages": [{"role": "user", "content": msg}],
                    "max_tokens": max_tk, "stream": True,
                }) as resp:
                    async for line in resp.aiter_lines():
                        if line.startswith("data: ") and line != "data: [DONE]":
                            chunks += 1
                return time.monotonic() - start, chunks
            else:
                r = await cli.post(f"{BASE_URL}/v1/chat/completions", json={
                    "model": MODEL, "messages": [{"role": "user", "content": msg}],
                    "max_tokens": max_tk, "stream": False,
                })
                el = time.monotonic() - start
                d = r.json()
                tk = d.get("usage", {}).get("completion_tokens", 0)
                return el, tk
    except Exception as e:
        return time.monotonic() - start, 0


async def main():
    runs = max(1, int(sys.argv[1]) if len(sys.argv) > 1 else 1)
    now = datetime.now(UTC)
    ds = now.strftime('%Y-%m-%d')

    print("=" * 72)
    print(f"  🔬 Jarvis Benchmark — {now.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"  Model: {MODEL}  |  {runs}x /v1/chat/completions per scenario")
    print("=" * 72)

    rows, hist = [], []

    for name, msg, maxtk in SCENARIOS:
        print(f"\n  ┌─ {name}")
        print(f"  │  \"{msg}\"")

        t_tot, t_tk, t_ttft = [], [], []
        for i in range(runs):
            e, tk = await run_v1(msg, maxtk, stream=False)
            t_tot.append(e); t_tk.append(tk)
            e2, _ = await run_v1(msg, maxtk, stream=True)
            t_ttft.append(e2)

        def a(v): return v[0] if len(v) == 1 else statistics.mean(v)
        tot, tk, ttft = a(t_tot), a(t_tk), a(t_ttft)
        toks = tk / tot if tot > 0 else 0

        rows.append((name, "/v1 direct", ttft, tot, toks, tk))
        hist.append((ds, name, "/v1 direct", ttft, tot, toks, "Benchmark v2 — /v1 only"))

        print(f"  │  TTFT {fmt_time(ttft)}  |  tot {fmt_time(tot)}  |  {toks:.1f} tok/s  |  {tk:.0f} tk")

    # ── Write BENCHMARKS.md ──
    lines = [
        "# 📊 Jarvis Performance Benchmarks",
        "",
        f"**Ultimo aggiornamento:** {now.strftime('%Y-%m-%d %H:%M:%S UTC')}",
        f"**Modello:** {MODEL} (n_gpu_layers=-1, flash_attn=true)",
        f"**GPU:** NVIDIA RTX 3050 Ti 4GB — VRAM ~3334/4096 MiB (81%)",
        "",
        "## `/v1/chat/completions` (via Qwen3.5-4B direct)",
        "",
        "| Scenario | TTFT | Total Time | tok/s | Output tk |",
        "|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(f"| {r[0]} | {fmt_time(r[2])} | {fmt_time(r[3])} | {r[4]:.1f} | {r[5]:.0f} |")

    lines += ["", "---", "## Storico Benchmark", "",
              "| Data | Scenario | Endpoint | TTFT | Total Time | tok/s | Note |",
              "|---|---|---|---|---|---|---|---|"]
    for h in hist:
        lines.append(f"| {h[0]} | {h[1]} | {h[2]} | {fmt_time(h[3])} | {fmt_time(h[4])} | {h[5]:.1f} | {h[6]} |")

    lines += ["", "---", "*Generato da `scripts/benchmark.py`*"]
    with open("/home/alfio/Projects/NeuroNet/docs/BENCHMARKS.md", "w") as f:
        f.write("\n".join(lines) + "\n")

    print(f"\n  ✅ docs/BENCHMARKS.md scritto ({len(lines)} righe)")


if __name__ == "__main__":
    asyncio.run(main())


