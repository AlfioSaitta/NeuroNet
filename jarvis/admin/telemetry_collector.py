"""
telemetry_collector.py — TelemetryCache, GPU/health/Qdrant/Synaptiq background collector
======================================================================================
Estratto da admin/dashboard.py per modularizzazione (H7).
"""

import os
import time
import asyncio
import logging
from dataclasses import dataclass

import core.state as state
from core.config import QDRANT_HOST, SEARXNG_HOST, CRAWL4AI_HOST, CRAWL4AI_API_TOKEN, SYNAPTIQ_ENABLED

try:
    from graph.synaptiq_engine import synaptiq_engine
except ImportError:
    synaptiq_engine = None

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# Telemetry Cache (evita subprocess/IO bloccanti a ogni richiesta)
# ═══════════════════════════════════════════════════════════════

@dataclass
class TelemetryCache:
    gpu: dict | None = None
    sys_metrics: dict | None = None
    health: dict | None = None
    qdrant_collections: list | None = None
    sys_stats: dict | None = None  # uptime, load, disk, ram_mb
    synaptiq: dict | None = None   # Synaptiq engine status
    last_gpu_ts: float = 0.0
    last_health_ts: float = 0.0
    last_synaptiq_ts: float = 0.0


_telemetry_cache = TelemetryCache()
_TELEMETRY_POLL_INTERVAL = 5  # secondi


async def _collect_gpu_cache() -> dict | None:
    """Colle metrics GPU via subprocess (offloaded a thread pool) e le cache."""
    import subprocess
    loop = asyncio.get_running_loop()
    result = {"temp": None, "vram_used": None, "vram_total": None, "util": None, "cuda_version": None, "processes": None}
    try:
        out = await loop.run_in_executor(None, lambda: subprocess.run(
            ["nvidia-smi", "--query-gpu=temperature.gpu,memory.used,memory.total,utilization.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5
        ))
        if out.returncode == 0:
            parts = out.stdout.strip().split(", ")
            if len(parts) >= 3:
                result["temp"] = int(parts[0])
                result["vram_used"] = int(parts[1])
                result["vram_total"] = int(parts[2])
            if len(parts) >= 4:
                result["util"] = int(parts[3]) if parts[3].lstrip('-').isdigit() else 0
    except Exception:
        pass

    try:
        out2 = await loop.run_in_executor(None, lambda: subprocess.run(
            ["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3
        ))
        if out2.returncode == 0:
            result["cuda_version"] = out2.stdout.strip()
    except Exception:
        pass

    try:
        out3 = await loop.run_in_executor(None, lambda: subprocess.run(
            ["nvidia-smi", "--query-compute-apps=pid,process_name,used_memory",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3
        ))
        if out3.returncode == 0 and out3.stdout.strip():
            lines = [l.strip() for l in out3.stdout.strip().split('\n') if l.strip()]
            header = f"{'PID':>7}  {'NAME':<30}  {'VRAM':>8}\n" + "-" * 50
            rows = []
            for l in lines:
                parts = l.split(", ")
                if len(parts) >= 3:
                    rows.append(f"{parts[0]:>7}  {parts[1]:<30}  {parts[2]:>8}")
            if rows:
                result["processes"] = header + "\n" + "\n".join(rows)
    except Exception:
        pass

    if result["temp"] is not None:
        state.gpu_history.append({
            "ts": time.time(), "temp": result["temp"],
            "vram_used": result["vram_used"], "vram_total": result["vram_total"],
            "util": result["util"] or 0
        })

    return result


async def _collect_health_cache() -> tuple[dict, dict]:
    """Health checks per servizi esterni + sys_stats (uptime, load, disk, RAM)."""
    health = {"searxng": False, "crawl4ai": False, "qdrant": False}
    try:
        r = await state.http_client.get(SEARXNG_HOST, timeout=1.0)
        health["searxng"] = (r.status_code < 500)
    except Exception:
        pass
    try:
        health_url = CRAWL4AI_HOST.rstrip('/') + '/health'
        headers = {}
        if CRAWL4AI_API_TOKEN:
            headers["Authorization"] = f"Bearer {CRAWL4AI_API_TOKEN}"
        r = await state.http_client.get(health_url, headers=headers, timeout=2.0)
        health["crawl4ai"] = (r.status_code < 500)
    except Exception:
        pass
    try:
        res = await state.http_client.get(f"http://{QDRANT_HOST}:6333/collections", timeout=2.0)
        health["qdrant"] = (res.status_code == 200)
    except Exception:
        pass

    sys_stats = {"uptime": "N/A", "load": "N/A", "disk": "N/A", "ram_mb": 0}
    loop = asyncio.get_running_loop()
    try:
        content = await loop.run_in_executor(None, lambda: open('/proc/uptime').read())
        uptime_seconds = float(content.split()[0])
        h, m = int(uptime_seconds // 3600), int((uptime_seconds % 3600) // 60)
        sys_stats["uptime"] = f"{h}h {m}m"
    except Exception:
        pass
    try:
        content = await loop.run_in_executor(None, lambda: open('/proc/loadavg').read())
        sys_stats["load"] = " ".join(content.split()[0:3])
    except Exception:
        pass
    try:
        st = await loop.run_in_executor(None, os.statvfs, '/')
        total_gb = (st.f_blocks * st.f_frsize) / (1024 ** 3)
        free_gb = (st.f_bavail * st.f_frsize) / (1024 ** 3)
        sys_stats["disk"] = f"{total_gb - free_gb:.1f}G / {total_gb:.1f}G"
    except Exception:
        pass
    try:
        content = await loop.run_in_executor(None, lambda: open('/proc/self/statm').read())
        process_pages = int(content.split()[1])
        page_size = os.sysconf('SC_PAGE_SIZE')
        sys_stats["ram_mb"] = round((process_pages * page_size) / (1024 * 1024), 1)
    except Exception:
        pass

    return health, sys_stats


async def _collect_qdrant_cache() -> list:
    """Lista collezioni Qdrant con punti."""
    collections = []
    try:
        res = await state.http_client.get(f"http://{QDRANT_HOST}:6333/collections", timeout=2.0)
        if res.status_code == 200:
            c_data = res.json()
            if "result" in c_data and "collections" in c_data["result"]:
                for c in c_data["result"]["collections"]:
                    name = c["name"]
                    try:
                        info = await state.http_client.get(
                            f"http://{QDRANT_HOST}:6333/collections/{name}", timeout=2.0
                        )
                        if info.status_code == 200:
                            pts = info.json().get("result", {}).get("points_count", 0)
                            collections.append({"name": name, "points": pts})
                            continue
                    except Exception:
                        pass
                    collections.append({"name": name})
    except Exception:
        pass
    return collections


async def _collect_synaptiq_cache() -> dict:
    """Stato del motore Synaptiq (leggero: status dal singleton)."""
    try:
        if synaptiq_engine and synaptiq_engine.is_initialized:
            return await synaptiq_engine.status()
    except Exception:
        pass
    return {
        "available": SYNAPTIQ_ENABLED,
        "initialized": False,
        "nodes_count": 0,
        "relationships_count": 0,
    }


async def telemetry_collector_loop():
    """Background task: raccoglie GPU + health + Qdrant + Synaptiq ogni N secondi e li cache."""
    while True:
        try:
            # GPU (operazione pesante → eseguita in thread pool)
            gpu = await _collect_gpu_cache()
            if gpu:
                _telemetry_cache.gpu = gpu
                _telemetry_cache.last_gpu_ts = time.time()
        except Exception as e:
            logger.debug(f"Telemetry GPU collector: {e}")

        try:
            health, sys_stats = await _collect_health_cache()
            _telemetry_cache.health = health
            _telemetry_cache.sys_stats = sys_stats
            _telemetry_cache.last_health_ts = time.time()
        except Exception as e:
            logger.debug(f"Telemetry health collector: {e}")

        try:
            qdrant = await _collect_qdrant_cache()
            _telemetry_cache.qdrant_collections = qdrant
        except Exception as e:
            logger.debug(f"Telemetry Qdrant collector: {e}")

        try:
            sy = await _collect_synaptiq_cache()
            if sy:
                _telemetry_cache.synaptiq = sy
                _telemetry_cache.last_synaptiq_ts = time.time()
        except Exception as e:
            logger.debug(f"Telemetry Synaptiq collector: {e}")

        await asyncio.sleep(_TELEMETRY_POLL_INTERVAL)


def start_telemetry_collector(app):
    """Avvia il background collector. Chiamato dal lifespan di main.py."""
    task = asyncio.create_task(telemetry_collector_loop())
    state.background_tasks.add(task)
    task.add_done_callback(state.background_tasks.discard)
    logger.info("📊 Telemetry collector avviato (poll %ds)", _TELEMETRY_POLL_INTERVAL)
