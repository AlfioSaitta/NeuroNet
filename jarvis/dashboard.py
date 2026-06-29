import os
import sys
import time
import struct
import asyncio
from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse
from config import QDRANT_HOST, ALLOWED_USERS, VECTOR_DB_VERSION
import state
from llm_engine import engine

dashboard_router = APIRouter()

HTML_CONTENT = r"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>NeuroNet — Neural Control Panel</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;800&family=JetBrains+Mono:wght@400;700&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.8.0/styles/atom-one-dark.min.css">
    <script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.8.0/highlight.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>
    <script type="importmap">
    {
        "imports": {
            "sigma": "https://esm.sh/sigma@3.0.3",
            "graphology": "https://esm.sh/graphology@0.26.0",
            "graphology-layout-forceatlas2": "https://esm.sh/graphology-layout-forceatlas2@0.10.1",
            "graphology-layout-forceatlas2/worker": "https://esm.sh/graphology-layout-forceatlas2@0.10.1/worker"
        }
    }
    </script>
    <script type="module">
    import Graphology from 'graphology';
    import Sigma from 'sigma';
    import forceAtlas2 from 'graphology-layout-forceatlas2';
    import FA2Layout from 'graphology-layout-forceatlas2/worker';
    window.__sigma = Sigma;
    window.__graphology = Graphology;
    window.__fa2 = forceAtlas2;
    window.__fa2Worker = FA2Layout;
    </script>
    <style>
        :root {
            --bg-base: #05070a;
            --glass-bg: rgba(15, 20, 30, 0.6);
            --glass-border: rgba(0, 255, 204, 0.1);
            --primary: #00ffcc;
            --secondary: #00b8ff;
            --accent: #7b2cbf;
            --danger: #ff3366;
            --warning: #ffcc00;
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
            --card-radius: 16px;
        }
        * { box-sizing: border-box; }
        body {
            margin: 0; padding: 0;
            font-family: 'Inter', sans-serif;
            background: var(--bg-base);
            color: var(--text-main);
            height: 100vh;
            display: flex;
            overflow: hidden;
            background-image: 
                radial-gradient(circle at 0% 0%, rgba(123, 44, 191, 0.15), transparent 30%),
                radial-gradient(circle at 100% 100%, rgba(0, 255, 204, 0.1), transparent 30%);
        }
        
        .sidebar {
            width: 320px;
            background: rgba(10, 15, 20, 0.8);
            backdrop-filter: blur(20px);
            border-right: 1px solid var(--glass-border);
            padding: 30px 20px;
            display: flex;
            flex-direction: column;
            gap: 20px;
            overflow-y: auto;
            z-index: 10;
            flex-shrink: 0;
        }
        .brand {
            text-align: center; padding-bottom: 10px;
            border-bottom: 1px solid rgba(255,255,255,0.05);
        }
        .brand h1 {
            font-weight: 800; font-size: 2.2rem;
            background: linear-gradient(90deg, var(--primary), var(--secondary));
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            margin: 0; letter-spacing: -1px;
        }
        .brand .subtitle {
            font-family: 'JetBrains Mono', monospace; font-size: 0.75rem;
            color: var(--text-muted); text-transform: uppercase; letter-spacing: 2px; margin-top: 5px;
        }
        
        .main-content {
            flex: 1;
            padding: 30px;
            overflow-y: auto;
            display: flex;
            flex-direction: column;
            gap: 24px;
        }

        .grid-container {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(360px, 1fr));
            gap: 20px;
        }

        .card {
            background: var(--glass-bg);
            border: 1px solid var(--glass-border);
            border-radius: var(--card-radius); padding: 20px;
            box-shadow: 0 8px 32px rgba(0,0,0,0.3);
            transition: transform 0.3s ease, box-shadow 0.3s ease;
        }
        .card:hover {
            transform: translateY(-2px);
            box-shadow: 0 12px 40px rgba(0, 255, 204, 0.05);
            border-color: rgba(0, 255, 204, 0.3);
        }
        .card-header {
            font-size: 0.95rem; font-weight: 600; color: #fff;
            text-transform: uppercase; letter-spacing: 1px;
            display: flex; align-items: center; margin-bottom: 18px;
        }
        
        .dot { height: 10px; width: 10px; border-radius: 50%; display: inline-block; margin-right: 12px; box-shadow: 0 0 10px currentColor; flex-shrink: 0; }
        .dot-primary { background: var(--primary); color: var(--primary); }
        .dot-secondary { background: var(--secondary); color: var(--secondary); }
        .dot-accent { background: var(--accent); color: var(--accent); }
        .dot-warning { background: var(--warning); color: var(--warning); }
        .dot-danger { background: var(--danger); color: var(--danger); }
        .pulsing { animation: pulse 2s infinite ease-in-out; }
        @keyframes pulse { 0% { opacity: 1; transform: scale(1); } 50% { opacity: 0.5; transform: scale(1.4); } 100% { opacity: 1; transform: scale(1); } }
        .fade-in { animation: fadeIn 0.5s ease-in; }
        @keyframes fadeIn { from { opacity: 0; transform: translateY(5px); } to { opacity: 1; transform: translateY(0); } }

        .metric-row { display: flex; gap: 16px; align-items: flex-end; margin-bottom: 14px; }
        .metric { flex: 1; min-width: 0; }
        .metric .val { font-size: 1.6rem; font-weight: 800; font-family: 'JetBrains Mono', monospace; margin: 0; line-height: 1.2; }
        .metric .label { color: var(--text-muted); font-size: 0.7rem; text-transform: uppercase; font-weight: 600; letter-spacing: 1px; margin-top: 4px; }

        .progress-bar {
            width: 100%; height: 8px; background: rgba(255,255,255,0.05);
            border-radius: 4px; overflow: hidden; margin: 8px 0;
        }
        .progress-fill {
            height: 100%; border-radius: 4px;
            transition: width 0.8s ease, background 0.5s ease;
        }
        .progress-fill.green { background: var(--primary); }
        .progress-fill.yellow { background: var(--warning); }
        .progress-fill.red { background: var(--danger); }

        .data-list { list-style: none; padding: 0; margin: 0; font-family: 'JetBrains Mono', monospace; font-size: 0.8rem; }
        .data-list li { padding: 10px 0; border-bottom: 1px solid rgba(255,255,255,0.04); display: flex; justify-content: space-between; align-items: center; }
        .data-list li:last-child { border-bottom: none; }
        
        .badge { padding: 3px 10px; border-radius: 8px; font-weight: 600; font-size: 0.7rem; text-transform: uppercase; white-space: nowrap; }
        .badge-primary { background: rgba(0, 255, 204, 0.1); color: var(--primary); border: 1px solid rgba(0, 255, 204, 0.2); }
        .badge-danger { background: rgba(255, 51, 102, 0.1); color: var(--danger); border: 1px solid rgba(255, 51, 102, 0.2); }
        .badge-accent { background: rgba(123, 44, 191, 0.15); color: #d8b4fe; border: 1px solid rgba(123, 44, 191, 0.3); }
        .badge-warning { background: rgba(255, 204, 0, 0.1); color: var(--warning); border: 1px solid rgba(255, 204, 0, 0.2); }

        .btn {
            background: rgba(0, 255, 204, 0.1); color: var(--primary);
            border: 1px solid rgba(0, 255, 204, 0.3); border-radius: 6px;
            padding: 4px 10px; cursor: pointer; transition: 0.2s;
            font-family: 'JetBrains Mono', monospace; font-size: 0.7rem; font-weight: 600;
        }
        .btn:hover { background: var(--primary); color: #000; box-shadow: 0 0 15px var(--primary); }

        .modal {
            display: none; position: fixed; z-index: 1000;
            left: 320px; top: 0;
            width: calc(100% - 320px); height: 100%;
            background-color: rgba(5, 7, 10, 0.97);
            backdrop-filter: blur(10px);
        }
        .modal-header {
            position: absolute; top: 0; left: 0; width: 100%; padding: 12px 24px;
            display: flex; justify-content: space-between; align-items: center;
            background: linear-gradient(to bottom, rgba(0,0,0,0.85), transparent);
            z-index: 1010;
        }
        .modal-header h2 { margin: 0; font-size: 1rem; font-weight: 600; display: flex; align-items: center; gap: 10px;}
        .close-modal { color: #fff; font-size: 28px; cursor: pointer; transition: 0.2s; line-height: 1;}
        .close-modal:hover { color: var(--danger); transform: scale(1.1);}
        
        #graph-container { width: 100%; height: 100%; }
        #filter-container { display: none; align-items: center; gap: 8px; background: rgba(0,0,0,0.5); padding: 4px 12px; border-radius: 8px; border: 1px solid var(--glass-border); }
        #filter-container input, #filter-container select {
            background: rgba(0,0,0,0.8); color: #fff; padding: 3px 8px;
            border: 1px solid var(--glass-border); border-radius: 4px;
            font-family: 'JetBrains Mono', monospace; font-size: 0.7rem;
        }

        .node-info {
            position: absolute; bottom: 20px; left: 20px; width: 380px;
            max-height: 70vh; overflow-y: auto;
            background: rgba(10, 15, 25, 0.92); border: 1px solid var(--primary);
            padding: 16px; border-radius: 10px; backdrop-filter: blur(15px);
            display: none; z-index: 1010;
            box-shadow: 0 10px 40px rgba(0,0,0,0.5), 0 0 20px rgba(0,255,204,0.1);
        }
        .node-info h3 { margin: 0 0 10px 0; color: var(--primary); font-size: 0.85rem; border-bottom: 1px solid rgba(0,255,204,0.2); padding-bottom: 8px;}
        .node-info .property-row { margin-bottom: 8px; }
        .node-info .property-label { font-size: 0.65rem; color: var(--secondary); text-transform: uppercase; font-weight: 600; margin-bottom: 2px; }
        .node-info .property-value { font-size: 0.8rem; line-height: 1.4; color: #fff; word-break: break-word;}
        .node-info pre { margin: 0; font-family: 'JetBrains Mono', monospace; font-size: 0.7rem; border-radius: 6px; border: 1px solid rgba(255,255,255,0.1); }

        .graph-legend {
            position: absolute; bottom: 20px; right: 20px; z-index: 1010;
            background: rgba(10, 15, 25, 0.85); border: 1px solid rgba(255,255,255,0.08);
            padding: 10px 14px; border-radius: 8px; font-size: 0.65rem;
            display: none; flex-direction: column; gap: 3px;
        }
        .graph-legend .legend-row { display: flex; align-items: center; gap: 6px; }
        .graph-legend .legend-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
        
        ::-webkit-scrollbar { width: 6px; }
        ::-webkit-scrollbar-track { background: rgba(0,0,0,0.2); }
        ::-webkit-scrollbar-thumb { background: rgba(0,255,204,0.3); border-radius: 4px; }
        ::-webkit-scrollbar-thumb:hover { background: rgba(0,255,204,0.6); }

        .gpu-temp { display: flex; align-items: center; gap: 12px; }
        .gpu-temp .temp-val { font-size: 2.4rem; font-weight: 800; font-family: 'JetBrains Mono', monospace; }
        .gpu-temp .temp-label { font-size: 0.8rem; }
        .gpu-processes pre { font-size: 0.7rem; margin: 0; background: rgba(0,0,0,0.3); padding: 8px; border-radius: 6px; max-height: 120px; overflow-y: auto; }
    </style>
</head>
<body>

    <div class="sidebar">
        <div class="brand">
            <h1>NEURONET</h1>
            <div class="subtitle">Neural Control Panel</div>
        </div>

        <div class="card" style="padding: 16px;">
            <div class="card-header" style="font-size: 0.85rem; margin-bottom: 8px;"><span class="dot dot-accent pulsing"></span> Neural Engine</div>
            <div style="margin-bottom: 8px;">
                <div style="display:flex; justify-content:space-between; font-size:0.8rem;">
                    <span style="color:var(--text-muted);">Chat</span>
                    <span id="model-chat-side" style="font-family:'JetBrains Mono',monospace;font-size:0.7rem;max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">--</span>
                </div>
                <div style="display:flex; justify-content:space-between; font-size:0.8rem;">
                    <span style="color:var(--text-muted);">Embed</span>
                    <span id="model-embed-side" style="font-family:'JetBrains Mono',monospace;font-size:0.7rem;max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">--</span>
                </div>
            </div>
            <ul class="data-list" id="model-details-side" style="margin-bottom:8px;"></ul>
            <div style="border-top:1px solid rgba(255,255,255,0.05);padding-top:6px;">
                <div style="font-size:0.7rem;color:var(--text-muted);text-transform:uppercase;letter-spacing:1px;margin-bottom:6px;">Features</div>
                <div id="features-list" style="display:grid;grid-template-columns:1fr 1fr;gap:3px;"></div>
            </div>
        </div>

        <div class="card" style="padding: 16px;">
            <div class="card-header" style="font-size: 0.85rem; margin-bottom: 12px;"><span class="dot dot-accent pulsing"></span> GPU Monitor</div>
            <div class="gpu-temp" style="margin-bottom: 10px;">
                <div class="temp-val" id="gpu-temp">--</div>
                <div style="flex:1">
                    <div class="temp-label" style="color: var(--text-muted);">°C</div>
                    <div class="progress-bar" style="height: 6px;">
                        <div class="progress-fill" id="gpu-temp-bar" style="width: 0%;"></div>
                    </div>
                </div>
            </div>
            <div class="metric-row" style="gap: 10px; margin-bottom: 0;">
                <div class="metric">
                    <div class="val" id="gpu-vram-used" style="font-size: 1rem; color: var(--primary);">--</div>
                    <div class="label" style="font-size: 0.65rem;">VRAM Used</div>
                </div>
                <div class="metric">
                    <div class="val" id="gpu-vram-total" style="font-size: 1rem; color: var(--text-muted);">--</div>
                    <div class="label" style="font-size: 0.65rem;">Total</div>
                </div>
                <div class="metric">
                    <div class="val" id="gpu-util" style="font-size: 1rem;">--</div>
                    <div class="label" style="font-size: 0.65rem;">Util %</div>
                </div>
            </div>
            <div class="progress-bar" style="height: 5px;">
                <div class="progress-fill" id="gpu-vram-bar" style="width: 0%;"></div>
            </div>
            <div class="gpu-processes" id="gpu-processes" style="margin-top: 8px;"></div>
        </div>

        <div class="card" style="padding: 16px;">
            <div class="card-header" style="font-size: 0.85rem; margin-bottom: 12px;"><span class="dot dot-warning"></span> Host</div>
            <div style="display: flex; flex-direction: column; gap: 8px;">
                <div style="display: flex; justify-content: space-between; font-size: 0.8rem;">
                    <span style="color: var(--text-muted);">Uptime</span>
                    <span id="sys-uptime" style="font-family: 'JetBrains Mono', monospace;">--</span>
                </div>
                <div style="display: flex; justify-content: space-between; font-size: 0.8rem;">
                    <span style="color: var(--text-muted);">Load Avg</span>
                    <span id="sys-load" style="font-family: 'JetBrains Mono', monospace;">--</span>
                </div>
                <div style="display: flex; justify-content: space-between; font-size: 0.8rem;">
                    <span style="color: var(--text-muted);">Disk</span>
                    <span id="sys-disk" style="font-family: 'JetBrains Mono', monospace;">--</span>
                </div>
                <div style="display: flex; justify-content: space-between; font-size: 0.8rem;">
                    <span style="color: var(--text-muted);">Agent RAM</span>
                    <span id="sys-ram" style="font-family: 'JetBrains Mono', monospace;">--</span>
                </div>
            </div>
        </div>

        <div class="card" style="padding: 16px;">
            <div class="card-header" style="font-size: 0.85rem; margin-bottom: 12px;">
                <span class="dot dot-secondary"></span> Services
                <span style="flex:1;"></span>
                <button class="btn" onclick="openLogModal()" style="font-size:0.65rem;padding:2px 6px;">Logs</button>
            </div>
            <ul class="data-list">
                <li style="display:flex;gap:4px;flex-wrap:wrap;">
                    <span style="flex:1;">SearXNG</span>
                    <span class="badge" id="health-searxng">...</span>
                    <button class="btn" onclick="restartContainer('searxng')" style="font-size:0.6rem;padding:1px 5px;" title="Restart">⟳</button>
                </li>
                <li style="display:flex;gap:4px;flex-wrap:wrap;">
                    <span style="flex:1;">Crawl4AI</span>
                    <span class="badge" id="health-crawl4ai">...</span>
                    <button class="btn" onclick="restartContainer('crawl4ai_server')" style="font-size:0.6rem;padding:1px 5px;" title="Restart">⟳</button>
                </li>
                <li style="display:flex;gap:4px;flex-wrap:wrap;">
                    <span style="flex:1;">Qdrant</span>
                    <span class="badge" id="health-qdrant">...</span>
                    <button class="btn" onclick="restartContainer('qdrant_db')" style="font-size:0.6rem;padding:1px 5px;" title="Restart">⟳</button>
                </li>
                <li><span>GPU (CUDA)</span> <span class="badge" id="health-cuda">...</span></li>
            </ul>
            <div style="display:flex;gap:6px;margin-top:8px;padding-top:8px;border-top:1px solid rgba(255,255,255,0.05);">
                <button class="btn" onclick="restartIngestion()" style="font-size:0.65rem;padding:3px 8px;">⟳ Restart Ingestion</button>
                <button class="btn" onclick="openMemoryGraphModal()" style="font-size:0.65rem;padding:3px 8px;background:rgba(179,136,255,0.15);border-color:rgba(179,136,255,0.3);color:#b388ff;">✧ Memory Graph</button>
            </div>
        </div>
    </div>

    <div class="main-content">
        <!-- Top metric cards row -->
        <div class="grid-container" style="grid-template-columns: repeat(2, 1fr);">
            <div class="card fade-in">
                <div class="card-header"><span class="dot dot-primary pulsing"></span> Inference</div>
                <div class="metric-row">
                    <div class="metric">
                        <div class="val" id="inf-requests" style="color: var(--warning);">0</div>
                        <div class="label">Requests</div>
                    </div>
                    <div class="metric">
                        <div class="val" id="inf-tokens" style="color: #d8b4fe;">0</div>
                        <div class="label">Compl. Tok</div>
                    </div>
                    <div class="metric">
                        <div class="val" id="inf-prompt-tokens" style="color: var(--secondary);">0</div>
                        <div class="label">Prompt Tok</div>
                    </div>
                    <div class="metric">
                        <div class="val" id="inf-tok-per-sec" style="color: var(--primary);">0</div>
                        <div class="label">Tok/s (3s)</div>
                    </div>
                </div>
            </div>

            <div class="card fade-in">
                <div class="card-header"><span class="dot dot-primary"></span> Agentic</div>
                <div class="metric-row">
                    <div class="metric">
                        <div class="val" id="active-cron">0</div>
                        <div class="label">Cron Jobs</div>
                    </div>
                    <div class="metric">
                        <div class="val" id="active-todos">0</div>
                        <div class="label">Pending To-Dos</div>
                    </div>
                    <div class="metric">
                        <div class="val" id="allowed-users">0</div>
                        <div class="label">ACL Users</div>
                    </div>
                    <div class="metric">
                        <div class="val" id="async-tasks">0</div>
                        <div class="label">Async Tasks</div>
                    </div>
                </div>
            </div>
        </div>

        <!-- Monitoring section: 2 rows of charts -->
        <div style="margin: 16px 0 6px; display:flex; align-items:center; gap:10px;">
            <span class="dot dot-secondary pulsing"></span>
            <span style="font-weight:600;font-size:0.8rem;text-transform:uppercase;letter-spacing:1px;color:var(--text-muted);">Monitoring</span>
            <span style="flex:1;height:1px;background:rgba(255,255,255,0.06);"></span>
        </div>
        <div class="grid-container" style="grid-template-columns: repeat(3, 1fr); margin-bottom: 6px;">
            <div class="card" style="padding: 10px;">
                <div class="card-header" style="font-size: 0.7rem; margin-bottom: 4px;">
                    <span class="dot dot-secondary pulsing"></span> GPU Temperature
                    <span style="margin-left: auto; font-family: 'JetBrains Mono', monospace; font-size: 0.8rem;" id="chart-current-temp">--°C</span>
                </div>
                <div style="position: relative; height: 80px;"><canvas id="chart-temp"></canvas></div>
            </div>
            <div class="card" style="padding: 10px;">
                <div class="card-header" style="font-size: 0.7rem; margin-bottom: 4px;">
                    <span class="dot dot-primary pulsing"></span> VRAM
                    <span style="margin-left: auto; font-family: 'JetBrains Mono', monospace; font-size: 0.8rem;" id="chart-current-vram">-- MiB</span>
                </div>
                <div style="position: relative; height: 80px;"><canvas id="chart-vram"></canvas></div>
            </div>
            <div class="card" style="padding: 10px;">
                <div class="card-header" style="font-size: 0.7rem; margin-bottom: 4px;">
                    <span class="dot dot-accent pulsing"></span> GPU Utilization
                    <span style="margin-left: auto; font-family: 'JetBrains Mono', monospace; font-size: 0.8rem;" id="chart-current-util">--%</span>
                </div>
                <div style="position: relative; height: 80px;"><canvas id="chart-util"></canvas></div>
            </div>
        </div>
        <div class="grid-container" style="grid-template-columns: repeat(3, 1fr); margin-bottom: 12px;">
            <div class="card" style="padding: 10px;">
                <div class="card-header" style="font-size: 0.7rem; margin-bottom: 4px;">
                    <span class="dot dot-warning pulsing"></span> System RAM
                    <span style="margin-left: auto; font-family: 'JetBrains Mono', monospace; font-size: 0.8rem;" id="chart-current-ram">--%</span>
                </div>
                <div style="position: relative; height: 80px;"><canvas id="chart-ram"></canvas></div>
            </div>
            <div class="card" style="padding: 10px;">
                <div class="card-header" style="font-size: 0.7rem; margin-bottom: 4px;">
                    <span class="dot dot-warning pulsing"></span> CPU Usage
                    <span style="margin-left: auto; font-family: 'JetBrains Mono', monospace; font-size: 0.8rem;" id="chart-current-cpu">--%</span>
                </div>
                <div style="position: relative; height: 80px;"><canvas id="chart-cpu"></canvas></div>
            </div>
            <div class="card" style="padding: 10px;">
                <div class="card-header" style="font-size: 0.7rem; margin-bottom: 4px;">
                    <span class="dot dot-danger pulsing"></span> CPU Temperature
                    <span style="margin-left: auto; font-family: 'JetBrains Mono', monospace; font-size: 0.8rem;" id="chart-current-cpu-temp">--°C</span>
                </div>
                <div style="position: relative; height: 80px;"><canvas id="chart-cpu-temp"></canvas></div>
            </div>
        </div>

        <!-- Inference Trend -->
        <div class="grid-container" style="grid-template-columns: 1fr; margin-bottom: 12px;">
            <div class="card" style="padding: 10px;">
                <div class="card-header" style="font-size: 0.7rem; margin-bottom: 4px;">
                    <span class="dot dot-primary pulsing"></span> Inference Trend (tok/s)
                    <span style="margin-left: auto; font-family: 'JetBrains Mono', monospace; font-size: 0.8rem;" id="chart-current-tok-per-sec">-- tok/s</span>
                </div>
                <div style="position: relative; height: 80px;"><canvas id="chart-tok-per-sec"></canvas></div>
            </div>
        </div>

        <!-- Bottom 2-col row -->
        <div class="grid-container">
            <div class="card fade-in" style="display: flex; flex-direction: column;">
                <div class="card-header"><span class="dot dot-primary"></span> Vector Knowledge Base (RAG)</div>
                <div class="metric-row">
                    <div class="metric">
                        <div class="val" id="total-chunks" style="color: var(--primary);">0</div>
                        <div class="label">Vector Chunks</div>
                    </div>
                    <div class="metric">
                        <div class="val" id="indexed-files">0</div>
                        <div class="label">Tracked Files</div>
                    </div>
                    <div class="metric">
                        <div class="val" id="pending-queue" style="color: var(--warning);">0</div>
                        <div class="label">FS Pending</div>
                    </div>
                </div>
                <h3 style="font-size: 0.8rem; color: var(--text-muted); text-transform: uppercase; margin: 14px 0 8px;">Qdrant Collections</h3>
                <ul class="data-list" id="qdrant-list" style="flex: 1; overflow-y: auto; max-height: 200px;">
                </ul>
            </div>

            <div class="card fade-in" style="display: flex; flex-direction: column;">
                <div class="card-header"><span class="dot dot-accent"></span> GPU Processes</div>
                <div id="gpu-proc-list" style="flex:1; overflow-y: auto;">
                    <pre id="gpu-proc-text" style="font-family: 'JetBrains Mono', monospace; font-size: 0.7rem; color: var(--text-muted); margin: 0; white-space: pre-wrap;">Loading...</pre>
                </div>
            </div>
        </div>
    </div>

    <div id="graph-modal" class="modal">
        <div class="modal-header">
            <h2><span class="dot dot-primary pulsing"></span> <span id="modal-title">Vector Network</span></h2>
            <div id="filter-container">
                <input type="text" id="node-search" placeholder="Search..." onkeyup="applyGraphFilter()">
                <label style="color: var(--text-muted); font-size: 0.7rem; text-transform: uppercase; font-family: 'JetBrains Mono', monospace;">Type:</label>
                <select id="file-type-filter" onchange="applyGraphFilter()">
                    <option value="ALL">All Files</option>
                </select>
            </div>
            <div class="close-modal" onclick="closeModal()">&times;</div>
        </div>
        <div id="graph-container"></div>
        <div class="node-info" id="node-info">
            <h3>NODE DETAILS</h3>
            <div id="node-content"></div>
        </div>
        <div class="graph-legend" id="graph-legend"></div>
    </div>

    <!-- Log Viewer Modal -->
    <div id="log-modal" class="modal" style="display:none;">
        <div class="modal-header">
            <h2><span class="dot dot-primary pulsing"></span> Container Logs</h2>
            <div style="display:flex;align-items:center;gap:10px;">
                <select id="log-container-select" onchange="fetchLogs()" style="background:rgba(0,0,0,0.8);color:#fff;padding:4px 8px;border:1px solid var(--glass-border);border-radius:4px;font-family:'JetBrains Mono',monospace;font-size:0.75rem;">
                    <option value="all">All Containers</option>
                </select>
                <label style="color:var(--text-muted);font-size:0.7rem;display:flex;align-items:center;gap:4px;cursor:pointer;">
                    <input type="checkbox" id="log-auto-refresh" checked onchange="toggleAutoRefresh()"> Auto
                </label>
                <button class="btn" onclick="fetchLogs()" style="font-size:0.7rem;">Refresh</button>
            </div>
            <div class="close-modal" onclick="closeLogModal()">&times;</div>
        </div>
        <div style="height:100%;padding-top:56px;overflow-y:auto;">
            <pre id="log-display" style="font-family:'JetBrains Mono',monospace;font-size:0.75rem;padding:16px;margin:0;white-space:pre-wrap;word-break:break-all;color:var(--text-main);"></pre>
        </div>
    </div>

    <script>
        let isModalOpen = false;
        let sigmaInstance = null;
        let fa2Layout = null;
        let selectedNodeId = null;
        let allNodes = [];
        let allLinks = [];

        const graphFilter = { ext: 'ALL', query: '' };

        function escapeHtml(unsafe) {
            return String(unsafe)
                .replace(/&/g, "&amp;")
                .replace(/</g, "&lt;")
                .replace(/>/g, "&gt;")
                .replace(/"/g, "&quot;")
                .replace(/'/g, "&#039;");
        }

        function calcGpuTempColor(temp) {
            if (temp < 65) return { color: '#00ffcc', class: 'green' };
            if (temp < 80) return { color: '#ffcc00', class: 'yellow' };
            return { color: '#ff3366', class: 'red' };
        }

        function calcVramColor(pct) {
            if (pct < 70) return 'green';
            if (pct < 85) return 'yellow';
            return 'red';
        }

        function applyGraphFilter() {
            if (!sigmaInstance) return;
            graphFilter.ext = document.getElementById('file-type-filter').value;
            graphFilter.query = document.getElementById('node-search').value.toLowerCase();
            document.getElementById('node-info').style.display = "none";
            selectedNodeId = null;
            sigmaInstance.refresh();
        }

        const EXT_COLORS = {
            '.py': '#3572A5', '.js': '#F7DF1E', '.ts': '#3178C6', '.tsx': '#3178C6',
            '.jsx': '#61DAFB', '.md': '#083FA1', '.html': '#E34F26', '.css': '#563D7C',
            '.json': '#292929', '.txt': '#888888', '.yaml': '#6CB4EE', '.yml': '#6CB4EE',
            '.go': '#00ADD8', '.rs': '#DEA584', '.cpp': '#F34B7D', '.c': '#555555',
            '.java': '#ED8B00', '.sql': '#E38C00',
            'entity': '#b388ff', 'memory': '#00e5ff',
        };
        const EXT_NAMES = { '.py': 'Python', '.js': 'JavaScript', '.ts': 'TypeScript', '.tsx': 'TSX',
            '.jsx': 'JSX', '.md': 'Markdown', '.html': 'HTML', '.css': 'CSS',
            '.json': 'JSON', '.yaml': 'YAML', '.yml': 'YAML', '.go': 'Go',
            '.rs': 'Rust', '.cpp': 'C++', '.c': 'C', '.java': 'Java', '.sql': 'SQL',
            'entity': 'Entity', 'memory': 'Memory',
        };

        async function openGraphModal(collectionName) {
            isModalOpen = true;
            document.getElementById('graph-modal').style.display = "block";
            document.getElementById('modal-title').innerText = `Loading Vectors for ${collectionName}...`;
            document.getElementById('node-info').style.display = "none";
            document.getElementById('graph-container').innerHTML = '';
            document.getElementById('filter-container').style.display = "none";
            document.getElementById('node-search').value = '';
            selectedNodeId = null;
            graphFilter.ext = 'ALL';
            graphFilter.query = '';

            allNodes = [];
            allLinks = [];

            try {
                const res = await fetch(`/api/dashboard/qdrant/${collectionName}/vectors`);
                const data = await res.json();

                if(!data.points || data.points.length === 0) {
                    document.getElementById('modal-title').innerText = `${collectionName} - No Vectors Found`;
                    return;
                }

                document.getElementById('modal-title').innerText = `${collectionName} — Graph Network (${data.points.length} vectors)`;

                const extSet = new Set();

                data.points.forEach(p => {
                    let ext = "Unknown";
                    const potentialFields = [p.payload.file_path, p.payload.source, p.payload.url];
                    for (let field of potentialFields) {
                        if (typeof field === 'string' && field.includes('.')) {
                            const parts = field.split('?')[0].split('#')[0].split('.');
                            ext = "." + parts[parts.length - 1].toLowerCase();
                            if (ext.length > 6 || !/^\.[a-z0-9]+$/.test(ext)) {
                                ext = "Unknown";
                            } else {
                                break;
                            }
                        }
                    }
                    if (ext === "Unknown" && p.payload) {
                         const payloadStr = JSON.stringify(p.payload);
                         const match = payloadStr.match(/[\w-]+\.(js|py|tsx|ts|jsx|md|html|css|txt|json|yaml|yml|go|rs|cpp|c|java|sql)\b/i);
                         if(match) ext = "." + match[1].toLowerCase();
                    }

                    extSet.add(ext);

                    allNodes.push({
                        id: p.id,
                        payload: p.payload || {},
                        ext: ext,
                    });
                });

                const filterSelect = document.getElementById('file-type-filter');
                filterSelect.innerHTML = '<option value="ALL">All Files</option>';
                const sortedExts = Array.from(extSet).sort();
                sortedExts.forEach(e => {
                    const opt = document.createElement('option');
                    opt.value = e;
                    opt.innerText = EXT_NAMES[e] || e.toUpperCase();
                    filterSelect.appendChild(opt);
                });

                if (sortedExts.length > 1 || allNodes.length > 0) {
                    document.getElementById('filter-container').style.display = "flex";
                }

                allLinks = data.links || [];

                // Compute node degree (connection count)
                const degree = {};
                allLinks.forEach(l => {
                    const sId = l.source?.id || l.source;
                    const tId = l.target?.id || l.target;
                    degree[sId] = (degree[sId] || 0) + 1;
                    degree[tId] = (degree[tId] || 0) + 1;
                });
                const maxDeg = Math.max(1, ...Object.values(degree));
                const nodeCount = allNodes.length;

                // Build legend
                const legendEl = document.getElementById('graph-legend');
                legendEl.style.display = 'flex';
                legendEl.innerHTML = '';
                sortedExts.forEach(ext => {
                    const row = document.createElement('div');
                    row.className = 'legend-row';
                    const dot = document.createElement('span');
                    dot.className = 'legend-dot';
                    dot.style.background = EXT_COLORS[ext] || '#888';
                    row.appendChild(dot);
                    row.appendChild(document.createTextNode(EXT_NAMES[ext] || ext));
                    legendEl.appendChild(row);
                });

                // --- Build graphology graph ---
                const sigmaGraph = new window.__graphology();

                data.points.forEach(p => {
                    const ext = allNodes.find(n => n.id === p.id)?.ext || 'Unknown';
                    const size = Math.max(3, Math.min(20, 1 + (degree[p.id] || 0) / maxDeg * 4));
                    sigmaGraph.addNode(p.id, {
                        label: `${EXT_NAMES[ext] || ext} — ${degree[p.id] || 0} connections`,
                        x: Math.random() * 200 - 100,
                        y: Math.random() * 200 - 100,
                        size: nodeCount > 500 ? size * 0.6 : size,
                        color: EXT_COLORS[ext] || '#888888',
                        ext: ext,
                        payload: p.payload || {},
                    });
                });

                data.links.forEach(l => {
                    const source = l.source?.id || l.source;
                    const target = l.target?.id || l.target;
                    if (sigmaGraph.hasNode(source) && sigmaGraph.hasNode(target)) {
                        const width = Math.max(0.3, (l.similarity - 0.35) * 8);
                        const alpha = Math.max(0.1, (l.similarity - 0.35) * 2);
                        sigmaGraph.addEdge(source, target, {
                            color: `rgba(0, 255, 204, ${alpha})`,
                            size: width,
                        });
                    }
                });

                // Warm-up with synchronous FA2
                window.__fa2.assign(sigmaGraph, {
                    iterations: nodeCount > 500 ? 80 : 50,
                    settings: {
                        barnesHutOptimize: nodeCount > 500,
                        gravity: 0.5,
                        scalingRatio: nodeCount > 500 ? 5 : 2,
                    },
                });

                // Continuous FA2 in Web Worker
                const layout = new window.__fa2Worker(sigmaGraph, {
                    settings: {
                        barnesHutOptimize: nodeCount > 500,
                        gravity: 0.5,
                        scalingRatio: nodeCount > 500 ? 5 : 2,
                    },
                });
                layout.start();
                fa2Layout = layout;

                // Create sigma renderer
                const renderer = new window.__sigma(sigmaGraph, document.getElementById('graph-container'), {
                    renderEdgeLabels: false,
                    enableEdgeEvents: true,
                    labelRenderedSizeThreshold: 6,
                    labelDensity: 0.3,
                    minCameraRatio: 0.05,
                    maxCameraRatio: 10,
                    nodeReducer: (node, data) => {
                        if (graphFilter.ext !== 'ALL' && data.ext !== graphFilter.ext) {
                            return { ...data, hidden: true };
                        }
                        if (graphFilter.query) {
                            const payload = data.payload || {};
                            const txt = (payload.text || payload.data || JSON.stringify(payload)).toLowerCase();
                            if (!txt.includes(graphFilter.query) && !String(node).toLowerCase().includes(graphFilter.query)) {
                                return { ...data, hidden: true };
                            }
                        }
                        if (node === selectedNodeId) {
                            return { ...data, color: '#ff00ff', size: data.size * 1.5 };
                        }
                        return data;
                    },
                    edgeReducer: (edge, data) => {
                        const [src, tgt] = sigmaGraph.extremities(edge);
                        const srcAttrs = sigmaGraph.getNodeAttributes(src);
                        const tgtAttrs = sigmaGraph.getNodeAttributes(tgt);
                        if (srcAttrs.hidden || tgtAttrs.hidden) return { ...data, hidden: true };
                        return data;
                    },
                });

                sigmaInstance = renderer;

                // Node click
                renderer.on('clickNode', ({ node }) => {
                    selectedNodeId = node;
                    const attrs = sigmaGraph.getNodeAttributes(node);

                    const infoBox = document.getElementById('node-info');
                    const contentBox = document.getElementById('node-content');
                    infoBox.style.display = "block";

                    let htmlContent = `<div class="property-row"><div class="property-label">Vector ID</div><div class="property-value">${escapeHtml(node)}</div></div>`;

                    if(attrs.payload.text || attrs.payload.data) {
                        const mainText = attrs.payload.text || attrs.payload.data;
                        htmlContent += `<div class="property-row"><div class="property-label">Primary Text</div><div class="property-value"><pre><code class="language-javascript" style="white-space: pre-wrap; padding: 8px; border-radius: 4px;">${escapeHtml(mainText)}</code></pre></div></div>`;
                    }

                    const payloadStr = JSON.stringify(attrs.payload, null, 2);
                    htmlContent += `<div class="property-row"><div class="property-label">Raw Payload (JSON)</div><pre><code class="language-json" style="padding: 8px; border-radius: 4px;">${escapeHtml(payloadStr)}</code></pre></div>`;

                    contentBox.innerHTML = htmlContent;
                    contentBox.querySelectorAll('pre code').forEach((block) => {
                        hljs.highlightElement(block);
                    });

                    renderer.refresh();
                });

                renderer.on('enterNode', ({ node }) => {
                    document.getElementById('graph-container').style.cursor = 'pointer';
                });

                renderer.on('leaveNode', () => {
                    document.getElementById('graph-container').style.cursor = '';
                });

                renderer.on('clickStage', () => {
                    selectedNodeId = null;
                    document.getElementById('node-info').style.display = "none";
                    renderer.refresh();
                });

                // Fit view
                setTimeout(() => {
                    renderer.getCamera().animatedReset({ duration: 400 });
                }, 200);

            } catch(e) {
                console.error(e);
                document.getElementById('modal-title').innerText = "Error Loading Graph";
            }
        }

        function closeModal() {
            document.getElementById('graph-modal').style.display = "none";
            document.getElementById('node-info').style.display = "none";
            document.getElementById('graph-legend').style.display = "none";
            if(fa2Layout) {
                fa2Layout.kill();
                fa2Layout = null;
            }
            if(sigmaInstance) {
                sigmaInstance.kill();
                sigmaInstance = null;
            }
            document.getElementById('graph-container').innerHTML = '';
            isModalOpen = false;
        }

        async function openMemoryGraphModal() {
            isModalOpen = true;
            document.getElementById('graph-modal').style.display = "block";
            document.getElementById('modal-title').innerText = 'Loading Memory Graph...';
            document.getElementById('node-info').style.display = "none";
            document.getElementById('graph-container').innerHTML = '';
            document.getElementById('filter-container').style.display = "none";
            document.getElementById('node-search').value = '';

            allNodes = [];
            allLinks = [];

            try {
                const res = await fetch('/api/dashboard/graph/memory');
                const data = await res.json();

                const points = data.points || [];
                const links = data.links || [];

                if (points.length === 0) {
                    document.getElementById('modal-title').innerText = 'Memory Graph — no entity links yet. Run /api/graph/reindex first.';
                    return;
                }

                const msg = data.memory_count ? ` (${data.entity_count} entities ↔ ${data.memory_count} memories)` : '';
                document.getElementById('modal-title').innerText = `Memory Entity Graph${msg} (${points.length} nodes, ${links.length} links)`;

                allNodes = points;
                allLinks = links;

                // Build legend
                const legendEl = document.getElementById('graph-legend');
                legendEl.style.display = 'flex';
                legendEl.innerHTML = `
                    <div class="legend-row"><span class="legend-dot" style="background:#b388ff"></span> Entity</div>
                    <div class="legend-row"><span class="legend-dot" style="background:#00e5ff"></span> Memory</div>
                `;

                // Compute degree
                const degree = {};
                allLinks.forEach(l => {
                    const sId = l.source?.id || l.source;
                    const tId = l.target?.id || l.target;
                    degree[sId] = (degree[sId] || 0) + 1;
                    degree[tId] = (degree[tId] || 0) + 1;
                });
                const maxDeg = Math.max(1, ...Object.values(degree));
                const nodeCount = allNodes.length;

                // --- Build graphology graph ---
                const sigmaGraph = new window.__graphology();

                allNodes.forEach(p => {
                    const group = p.group || 'memory';
                    const isEntity = group === 'entity';
                    const size = isEntity
                        ? Math.max(5, Math.min(25, 2 + (degree[p.id] || 0) / maxDeg * 6))
                        : Math.max(3, Math.min(15, 1 + (degree[p.id] || 0) / maxDeg * 4));
                    sigmaGraph.addNode(p.id, {
                        label: isEntity
                            ? `Entity: ${p.payload.entity_name} (${degree[p.id] || 0} connections)`
                            : `Memory (${degree[p.id] || 0} connections)`,
                        x: Math.random() * 200 - 100,
                        y: Math.random() * 200 - 100,
                        size: nodeCount > 500 ? size * 0.7 : size,
                        color: isEntity ? '#b388ff' : '#00e5ff',
                        group: group,
                        payload: p.payload || {},
                    });
                });

                allLinks.forEach(l => {
                    const source = l.source?.id || l.source;
                    const target = l.target?.id || l.target;
                    if (sigmaGraph.hasNode(source) && sigmaGraph.hasNode(target)) {
                        sigmaGraph.addEdge(source, target, {
                            color: 'rgba(0, 255, 204, 0.3)',
                            size: 1,
                        });
                    }
                });

                // Warm-up with synchronous FA2
                window.__fa2.assign(sigmaGraph, {
                    iterations: nodeCount > 500 ? 80 : 50,
                    settings: {
                        barnesHutOptimize: nodeCount > 500,
                        gravity: 0.5,
                        scalingRatio: nodeCount > 500 ? 5 : 2,
                    },
                });

                // Continuous FA2 in Web Worker
                const layout = new window.__fa2Worker(sigmaGraph, {
                    settings: {
                        barnesHutOptimize: nodeCount > 500,
                        gravity: 0.5,
                        scalingRatio: nodeCount > 500 ? 5 : 2,
                    },
                });
                layout.start();
                fa2Layout = layout;

                // Create sigma renderer
                const renderer = new window.__sigma(sigmaGraph, document.getElementById('graph-container'), {
                    renderEdgeLabels: false,
                    enableEdgeEvents: true,
                    labelRenderedSizeThreshold: 6,
                    labelDensity: 0.3,
                    minCameraRatio: 0.05,
                    maxCameraRatio: 10,
                    nodeReducer: (node, data) => {
                        if (node === selectedNodeId) {
                            return { ...data, color: '#ff00ff', size: data.size * 1.5 };
                        }
                        return data;
                    },
                });

                sigmaInstance = renderer;

                // Node click
                renderer.on('clickNode', ({ node }) => {
                    selectedNodeId = node;
                    const attrs = sigmaGraph.getNodeAttributes(node);
                    const isEntity = attrs.group === 'entity';

                    const infoBox = document.getElementById('node-info');
                    const contentBox = document.getElementById('node-content');
                    infoBox.style.display = "block";

                    let htmlContent = `<div class="property-row"><div class="property-label">Type</div><div class="property-value">${isEntity ? '🔮 Entity' : '🧠 Memory'}</div></div>`;

                    if (isEntity) {
                        htmlContent += `<div class="property-row"><div class="property-label">Entity Name</div><div class="property-value">${escapeHtml(attrs.payload.entity_name)}</div></div>`;
                        htmlContent += `<div class="property-row"><div class="property-label">Connected Memories</div><div class="property-value">${attrs.payload.connected_memories || 0}</div></div>`;
                        if (attrs.payload.entity_type) {
                            htmlContent += `<div class="property-row"><div class="property-label">Entity Type</div><div class="property-value">${escapeHtml(attrs.payload.entity_type)}</div></div>`;
                        }
                    } else {
                        const memText = attrs.payload.memory || '';
                        htmlContent += `<div class="property-row"><div class="property-label">Memory Excerpt</div><div class="property-value"><pre><code class="language-javascript" style="white-space: pre-wrap; padding: 8px; border-radius: 4px;">${escapeHtml(memText)}</code></pre></div></div>`;
                        if (attrs.payload.entity_count) {
                            htmlContent += `<div class="property-row"><div class="property-label">Connected Entities</div><div class="property-value">${attrs.payload.entity_count}</div></div>`;
                        }
                    }

                    htmlContent += `<div class="property-row"><div class="property-label">Node ID</div><div class="property-value" style="font-size:0.7rem;color:var(--text-muted);">${escapeHtml(node)}</div></div>`;
                    contentBox.innerHTML = htmlContent;

                    contentBox.querySelectorAll('pre code').forEach((block) => {
                        hljs.highlightElement(block);
                    });

                    renderer.refresh();
                });

                renderer.on('enterNode', ({ node }) => {
                    document.getElementById('graph-container').style.cursor = 'pointer';
                });

                renderer.on('leaveNode', () => {
                    document.getElementById('graph-container').style.cursor = '';
                });

                renderer.on('clickStage', () => {
                    selectedNodeId = null;
                    document.getElementById('node-info').style.display = "none";
                    renderer.refresh();
                });

                // Fit view
                setTimeout(() => {
                    renderer.getCamera().animatedReset({ duration: 400 });
                }, 200);

            } catch(e) {
                console.error(e);
                document.getElementById('modal-title').innerText = "Error Loading Memory Graph";
            }
        }

        let tempChart, vramChart, utilChart, ramChart, cpuChart, cpuTempChart, tokPerSecChart;

        function createLineChart(canvasId, color, fillColor, yMin, yMax) {
            const ctx = document.getElementById(canvasId).getContext('2d');
            return new Chart(ctx, {
                type: 'line',
                data: { labels: [], datasets: [{ data: [], borderColor: color, backgroundColor: fillColor, fill: true }] },
                options: {
                    responsive: true, maintainAspectRatio: false,
                    animation: false, spanGaps: true,
                    plugins: { legend: { display: false } },
                    scales: {
                        x: { display: false, grid: { display: false } },
                        y: {
                            min: yMin, max: yMax,
                            grid: { color: 'rgba(255,255,255,0.06)' },
                            ticks: { color: '#94a3b8', font: { size: 8, family: "'JetBrains Mono', monospace" }, maxTicksLimit: 4 }
                        }
                    },
                    elements: {
                        point: { radius: 0, hitRadius: 2 },
                        line: { borderWidth: 1.5, tension: 0.3 }
                    }
                }
            });
        }

        function initCharts() {
            tempChart = createLineChart('chart-temp', '#00b8ff', 'rgba(0,184,255,0.08)', 30, 100);
            vramChart = createLineChart('chart-vram', '#00ffcc', 'rgba(0,255,204,0.08)', 0, 100);
            utilChart = createLineChart('chart-util', '#7b2cbf', 'rgba(123,44,191,0.08)', 0, 100);
            ramChart = createLineChart('chart-ram', '#ffcc00', 'rgba(255,204,0,0.08)', 0, 100);
            cpuChart = createLineChart('chart-cpu', '#ff8833', 'rgba(255,136,51,0.08)', 0, 100);
            cpuTempChart = createLineChart('chart-cpu-temp', '#ff3366', 'rgba(255,51,102,0.08)', 20, 110);
            tokPerSecChart = createLineChart('chart-tok-per-sec', '#00ffcc', 'rgba(0,255,204,0.08)', 0, 20);
        }

        function updateCharts(history) {
            if (!history || history.length === 0) return;
            const len = history.length;
            const labels = history.map(() => '');
            const temps = history.map(h => h.temp);
            const vramPcts = history.map(h => h.vram_total ? Math.round(h.vram_used / h.vram_total * 100) : 0);
            const utils = history.map(h => h.util ?? 0);

            const last = history[len-1];
            document.getElementById('chart-current-temp').innerText = last.temp + '°C';
            document.getElementById('chart-current-vram').innerText = last.vram_used + ' MiB';
            document.getElementById('chart-current-util').innerText = (last.util ?? 0) + '%';

            tempChart.data.labels = labels;
            tempChart.data.datasets[0].data = temps;
            tempChart.update('none');

            vramChart.data.labels = labels;
            vramChart.data.datasets[0].data = vramPcts;
            vramChart.update('none');

            utilChart.data.labels = labels;
            utilChart.data.datasets[0].data = utils;
            utilChart.update('none');
        }

        function updateSysCharts(sysHistory) {
            if (!sysHistory || sysHistory.length === 0) return;
            const labels = sysHistory.map(() => '');
            ramChart.data.labels = labels;
            ramChart.data.datasets[0].data = sysHistory.map(h => h.ram_pct);
            ramChart.update('none');
            cpuChart.data.labels = labels;
            cpuChart.data.datasets[0].data = sysHistory.map(h => h.cpu_pct);
            cpuChart.update('none');
            cpuTempChart.data.labels = labels;
            cpuTempChart.data.datasets[0].data = sysHistory.map(h => h.cpu_temp);
            cpuTempChart.update('none');
            const last = sysHistory[sysHistory.length-1];
            document.getElementById('chart-current-ram').innerText = last.ram_pct + '%';
            document.getElementById('chart-current-cpu').innerText = last.cpu_pct + '%';
            document.getElementById('chart-current-cpu-temp').innerText = (last.cpu_temp ?? '--') + '°C';
        }

        function updateInfCharts(infHistory) {
            if (!infHistory || infHistory.length === 0) return;
            const labels = infHistory.map(() => '');
            tokPerSecChart.data.labels = labels;
            tokPerSecChart.data.datasets[0].data = infHistory.map(h => h.tokens_per_sec);
            tokPerSecChart.update('none');
            const last = infHistory[infHistory.length-1];
            document.getElementById('chart-current-tok-per-sec').innerText = (last.tokens_per_sec ?? 0) + ' tok/s';
        }

        fetchStats = async function() {
            if (isModalOpen) return;
            try {
                const res = await fetch('/api/dashboard/stats');
                const data = await res.json();

                if (data.gpu) {
                    const g = data.gpu;
                    document.getElementById('gpu-temp').innerText = g.temp ?? '--';
                    const tempInfo = calcGpuTempColor(g.temp);
                    const tempPct = Math.min(100, ((g.temp ?? 0) / 100) * 100);
                    const tempBar = document.getElementById('gpu-temp-bar');
                    tempBar.style.width = tempPct + '%';
                    tempBar.className = 'progress-fill ' + tempInfo.class;

                    const vramUsed = (g.vram_used ?? 0);
                    const vramTotal = (g.vram_total ?? 1);
                    const vramPct = (vramUsed / vramTotal) * 100;
                    document.getElementById('gpu-vram-used').innerText = vramUsed + 'MiB';
                    document.getElementById('gpu-vram-total').innerText = vramTotal + 'MiB';
                    document.getElementById('gpu-util').innerText = (g.util ?? 0);
                    const vramBar = document.getElementById('gpu-vram-bar');
                    vramBar.style.width = Math.min(100, vramPct) + '%';
                    vramBar.className = 'progress-fill ' + calcVramColor(vramPct);

                    document.getElementById('health-cuda').innerText = g.cuda_version || 'N/A';
                    document.getElementById('health-cuda').className = g.cuda_version ? 'badge badge-primary' : 'badge badge-danger';

                    if (g.processes) {
                        document.getElementById('gpu-proc-text').innerText = g.processes;
                    }
                }

                if (data.gpu_history) {
                    updateCharts(data.gpu_history);
                }

                if (data.features) {
                    const f = data.features;
                    const container = document.getElementById('features-list');
                    container.innerHTML = '';
                    const labels = {
                        llm: 'LLM Engine', embeddings: 'Embeddings', rag: 'RAG (Qdrant)',
                        memory: 'Memory (mem0)', ast_parser: 'AST Parser',
                        file_watcher: 'File Watcher', telegram: 'Telegram Bot',
                        cron: 'Cron Scheduler', searxng: 'SearXNG',
                        crawl4ai: 'Crawl4AI', whisper: 'Voice I/O', userbots: 'Userbots'
                    };
                    for (const [key, label] of Object.entries(labels)) {
                        const active = f[key];
                        const div = document.createElement('div');
                        div.style.cssText = 'font-size:0.7rem;display:flex;align-items:center;gap:4px;';
                        div.innerHTML = `<span style="color:${active ? 'var(--primary)' : 'var(--text-muted)'};">${active ? '✓' : '○'}</span> ${label}`;
                        container.appendChild(div);
                    }
                }

                if (data.rag_stats) {
                    document.getElementById('indexed-files').innerText = data.rag_stats.indexed_files ?? 0;
                    document.getElementById('pending-queue').innerText = data.rag_stats.pending_events ?? 0;
                    document.getElementById('total-chunks').innerText = data.rag_stats.total_chunks ?? 0;
                }

                if (data.models) {
                    const chatName = data.models.chat_model || 'N/A';
                    const embedName = data.models.embed_model || 'N/A';
                    document.getElementById('model-chat-side').innerText = chatName.split('/').pop();
                    document.getElementById('model-embed-side').innerText = embedName.split('/').pop();
                    const sideList = document.getElementById('model-details-side');
                    sideList.innerHTML = '';
                    if (data.models.details) {
                        data.models.details.forEach(d => {
                            const li = document.createElement('li');
                            li.style.fontSize = '0.7rem';
                            li.innerHTML = `<span>${d.label}</span> <span class="badge badge-accent">${d.value}</span>`;
                            sideList.appendChild(li);
                        });
                    } else {
                        sideList.innerHTML = '<li style="color: var(--text-muted);font-size:0.7rem;">No model loaded</li>';
                    }
                }

                if (data.inference) {
                    document.getElementById('inf-requests').innerText = data.inference.total_requests ?? 0;
                    document.getElementById('inf-tokens').innerText = data.inference.total_completion_tokens ?? 0;
                    document.getElementById('inf-prompt-tokens').innerText = data.inference.total_prompt_tokens ?? 0;
                }
                if (data.inference_history && data.inference_history.length > 0) {
                    const lastInf = data.inference_history[data.inference_history.length-1];
                    document.getElementById('inf-tok-per-sec').innerText = lastInf.tokens_per_sec ?? 0;
                }

                if (data.sys_history) {
                    updateSysCharts(data.sys_history);
                }
                if (data.inference_history) {
                    updateInfCharts(data.inference_history);
                }

                const qList = document.getElementById('qdrant-list');
                qList.innerHTML = '';
                if(data.qdrant_collections && data.qdrant_collections.length > 0) {
                    data.qdrant_collections.forEach(col => {
                        const li = document.createElement('li');
                        const name = typeof col === 'string' ? col : col.name;
                        const points = typeof col === 'string' ? '' : (col.points ?? '');
                        li.innerHTML = `
                            <span>${name}${points ? ' <span style="color: var(--text-muted); font-size: 0.7rem;">('+points+' pts)</span>' : ''}</span>
                            <button class="btn" onclick="openGraphModal('${name}')">Graph</button>
                        `;
                        qList.appendChild(li);
                    });
                } else {
                    qList.innerHTML = '<li style="color: var(--text-muted);">No collections</li>';
                }

                if (data.agent_stats) {
                    document.getElementById('active-cron').innerText = data.agent_stats.active_crons ?? 0;
                    document.getElementById('active-todos').innerText = data.agent_stats.active_todos ?? 0;
                    document.getElementById('allowed-users').innerText = data.agent_stats.allowed_users ?? 0;
                    document.getElementById('async-tasks').innerText = data.agent_stats.async_tasks ?? 0;
                }

                if (data.health) {
                    const setHealth = (id, isUp) => {
                        const el = document.getElementById(id);
                        if(isUp) { el.innerText = "ONLINE"; el.className = "badge badge-primary"; }
                        else { el.innerText = "OFFLINE"; el.className = "badge badge-danger"; }
                    };
                    setHealth('health-searxng', data.health.searxng);
                    setHealth('health-crawl4ai', data.health.crawl4ai);
                    setHealth('health-qdrant', data.health.qdrant);
                }

                if (data.sys_stats) {
                    document.getElementById('sys-ram').innerText = data.sys_stats.ram_mb + " MB";
                    document.getElementById('sys-uptime').innerText = data.sys_stats.uptime || '--';
                    document.getElementById('sys-load').innerText = data.sys_stats.load || '--';
                    document.getElementById('sys-disk').innerText = data.sys_stats.disk || '--';
                }

            } catch (err) {
                console.error('Failed to fetch telemetry', err);
            }
        };

        setInterval(fetchStats, 3000);
        initCharts();
        fetchStats();

        // ================================================================
        // LOG VIEWER
        // ================================================================
        let logInterval = null;

        function openLogModal() {
            document.getElementById('log-modal').style.display = 'block';
            loadContainers();
            fetchLogs();
            if (document.getElementById('log-auto-refresh').checked) {
                logInterval = setInterval(fetchLogs, 5000);
            }
        }

        function closeLogModal() {
            document.getElementById('log-modal').style.display = 'none';
            if (logInterval) { clearInterval(logInterval); logInterval = null; }
        }

        function toggleAutoRefresh() {
            if (logInterval) { clearInterval(logInterval); logInterval = null; }
            if (document.getElementById('log-auto-refresh').checked) {
                logInterval = setInterval(fetchLogs, 5000);
            }
        }

        async function loadContainers() {
            try {
                const res = await fetch('/api/dashboard/containers');
                const data = await res.json();
                const select = document.getElementById('log-container-select');
                const currentVal = select.value;
                select.innerHTML = '<option value="all">All Containers</option>';
                (data.containers || []).forEach(c => {
                    const opt = document.createElement('option');
                    opt.value = c.name;
                    opt.textContent = c.name + ' (' + (c.status || c.state) + ')';
                    select.appendChild(opt);
                });
                if (currentVal && [...select.options].some(o => o.value === currentVal)) {
                    select.value = currentVal;
                }
            } catch(e) {
                console.error('Failed to load containers', e);
            }
        }

        async function fetchLogs() {
            const container = document.getElementById('log-container-select').value;
            const display = document.getElementById('log-display');
            try {
                const res = await fetch(`/api/dashboard/containers/${encodeURIComponent(container)}/logs?tail=500`);
                const data = await res.json();
                if (data.logs) {
                    display.textContent = data.logs.map(l => `[${l.container}] ${l.message}`).join('\n');
                    display.scrollTop = display.scrollHeight;
                } else if (data.error) {
                    display.textContent = 'Error: ' + data.error;
                }
            } catch(e) {
                display.textContent = 'Failed to fetch logs: ' + (e.message || e);
            }
        }

        async function restartContainer(name) {
            if (!confirm('Restart container "' + name + '"?')) return;
            try {
                const res = await fetch(`/api/dashboard/containers/${encodeURIComponent(name)}/restart`, { method: 'POST' });
                const data = await res.json();
                if (data.status === 'restarting') {
                    setTimeout(fetchStats, 3000);
                }
            } catch(e) {
                console.error('Failed to restart', name, e);
            }
        }

        async function restartIngestion() {
            if (!confirm('Restart document ingestion?')) return;
            try {
                await fetch('/api/dashboard/ingestion/restart', { method: 'POST' });
            } catch(e) {
                console.error('Failed to restart ingestion', e);
            }
        }
    </script>
</body>
</html>
"""


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
        r = await state.http_client.get("http://searxng:8080", timeout=1.0)
        searxng_up = (r.status_code < 500)
    except Exception:
        pass

    crawl4ai_up = False
    try:
        r = await state.http_client.get("http://crawl4ai_server:11235", timeout=1.0)
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
    """Grafo bipartito: nodi entità ↔ nodi memoria dall'entity store di Mem0.

    Scansiona la entity store (``collateral_memories_v3_entities``) e la
    collection delle memorie (``collateral_memories_v3``), poi costruisce
    un grafo dove:
      - I nodi entità (colore viola) sono le entità estratte via spaCy
      - I nodi memoria (colore ciano) sono i ricordi episodici
      - I link connettono ogni entità alle memorie che la contengono
        (basati su ``linked_memory_ids`` nell'entity store)
    """
    nodes = []
    links = []
    memory_lookup = {}  # memory_id → memory text
    entity_lookup = {}  # entity_name → metadata

    # 1) Fetch memories from the memory collection
    try:
        res_pts = await state.http_client.post(
            f"http://{QDRANT_HOST}:6333/collections/{MEMORY_COLLECTION}/points/scroll",
            json={"limit": 1000, "with_payload": True, "with_vector": False,
                  "filter": {"must": [{"key": "user_id", "match": {"value": user_id}}]}},
            timeout=5.0
        )
        if res_pts.status_code == 200:
            points = res_pts.json().get("result", {}).get("points", [])
            for p in points:
                pid = p.get("id", "")
                payload = p.get("payload", {}) or {}
                mem_text = payload.get("memory", "") or ""
                memory_lookup[pid] = mem_text
    except Exception as e:
        pass

    # 2) Fetch entities from the entity store
    try:
        res_ent = await state.http_client.post(
            f"http://{QDRANT_HOST}:6333/collections/{ENTITY_COLLECTION}/points/scroll",
            json={"limit": 1000, "with_payload": True, "with_vector": False},
            timeout=5.0
        )
        if res_ent.status_code == 200:
            points = res_ent.json().get("result", {}).get("points", [])
            for p in points:
                payload = p.get("payload", {}) or {}
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
    added_entity_ids = set()

    for ent_name, ent_meta in entity_lookup.items():
        linked_ids = ent_meta["linked_memory_ids"]
        if not linked_ids:
            continue

        # Filter linked IDs to only those that exist in memory_lookup
        valid_linked = [lid for lid in linked_ids if lid in memory_lookup]
        if not valid_linked:
            continue

        # Entity node ID (prefixed to avoid collisions)
        ent_node_id = f"ent_{ent_name}"
        added_entity_ids.add(ent_node_id)

        # Count how many memories this entity connects to
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

        # Links: entity → each linked memory
        for mem_id in valid_linked:
            mem_text = memory_lookup.get(mem_id, "")
            # Truncate for display
            mem_excerpt = mem_text[:120] + "…" if len(mem_text) > 120 else mem_text
            links.append({
                "source": ent_node_id,
                "target": mem_id,
                "similarity": 0.9,  # fixed high weight for entity links
            })

    # 4) Add memory nodes that are connected to at least one entity
    connected_memory_ids = set()
    for link in links:
        connected_memory_ids.add(link["target"])

    for mem_id, mem_text in memory_lookup.items():
        if mem_id not in connected_memory_ids:
            continue
        mem_excerpt = mem_text[:150] + "…" if len(mem_text) > 150 else mem_text
        nodes.append({
            "id": mem_id,
            "payload": {
                "memory": mem_excerpt,
                "entity_count": sum(
                    1 for lnk in links if lnk["target"] == mem_id
                ),
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


@dashboard_router.post("/api/dashboard/ingestion/restart")
async def restart_ingestion():
    from rag import ingest_local_documents
    state.is_reindexing = True
    task = asyncio.create_task(ingest_local_documents())
    state.background_tasks.add(task)
    task.add_done_callback(state.background_tasks.discard)
    return JSONResponse({"status": "success", "message": "Document ingestion re-started"})
