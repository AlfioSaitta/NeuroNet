import os
import sys
import time
import struct
import json
import uuid
import asyncio
from datetime import datetime, UTC
from collections import deque
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict
from config import QDRANT_HOST, SEARXNG_HOST, CRAWL4AI_HOST, CRAWL4AI_API_TOKEN, ALLOWED_USERS, VECTOR_DB_VERSION
import state
from llm_engine import engine
from tag_processor import strip_action_tags, TagSafeStream

dashboard_router = APIRouter()

from dashboard_template import HTML_CONTENT

# ── Chat session state (in-memory ring buffer per conversation) ──
_chat_sessions: dict[str, deque] = {}
MAX_HISTORY = 200


class ChatStreamRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    message: str
    conversation_id: str = "dashboard_default"
    images: list[str] | None = None  # base64-encoded images


def _get_session(conversation_id: str) -> deque:
    if conversation_id not in _chat_sessions:
        _chat_sessions[conversation_id] = deque(maxlen=MAX_HISTORY)
    return _chat_sessions[conversation_id]


@dashboard_router.get("/")
@dashboard_router.get("/dashboard")
async def get_dashboard():
    return HTMLResponse(HTML_CONTENT)


def collect_sys_metrics():
    result = {"ram_pct": 0, "ram_used_mb": 0, "ram_total_mb": 0, "cpu_pct": 0, "cpu_temp": None}
    try:
        with open('/proc/meminfo') as f:
            mem = {}
            for line in f:
                parts = line.split()
                if parts[0] == 'MemTotal:': mem['total'] = int(parts[1]) // 1024
                if parts[0] == 'MemAvailable:': mem['avail'] = int(parts[1]) // 1024
                if 'total' in mem and 'avail' in mem: break
        if 'total' in mem and mem['total'] > 0:
            result['ram_total_mb'] = mem['total']
            result['ram_used_mb'] = mem['total'] - mem.get('avail', 0)
            result['ram_pct'] = round(result['ram_used_mb'] / mem['total'] * 100, 1)
    except Exception:
        pass

    try:
        with open('/proc/stat') as f:
            line = f.readline().strip().split()
        if line[0] == 'cpu' and len(line) >= 5:
            user = int(line[1]); nice = int(line[2]); sys = int(line[3]); idle = int(line[4])
            total = user + nice + sys + idle
            prev_idle = state.cpu_prev_idle
            prev_total = state.cpu_prev_total
            if prev_total > 0 and prev_idle > 0:
                delta_idle = idle - prev_idle
                delta_total = total - prev_total
                result['cpu_pct'] = round((1 - delta_idle / delta_total) * 100, 1) if delta_total > 0 else 0
            state.cpu_prev_idle = idle
            state.cpu_prev_total = total
    except Exception:
        pass

    for zone in ['/sys/class/thermal/thermal_zone0/temp',
                  '/sys/class/thermal/thermal_zone1/temp',
                  '/sys/class/thermal/thermal_zone2/temp']:
        try:
            with open(zone) as f:
                val = int(f.read().strip()) // 1000
                if 20 < val < 110:
                    result['cpu_temp'] = val
                    break
        except Exception:
            continue

    state.sys_history.append({
        "ts": time.time(),
        "ram_pct": result["ram_pct"],
        "ram_used_mb": result["ram_used_mb"],
        "ram_total_mb": result["ram_total_mb"],
        "cpu_pct": result["cpu_pct"],
        "cpu_temp": result["cpu_temp"]
    })

    return result


async def get_gpu_metrics():
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
            ["nvidia-smi", "--query-gpu=driver_version",
             "--format=csv,noheader,nounits"],
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
            "ts": time.time(),
            "temp": result["temp"],
            "vram_used": result["vram_used"],
            "vram_total": result["vram_total"],
            "util": result["util"] or 0
        })

    return result


@dashboard_router.get("/api/dashboard/gpu")
async def get_gpu_json():
    return JSONResponse(await get_gpu_metrics())


@dashboard_router.get("/api/dashboard/stats")
async def get_stats():
    import json

    gpu = await get_gpu_metrics()

    sys_m = collect_sys_metrics()

    # Inference delta tracking
    prev_req = getattr(state, '_prev_total_requests', None)
    prev_pt = getattr(state, '_prev_prompt_tokens', None)
    prev_ct = getattr(state, '_prev_completion_tokens', None)
    cur_req = getattr(state, 'total_requests', 0)
    cur_pt = getattr(state, 'total_prompt_tokens', 0)
    cur_ct = getattr(state, 'total_completion_tokens', 0)

    if prev_req is not None and prev_pt is not None and prev_ct is not None:
        delta_req = cur_req - prev_req
        delta_pt = cur_pt - prev_pt
        delta_ct = cur_ct - prev_ct
        state.inference_history.append({
            "ts": time.time(),
            "requests": max(delta_req, 0),
            "prompt_tokens": max(delta_pt, 0),
            "completion_tokens": max(delta_ct, 0),
            "tokens_per_sec": round(max(delta_ct, 0) / 3, 1) if delta_ct > 0 else 0
        })

    state._prev_total_requests = cur_req
    state._prev_prompt_tokens = cur_pt
    state._prev_completion_tokens = cur_ct

    qdrant_collections = []
    qdrant_up = False
    try:
        res = await state.http_client.get(f"http://{QDRANT_HOST}:6333/collections", timeout=2.0)
        if res.status_code == 200:
            qdrant_up = True
            c_data = res.json()
            if "result" in c_data and "collections" in c_data["result"]:
                for c in c_data["result"]["collections"]:
                    name = c["name"]
                    try:
                        info = await state.http_client.get(f"http://{QDRANT_HOST}:6333/collections/{name}", timeout=2.0)
                        if info.status_code == 200:
                            pts = info.json().get("result", {}).get("points_count", 0)
                            qdrant_collections.append({"name": name, "points": pts})
                            continue
                    except Exception:
                        pass
                    qdrant_collections.append({"name": name})
    except Exception:
        pass

    total_requests = getattr(state, 'total_requests', 0)
    total_prompt_tokens = getattr(state, 'total_prompt_tokens', 0)
    total_completion_tokens = getattr(state, 'total_completion_tokens', 0)

    models = {}
    try:
        chat_model_name = "N/A"
        embed_model_name = "N/A"
        details = []
        if engine and engine.chat_model:
            cm = engine.chat_model
            mp = getattr(cm, 'model_path', '') or ''
            chat_model_name = mp.split('/')[-1] if mp else "Loaded"
            mp2 = getattr(cm, 'model_params', None)
            cp2 = getattr(cm, 'context_params', None)
            ngl = (getattr(mp2, 'n_gpu_layers', '?') if mp2 else
                   getattr(cm, 'n_gpu_layers', '?'))
            try:
                meta = cm.metadata if hasattr(cm, 'metadata') else {}
                total_layers = meta.get('gemma4.block_count') or meta.get('llama.block_count') or meta.get('LLaMA.block_count') or '?'
            except Exception:
                total_layers = '?'
            ngl_str = f"{ngl} / {total_layers}" if total_layers != '?' else str(ngl)
            details.append({"label": "n_gpu_layers", "value": ngl_str})
            try:
                ctx = cm.n_ctx()
                try:
                    meta = cm.metadata if hasattr(cm, 'metadata') else {}
                    ctx_max = meta.get('gemma4.context_length') or meta.get('llama.context_length') or meta.get('LLaMA.context_length') or cm.n_ctx_train()
                except Exception:
                    ctx_max = None
                ctx_str = f"{ctx} / {ctx_max}" if ctx_max and ctx_max != ctx else str(ctx)
                details.append({"label": "n_ctx", "value": ctx_str})
            except Exception:
                details.append({"label": "n_ctx", "value": "?"})
            fa_type = (getattr(cp2, 'flash_attn_type', None) if cp2 else
                       getattr(cm, 'flash_attn_type', None))
            if fa_type is None:
                fa = '?'
            elif fa_type == 1:
                fa = 'True'
            else:
                fa = 'False'
            details.append({"label": "flash_attn", "value": str(fa)})
        else:
            details.append({"label": "Status", "value": "Not loaded"})
        if engine and engine.embed_model:
            mp = getattr(engine.embed_model, 'model_path', '') or ''
            embed_model_name = mp.split('/')[-1] if mp else "Loaded"
        models = {"chat_model": chat_model_name, "embed_model": embed_model_name, "details": details}
    except Exception as e:
        models = {"chat_model": "Error", "embed_model": "Error", "details": [{"label": "error", "value": str(e)}]}

    searxng_up = False
    try:
        r = await state.http_client.get(SEARXNG_HOST, timeout=1.0)
        searxng_up = (r.status_code < 500)
    except Exception:
        pass

    crawl4ai_up = False
    try:
        health_url = CRAWL4AI_HOST.rstrip('/') + '/health'
        headers = {}
        if CRAWL4AI_API_TOKEN:
            headers["Authorization"] = f"Bearer {CRAWL4AI_API_TOKEN}"
        r = await state.http_client.get(health_url, headers=headers, timeout=2.0)
        crawl4ai_up = (r.status_code < 500)
    except Exception:
        pass

    try:
        process = open('/proc/self/statm').read().split()[1]
        page_size = os.sysconf('SC_PAGE_SIZE')
        ram_mb = round((int(process) * page_size) / (1024 * 1024), 1)
    except:
        ram_mb = 0

    try:
        with open('/proc/uptime', 'r') as f:
            uptime_seconds = float(f.readline().split()[0])
            sys_uptime = f"{int(uptime_seconds // 3600)}h {int((uptime_seconds % 3600) // 60)}m"
    except:
        sys_uptime = "N/A"

    try:
        with open('/proc/loadavg', 'r') as f:
            sys_load = " ".join(f.readline().split()[0:3])
    except:
        sys_load = "N/A"

    try:
        st = os.statvfs('/')
        total_gb = (st.f_blocks * st.f_frsize) / (1024 ** 3)
        free_gb = (st.f_bavail * st.f_frsize) / (1024 ** 3)
        sys_disk = f"{total_gb - free_gb:.1f}G / {total_gb:.1f}G"
    except:
        sys_disk = "N/A"

    active_todos = 0
    active_crons = 0
    try:
        from task_manager import load_tasks
        tasks = load_tasks()
        active_todos = len([t for t in tasks.values() if t.get('status') != 'done'])
    except: pass

    try:
        from cron_agent import load_jobs
        jobs = load_jobs()
        active_crons = len(jobs)
    except: pass

    total_chunks = sum(len(f_data.get('chunks', [])) for f_data in state.rag_state.values())

    features = {
        "llm": bool(engine and engine.chat_model),
        "embeddings": bool(engine and engine.embed_model),
        "rag": qdrant_up,
        "memory": bool(state.memory),
        "ast_parser": True,
        "file_watcher": True,
        "telegram": bool(state.telegram_app),
        "cron": active_crons > 0 or True,
        "searxng": searxng_up,
        "crawl4ai": crawl4ai_up,
        "whisper": bool(state.telegram_app),
        "userbots": True,
    }

    # Telemetry summary
    gk_stats = getattr(state, 'gatekeeper_stats', None)
    gatekeeper_data = gk_stats.to_dict() if gk_stats and hasattr(gk_stats, 'to_dict') else None
    error_count = len(getattr(state, 'error_counters', {}))
    trace_count = len(getattr(state, 'pipeline_traces', []))
    active_traces_count = 0
    try:
        from telemetry import PipelineTracer
        active_traces_count = len(PipelineTracer.get_all_active())
    except Exception:
        pass

    return JSONResponse({
        "rag_stats": {
            "indexed_files": len(state.rag_state),
            "pending_events": state.file_event_queue.qsize() if hasattr(state, "file_event_queue") and state.file_event_queue else 0,
            "total_chunks": total_chunks
        },
        "models": models,
        "features": features,
        "inference": {
            "total_requests": total_requests,
            "total_prompt_tokens": total_prompt_tokens,
            "total_completion_tokens": total_completion_tokens
        },
        "gpu": gpu,
        "gpu_history": list(state.gpu_history)[-120:],
        "sys_metrics": sys_m,
        "sys_history": list(state.sys_history)[-120:],
        "inference_history": list(state.inference_history)[-120:],
        "qdrant_collections": qdrant_collections,
        "agent_stats": {
            "active_todos": active_todos,
            "active_crons": active_crons,
            "allowed_users": len(ALLOWED_USERS),
            "async_tasks": len(state.background_tasks) if hasattr(state, "background_tasks") else 0
        },
        "health": {
            "searxng": searxng_up,
            "crawl4ai": crawl4ai_up,
            "qdrant": qdrant_up
        },
        "sys_stats": {
            "ram_mb": ram_mb,
            "uptime": sys_uptime,
            "load": sys_load,
            "disk": sys_disk
        },
        "telemetry": {
            "gatekeeper": gatekeeper_data,
            "error_count": error_count,
            "trace_count": trace_count,
            "active_traces": active_traces_count,
            "mcp_v2_active": True,
        }
    })


@dashboard_router.get("/api/dashboard/qdrant/{collection}/vectors")
async def get_qdrant_vectors(collection: str):
    import numpy as np
    points_data = []
    links_data = []
    added_pairs = set()
    try:
        # Paginate: Qdrant scroll returns up to `limit` points per call
        all_raw_points = []
        offset = None
        scroll_limit = 2000  # use a higher limit, Qdrant default max is ~10K
        while True:
            body = {"limit": min(scroll_limit, 1000), "with_payload": True, "with_vector": True}
            if offset is not None:
                body["offset"] = offset

            res_pts = await state.http_client.post(
                f"http://{QDRANT_HOST}:6333/collections/{collection}/points/scroll",
                json=body, timeout=8.0
            )
            if res_pts.status_code != 200:
                break

            result = res_pts.json().get("result", {})
            batch = result.get("points", [])
            all_raw_points.extend(batch)

            # Check if there are more points
            next_offset = result.get("next_page_offset")
            if next_offset is None or not batch:
                break
            offset = next_offset

            if len(all_raw_points) >= 2000:  # safety limit
                break

        vectors = []
        for p in all_raw_points:
            vec = p.get("vector")
            if vec is not None:
                # Handle named vectors: {"": [0.1, 0.2, ...], "bm25": {...}} → flat array
                if isinstance(vec, dict):
                    # Mem0 stores dense embedding under empty key "", and optional
                    # sparse BM25 under "bm25". Take only the dense embedding.
                    vec = vec.get("", None)
                    if vec is None:
                        continue
                vectors.append(vec)
                del p["vector"]
            points_data.append(p)

        if vectors:
            vec_mat = np.array(vectors, dtype=np.float32)
            norms = np.linalg.norm(vec_mat, axis=1, keepdims=True)
            norms[norms == 0] = 1
            vec_mat_norm = vec_mat / norms
            sim_matrix = np.dot(vec_mat_norm, vec_mat_norm.T)

            # Map point index -> filename for diversity filtering
            filenames = []
            for p in points_data:
                fn = (p.get("payload") or {}).get("filename", "") or ""
                filenames.append(fn)

            TOP_K = 10
            n_pts = len(vectors)

            for i in range(n_pts):
                row = sim_matrix[i].copy()
                row[i] = -1  # exclude self
                if n_pts <= TOP_K + 1:
                    top_indices = np.argsort(row)[::-1]
                else:
                    top_indices = np.argpartition(row, -TOP_K)[-TOP_K:]
                    top_indices = top_indices[np.argsort(row[top_indices])[::-1]]

                added = 0
                same_file_count = 0
                seen_files = set()
                for j in top_indices:
                    sim = float(row[j])
                    if sim < 0.35:
                        continue

                    pair_key = (min(i, j), max(i, j))
                    if pair_key in added_pairs:
                        continue

                    same_file = filenames[i] and filenames[j] and filenames[i] == filenames[j]

                    if same_file:
                        if same_file_count >= 2:
                            continue
                        same_file_count += 1
                    elif filenames[j]:
                        seen_files.add(filenames[j])
                        if len(seen_files) > 6:
                            continue

                    if added >= 8:
                        break

                    added_pairs.add(pair_key)
                    links_data.append({
                        "source": points_data[i]["id"],
                        "target": points_data[j]["id"],
                        "similarity": sim
                    })
                    added += 1

    except Exception as e:
        logger.warning(f"Graph error for {collection}: {e}")
        return JSONResponse({
            "points": points_data or [],
            "links": [],
            "note": f"Errore elaborazione grafo: {e}"
        })

    return JSONResponse({"points": points_data, "links": links_data})


MEMORY_COLLECTION = f"collateral_memories_{VECTOR_DB_VERSION}"
ENTITY_COLLECTION = f"{MEMORY_COLLECTION}_entities"


@dashboard_router.get("/api/dashboard/graph/memory")
async def get_memory_graph(user_id: str = "alfio_dev"):
    """Grafo bipartito: nodi entità ↔ nodi memoria dall'entity store.

    Scansiona la entity store (``collateral_memories_v3_entities``) e la
    collection delle memorie (``collateral_memories_v3``) via state.qdrant,
    poi costruisce un grafo dove:
      - I nodi entità (colore viola) sono le entità estratte via regex
      - I nodi memoria (colore ciano) sono i ricordi episodici
      - I link connettono ogni entità alle memorie che la contengono
    """
    from qdrant_client import models as qdrant_models

    nodes = []
    links = []
    memory_lookup = {}  # memory_id → memory text
    entity_lookup = {}  # entity_name → metadata

    if not state.qdrant:
        return JSONResponse({"error": "Qdrant not available"}, status_code=503)

    # 1) Fetch memories from the memory collection
    try:
        all_memory_points, _ = await state.qdrant.scroll(
            collection_name=MEMORY_COLLECTION,
            limit=1000,
            with_payload=True,
            with_vectors=False,
            scroll_filter=qdrant_models.Filter(
                must=[qdrant_models.FieldCondition(
                    key="user_id",
                    match=qdrant_models.MatchValue(value=user_id),
                )]
            ),
        )
        for p in all_memory_points:
            pid = str(p.id)
            payload = p.payload or {}
            mem_text = payload.get("data", "") or payload.get("memory", "") or payload.get("text", "")
            memory_lookup[pid] = mem_text
    except Exception:
        pass

    # 2) Fetch entities from the entity store
    try:
        all_entity_points, _ = await state.qdrant.scroll(
            collection_name=ENTITY_COLLECTION,
            limit=1000,
            with_payload=True,
            with_vectors=False,
        )
        for p in all_entity_points:
            payload = p.payload or {}
            ent_name = payload.get("entity_name", "") or ""
            linked = payload.get("linked_memory_ids") or []
            ent_type = payload.get("entity_type", "unknown")
            if not ent_name:
                continue
            entity_lookup[ent_name] = {
                "linked_memory_ids": linked,
                "entity_type": ent_type,
            }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

    # 3) Build graph nodes & links
    for ent_name, ent_meta in entity_lookup.items():
        linked_ids = ent_meta["linked_memory_ids"]
        if not linked_ids:
            continue

        valid_linked = [lid for lid in linked_ids if lid in memory_lookup]
        if not valid_linked:
            continue

        ent_node_id = f"ent_{ent_name}"
        connected_count = len(valid_linked)

        nodes.append({
            "id": ent_node_id,
            "payload": {
                "entity_name": ent_name,
                "entity_type": ent_meta["entity_type"],
                "connected_memories": connected_count,
            },
            "ext": "entity",
            "group": "entity",
        })

        for mem_id in valid_linked:
            mem_text = memory_lookup.get(mem_id, "")
            mem_excerpt = mem_text[:120] + "…" if len(mem_text) > 120 else mem_text
            links.append({
                "source": ent_node_id,
                "target": mem_id,
                "similarity": 0.9,
            })

    # 4) Add memory nodes that are connected to at least one entity
    connected_memory_ids = {link["target"] for link in links}
    for mem_id, mem_text in memory_lookup.items():
        if mem_id not in connected_memory_ids:
            continue
        mem_excerpt = mem_text[:150] + "…" if len(mem_text) > 150 else mem_text
        nodes.append({
            "id": mem_id,
            "payload": {
                "memory": mem_excerpt,
                "entity_count": sum(1 for lnk in links if lnk["target"] == mem_id),
            },
            "ext": "memory",
            "group": "memory",
        })

    if not nodes:
        return JSONResponse({
            "points": [],
            "links": [],
            "message": "Nessuna entità collegata trovata. Usa prima /api/graph/reindex per creare i link.",
        })

    return JSONResponse({
        "points": nodes,
        "links": links,
        "entity_count": len([n for n in nodes if n.get("group") == "entity"]),
        "memory_count": len([n for n in nodes if n.get("group") == "memory"]),
    })


# ==============================================================================
# DOCKER HELPERS
# ==============================================================================

import http.client
import json
import socket as skt

DOCKER_SOCKET_PATH = next(
    (p for p in ["/var/run/docker.sock", "/run/docker.sock", "/host_fs/var/run/docker.sock", "/host_fs/run/docker.sock"] if os.path.exists(p)),
    "/var/run/docker.sock"
)


def _docker_connect(timeout: float = 10.0):
    """Create an HTTPConnection over a Unix socket to the Docker daemon."""
    conn = http.client.HTTPConnection("localhost", timeout=timeout)
    sock = skt.socket(skt.AF_UNIX, skt.SOCK_STREAM)
    sock.settimeout(timeout)
    sock.connect(DOCKER_SOCKET_PATH)
    conn.sock = sock
    return conn


def _docker_api_sync(method: str, path: str, timeout: float = 10.0):
    """Call Docker Engine API via Unix socket (synchronous). Returns (data, error)."""
    try:
        conn = _docker_connect(timeout)
        conn.request(method, path, headers={"Host": "localhost"})
        resp = conn.getresponse()
        body = resp.read()
        ct = resp.getheader("Content-Type", "") or ""

        if resp.status >= 400:
            return None, f"Docker API returned {resp.status}: {body.decode(errors='replace')[:200]}"

        if "application/json" in ct.lower():
            return json.loads(body), None
        return body, None

    except Exception as e:
        return None, str(e)


async def _docker_api(method: str, path: str, timeout: float = 10.0):
    """Async wrapper around _docker_api_sync."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _docker_api_sync, method, path, timeout)


def _parse_docker_logs(data: bytes) -> list[str]:
    """Parse Docker's multiplexed log stream (8-byte header + payload)."""
    lines = []
    idx = 0
    while idx + 8 <= len(data):
        length = struct.unpack('>I', data[idx+4:idx+8])[0]
        idx += 8
        if idx + length > len(data):
            break
        chunk = data[idx:idx+length]
        text = chunk.decode('utf-8', errors='replace').rstrip('\n\r')
        if text:
            lines.append(text)
        idx += length
    return lines


FALLBACK_CONTAINERS = [
    {"id": "jarvis", "name": "jarvis", "status": "Running (jarvis)", "state": "running", "image": "jarvis:latest"},
    {"id": "qdrant_db", "name": "qdrant_db", "status": "Running (qdrant)", "state": "running", "image": "qdrant/qdrant:latest"},
    {"id": "searxng", "name": "searxng", "status": "Running (searxng)", "state": "running", "image": "searxng/searxng:latest"},
    {"id": "crawl4ai_server", "name": "crawl4ai_server", "status": "Running (crawl4ai)", "state": "running", "image": "unclecode/crawl4ai:latest"},
]


async def _fetch_containers() -> list[dict]:
    data, err = await _docker_api("GET", "/containers/json?all=true")
    if err or not isinstance(data, list):
        return FALLBACK_CONTAINERS
    result = []
    for c in data:
        names = [n.lstrip("/") for n in c.get("Names", [])]
        result.append({
            "id": c.get("Id", "")[:12],
            "name": names[0] if names else "unknown",
            "names": names,
            "status": c.get("Status", "unknown"),
            "state": c.get("State", "unknown"),
            "image": c.get("Image", ""),
        })
    return result


async def _resolve_container(name: str) -> tuple[str | None, str | None]:
    """Resolve container name/prefix to full ID. Returns (id, display_name) or (None, error)."""
    data, err = await _docker_api("GET", "/containers/json?all=true")
    if err or not isinstance(data, list):
        return None, f"Docker API error: {err}"
    for c in data:
        cnames = [n.lstrip("/") for n in c.get("Names", [])]
        cid = c.get("Id", "")
        if name in cnames or cid.startswith(name):
            return cid, cnames[0] if cnames else cid[:12]
    return None, f"Container '{name}' not found"


# ==============================================================================
# DOCKER API ROUTES
# ==============================================================================

@dashboard_router.get("/api/dashboard/containers")
async def list_containers():
    containers = await _fetch_containers()
    return JSONResponse({"containers": containers})


@dashboard_router.get("/api/dashboard/containers/{name:path}/logs")
async def get_container_logs(name: str, tail: int = 200):
    if name == "all":
        containers = await _fetch_containers()
        all_logs: list[dict] = []
        for c in containers:
            raw, err = await _docker_api("GET", f"/containers/{c['id']}/logs?stdout=1&stderr=1&tail={tail}", timeout=8.0)
            if err or not isinstance(raw, bytes):
                all_logs.append({"container": c["name"], "message": f"[Error fetching logs: {err}]"})
            else:
                for line in _parse_docker_logs(raw):
                    all_logs.append({"container": c["name"], "message": line})
        return JSONResponse({"logs": all_logs, "container": "all"})

    cid, err = await _resolve_container(name)
    if err or not cid:
        return JSONResponse({"error": err or "Container not found"}, status_code=404)

    raw, err = await _docker_api("GET", f"/containers/{cid}/logs?stdout=1&stderr=1&tail={tail}", timeout=8.0)
    if err or not isinstance(raw, bytes):
        return JSONResponse({"error": err or "Failed to fetch logs"}, status_code=500)

    lines = _parse_docker_logs(raw)
    cname = name
    return JSONResponse({"logs": [{"container": cname, "message": l} for l in lines], "container": cname})


@dashboard_router.post("/api/dashboard/containers/{name:path}/restart")
async def restart_container(name: str):
    cid, err = await _resolve_container(name)
    if err or not cid:
        return JSONResponse({"error": err or "Container not found"}, status_code=404)

    _, api_err = await _docker_api("POST", f"/containers/{cid}/restart", timeout=30.0)
    if api_err:
        return JSONResponse({"error": api_err}, status_code=500)
    return JSONResponse({"status": "restarting", "container": name})


@dashboard_router.get("/api/dashboard/telemetry")
async def get_dashboard_telemetry():
    """Aggregated telemetry data: gatekeeper stats, error counters, recent traces."""
    import logging
    logger = logging.getLogger(__name__)

    gk_stats = getattr(state, 'gatekeeper_stats', None)
    gatekeeper_data = gk_stats.to_dict() if gk_stats and hasattr(gk_stats, 'to_dict') else None

    errors = dict(getattr(state, 'error_counters', {}))
    traces = []
    try:
        pipeline_traces = getattr(state, 'pipeline_traces', [])
        # deque does not support slicing → convert to list first
        trace_list = list(pipeline_traces) if hasattr(pipeline_traces, '__len__') else pipeline_traces
        traces = [t.to_dict() if hasattr(t, 'to_dict') else t for t in trace_list[-10:]]
    except Exception as e:
        logger.warning(f"Error reading traces: {e}")

    active_traces_list = []
    try:
        from telemetry import PipelineTracer
        active_traces_list = PipelineTracer.get_all_active()
    except Exception:
        pass

    return JSONResponse({
        "gatekeeper": gatekeeper_data,
        "error_counters": errors,
        "recent_traces": traces,
        "active_traces": active_traces_list,
    })


@dashboard_router.post("/api/dashboard/ingestion/restart")
async def restart_ingestion():
    from rag import ingest_local_documents
    state.is_reindexing = True
    task = asyncio.create_task(ingest_local_documents())
    state.background_tasks.add(task)
    task.add_done_callback(state.background_tasks.discard)
    return JSONResponse({"status": "success", "message": "Document ingestion re-started"})


# ═══════════════════════════════════════════════════════════════
# Dashboard Chat Endpoints
# ═══════════════════════════════════════════════════════════════


@dashboard_router.get("/api/dashboard/chat-history")
async def get_chat_history(conversation_id: str = "dashboard_default"):
    """Return message history for the dashboard chat."""
    session = _get_session(conversation_id)
    return JSONResponse({"messages": list(session)})


@dashboard_router.post("/api/dashboard/chat/stream")
async def chat_stream(payload: ChatStreamRequest, request: Request):
    """SSE streaming chat endpoint for the dashboard."""
    conversation_id = payload.conversation_id
    session = _get_session(conversation_id)

    # Store user message
    user_msg = {"role": "user", "content": payload.message, "timestamp": datetime.now(UTC).isoformat()}
    if payload.images:
        user_msg["images"] = payload.images
    session.append(user_msg)

    # Build messages for the engine — include history
    raw_messages = []
    for m in list(session):
        if m.get("role") == "user":
            content = m["content"]
            if m.get("images"):
                # Build multimodal content array
                parts = [{"type": "text", "text": content}]
                for b64img in m["images"]:
                    parts.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64img}"}})
                raw_messages.append({"role": "user", "content": parts})
            else:
                raw_messages.append({"role": "user", "content": content})
        elif m.get("role") == "assistant":
            raw_messages.append({"role": "assistant", "content": m["content"]})

    if not raw_messages:
        raw_messages = [{"role": "user", "content": payload.message}]

    from prompt_builder import build_omniscient_prompt
    current_user_id = "dashboard_user"
    request_id = str(uuid.uuid4())[:12]

    try:
        enhanced_messages = await build_omniscient_prompt(
            raw_messages, user_id=current_user_id,
            conversation_id=conversation_id, concise=False,
            request_id=request_id
        )
    except Exception:
        enhanced_messages = raw_messages

    async def sse_generator():
        full_text_chunks = []
        safe_stream = TagSafeStream()
        stream_start_time = time.monotonic()
        first_token_time = None

        try:
            gen = await engine.generate_chat_with_router(
                enhanced_messages, tools=None, options={}, stream=True
            )

            if isinstance(gen, dict) and "error" in gen:
                yield f"data: {json.dumps({'error': gen['error']})}\n\n"
                yield f"data: {json.dumps({'done': True})}\n\n"
                return

            async for chunk in gen:
                if "choices" in chunk and len(chunk["choices"]) > 0:
                    delta = chunk["choices"][0].get("delta", {})
                    content = delta.get("content", "")
                    if content:
                        if first_token_time is None:
                            first_token_time = time.monotonic()
                        full_text_chunks.append(content)
                        cleaned = safe_stream.process(content)
                        if cleaned:
                            yield f"data: {json.dumps({'content': cleaned})}\n\n"

            final_flush = safe_stream.flush()
            if final_flush:
                full_text_chunks.append(final_flush)
                yield f"data: {json.dumps({'content': final_flush})}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

        stream_end_time = time.monotonic()
        full_text = "".join(full_text_chunks)
        clean_text = strip_action_tags(full_text) if full_text else ""

        # Compute metrics
        total_duration_ms = round((stream_end_time - stream_start_time) * 1000, 1)
        ttft_ms = round((first_token_time - stream_start_time) * 1000, 1) if first_token_time else total_duration_ms
        char_count = len(full_text)
        estimated_tokens = max(1, char_count // 4)
        elapsed_sec = max(0.001, stream_end_time - stream_start_time)
        tok_per_sec = round(estimated_tokens / elapsed_sec, 1)

        # Store assistant response with metrics
        if clean_text:
            session.append({
                "role": "assistant",
                "content": clean_text,
                "timestamp": datetime.now(UTC).isoformat(),
                "metrics": {"ttft_ms": ttft_ms, "tok_per_sec": tok_per_sec, "tokens": estimated_tokens, "chars": char_count}
            })

        # Process tags in background
        if full_text:
            try:
                bg_task = asyncio.create_task(
                    process_response_tags(full_text, user_id=current_user_id)
                )
                state.background_tasks.add(bg_task)
                bg_task.add_done_callback(state.background_tasks.discard)
            except Exception:
                pass

        yield f"data: {json.dumps({'done': True, 'full_text': clean_text, 'ttft_ms': ttft_ms, 'tok_per_sec': tok_per_sec, 'tokens': estimated_tokens, 'duration_ms': total_duration_ms})}\n\n"

    return StreamingResponse(sse_generator(), media_type="text/event-stream")


# Lazy import for tag processing
async def process_response_tags(text: str, user_id: str):
    try:
        from tag_processor import process_response_tags as _process
        await _process(text, user_id=user_id)
    except Exception:
        pass
