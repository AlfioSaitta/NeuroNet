"""
Rilevamento hardware del server (identità di sistema per il prompt LLM).

Eseguito all'avvio (core/lifecycle.py) via comandi di sistema:
  - GPU + VRAM + driver   → nvidia-smi
  - CPU model + threads    → /proc/cpuinfo + os.cpu_count()
  - RAM totale/disponibile → /proc/meminfo

Usa SOLO stdlib (nessun import da core.config / llama_cpp) per essere
testabile standalone e senza catena di import pesante.
"""

import logging
import os
import re
import socket
import subprocess

logger = logging.getLogger(__name__)

_HW_CACHE: dict[str, str] | None = None


# ── Probe helper ──────────────────────────────────────────────────────

def _run(cmd: list[str], timeout: float = 5.0) -> str:
    """Esegue un comando di sistema, ritorna stdout pulito o ''."""
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
        )
        return (proc.stdout or "").strip()
    except Exception as e:
        logger.warning(f"Comando fallito ({cmd[0]}): {e}")
        return ""


def _detect_gpu() -> str:
    """GPU + VRAM + driver via nvidia-smi. Fallback CPU-only."""
    out = _run([
        "nvidia-smi",
        "--query-gpu=name,memory.total,memory.free,driver_version",
        "--format=csv,noheader,nounits",
    ])
    if not out:
        return "non rilevata (CPU-only)"
    parts = [p.strip() for p in out.split(",")]
    if len(parts) >= 4:
        name, vram_total, vram_free, driver = parts[0], parts[1], parts[2], parts[3]
        return f"{name} — {vram_total} MiB VRAM ({vram_free} MiB liberi), driver {driver}"
    return out


def _detect_cpu() -> str:
    """CPU model da /proc/cpuinfo + conteggio threads."""
    model = "n/d"
    try:
        with open("/proc/cpuinfo", "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith("model name"):
                    model = line.split(":", 1)[1].strip()
                    break
    except Exception as e:
        logger.warning(f"cpuinfo non leggibile: {e}")
    threads = os.cpu_count() or 0
    return f"{model} — {threads} threads" if threads else model


def _detect_ram() -> str:
    """RAM totale/disponibile da /proc/meminfo (kB → GiB)."""
    total_kb = avail_kb = 0
    try:
        with open("/proc/meminfo", "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                m = re.match(r"MemTotal:\s+(\d+) kB", line)
                if m:
                    total_kb = int(m.group(1))
                m = re.match(r"MemAvailable:\s+(\d+) kB", line)
                if m:
                    avail_kb = int(m.group(1))
    except Exception as e:
        logger.warning(f"meminfo non leggibile: {e}")
        try:
            total_kb = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") // 1024
        except Exception:
            return "n/d"
    if not total_kb:
        return "n/d"
    total_gb = total_kb / (1024 * 1024)
    avail_gb = avail_kb / (1024 * 1024) if avail_kb else 0.0
    if avail_gb:
        return f"{total_gb:.1f} GiB totali, {avail_gb:.1f} GiB disponibili"
    return f"{total_gb:.1f} GiB totali"


# ── Detection & cache ────────────────────────────────────────────────

def detect_hardware() -> dict[str, str]:
    """Esegue il rilevamento reale. Non lancia mai eccezioni."""
    global _HW_CACHE
    if _HW_CACHE is not None:
        return _HW_CACHE
    try:
        _HW_CACHE = {
            "hostname": socket.gethostname(),
            "gpu": _detect_gpu(),
            "cpu": _detect_cpu(),
            "ram": _detect_ram(),
        }
    except Exception as e:
        logger.warning(f"Rilevamento hardware fallito: {e}")
        _HW_CACHE = {
            "hostname": "n/d",
            "gpu": "non rilevata",
            "cpu": "n/d",
            "ram": "n/d",
        }
    return _HW_CACHE


def get_hardware_info() -> dict[str, str]:
    """Ritorna la cache (rileva lazy se non ancora eseguito)."""
    if _HW_CACHE is None:
        return detect_hardware()
    return _HW_CACHE


def get_hardware_block() -> str:
    """Blocco formattato per il system prompt. '' se nessun dato utile."""
    try:
        info = get_hardware_info()
    except Exception as e:
        logger.warning(f"get_hardware_block fallito (non critico): {e}")
        return ""
    if not info or info.get("gpu") in (None, "non rilevata", "") and info.get("hostname") in (None, "n/d"):
        return ""
    return (
        "[HARDWARE IDENTITY — REAL hardware of the Jarvis server]\n"
        f"- Hostname: {info.get('hostname', 'n/d')}\n"
        f"- GPU: {info.get('gpu', 'n/d')}\n"
        f"- CPU: {info.get('cpu', 'n/d')}\n"
        f"- RAM: {info.get('ram', 'n/d')}\n"
        "\n"
        "If the user asks about your hardware, models, or setup, answer using "
        "THESE real values above. Never invent, never deflect."
    )
