import os
import sys
from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse
from config import OLLAMA_BASE, QDRANT_HOST, ALLOWED_USERS
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
    <script src="https://unpkg.com/force-graph"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.8.0/styles/atom-one-dark.min.css">
    <script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.8.0/highlight.min.js"></script>
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
            display: none; position: fixed; z-index: 1000; left: 0; top: 0;
            width: 100%; height: 100%; background-color: rgba(5, 7, 10, 0.95);
            backdrop-filter: blur(10px);
        }
        .modal-header {
            position: absolute; top: 0; left: 0; width: 100%; padding: 20px 40px;
            display: flex; justify-content: space-between; align-items: center;
            background: linear-gradient(to bottom, rgba(0,0,0,0.8), transparent);
            z-index: 1010;
        }
        .modal-header h2 { margin: 0; font-size: 1.3rem; font-weight: 600; display: flex; align-items: center; gap: 15px;}
        .close-modal { color: #fff; font-size: 32px; cursor: pointer; transition: 0.2s; line-height: 1;}
        .close-modal:hover { color: var(--danger); transform: scale(1.1);}
        
        #graph-container { width: 100vw; height: 100vh; }
        #filter-container { display: none; align-items: center; gap: 12px; background: rgba(0,0,0,0.5); padding: 6px 14px; border-radius: 8px; border: 1px solid var(--glass-border); }
        #filter-container input, #filter-container select {
            background: rgba(0,0,0,0.8); color: #fff; padding: 4px 8px;
            border: 1px solid var(--glass-border); border-radius: 4px;
            font-family: 'JetBrains Mono', monospace; font-size: 0.75rem;
        }

        .node-info {
            position: absolute; bottom: 30px; left: 40px; width: 450px;
            max-height: 80vh; overflow-y: auto;
            background: rgba(15, 20, 30, 0.85); border: 1px solid var(--primary);
            padding: 20px; border-radius: 12px; backdrop-filter: blur(15px);
            display: none; z-index: 1010;
            box-shadow: 0 10px 40px rgba(0,0,0,0.5), 0 0 20px rgba(0,255,204,0.1);
        }
        .node-info h3 { margin: 0 0 15px 0; color: var(--primary); font-size: 1rem; border-bottom: 1px solid rgba(0,255,204,0.2); padding-bottom: 10px;}
        .node-info .property-row { margin-bottom: 12px; }
        .node-info .property-label { font-size: 0.7rem; color: var(--secondary); text-transform: uppercase; font-weight: 600; margin-bottom: 4px; }
        .node-info .property-value { font-size: 0.85rem; line-height: 1.4; color: #fff; word-break: break-word;}
        .node-info pre { margin: 0; font-family: 'JetBrains Mono', monospace; font-size: 0.75rem; border-radius: 8px; border: 1px solid rgba(255,255,255,0.1); }
        
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
            <div class="card-header" style="font-size: 0.85rem; margin-bottom: 12px;"><span class="dot dot-secondary"></span> Services</div>
            <ul class="data-list">
                <li><span>SearXNG</span> <span class="badge" id="health-searxng">...</span></li>
                <li><span>Crawl4AI</span> <span class="badge" id="health-crawl4ai">...</span></li>
                <li><span>Qdrant</span> <span class="badge" id="health-qdrant">...</span></li>
                <li><span>GPU (CUDA)</span> <span class="badge" id="health-cuda">...</span></li>
            </ul>
        </div>
    </div>

    <div class="main-content">
        <div class="grid-container">
            
            <div class="card fade-in">
                <div class="card-header"><span class="dot dot-primary pulsing"></span> Inference</div>
                <div class="metric-row">
                    <div class="metric">
                        <div class="val" id="inf-requests" style="color: var(--warning);">0</div>
                        <div class="label">Total Requests</div>
                    </div>
                    <div class="metric">
                        <div class="val" id="inf-tokens" style="color: #d8b4fe;">0</div>
                        <div class="label">Completion Tokens</div>
                    </div>
                    <div class="metric">
                        <div class="val" id="inf-prompt-tokens" style="color: var(--secondary);">0</div>
                        <div class="label">Prompt Tokens</div>
                    </div>
                </div>
            </div>

            <div class="card fade-in">
                <div class="card-header"><span class="dot dot-accent pulsing"></span> Neural Engine</div>
                <div class="metric-row">
                    <div class="metric">
                        <div class="val" id="model-chat" style="color: #d8b4fe; font-size: 1.1rem;">--</div>
                        <div class="label">Chat Model</div>
                    </div>
                    <div class="metric">
                        <div class="val" id="model-embed" style="color: var(--secondary); font-size: 1.1rem;">--</div>
                        <div class="label">Embed Model</div>
                    </div>
                </div>
                <ul class="data-list" id="model-details">
                    <li style="color: var(--text-muted);">Loading...</li>
                </ul>
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
                </div>
                <div class="metric-row">
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

            <div class="card fade-in">
                <div class="card-header"><span class="dot dot-accent"></span> GPU Processes</div>
                <div id="gpu-proc-list" style="max-height: 280px; overflow-y: auto;">
                    <pre id="gpu-proc-text" style="font-family: 'JetBrains Mono', monospace; font-size: 0.7rem; color: var(--text-muted); margin: 0; white-space: pre-wrap;">Loading...</pre>
                </div>
            </div>
        </div>
    </div>

    <div id="graph-modal" class="modal">
        <div class="modal-header">
            <h2><span class="dot dot-primary pulsing"></span> <span id="modal-title">Vector Network</span></h2>
            <div id="filter-container">
                <input type="text" id="node-search" placeholder="Search content..." onkeyup="applyGraphFilter()">
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
    </div>

    <script>
        let isModalOpen = false;
        let Graph = null;
        let currentSelectedNode = null;
        let allNodes = [];
        let allLinks = [];

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

        async function fetchStats() {
            if (isModalOpen) return; 
            try {
                const res = await fetch('/api/dashboard/stats');
                const data = await res.json();
                
                if (data.rag_stats) {
                    document.getElementById('indexed-files').innerText = data.rag_stats.indexed_files ?? 0;
                    document.getElementById('pending-queue').innerText = data.rag_stats.pending_events ?? 0;
                    document.getElementById('total-chunks').innerText = data.rag_stats.total_chunks ?? 0;
                }
                
                if (data.models) {
                    document.getElementById('model-chat').innerText = data.models.chat_model || 'N/A';
                    document.getElementById('model-embed').innerText = data.models.embed_model || 'N/A';
                    
                    const detailList = document.getElementById('model-details');
                    detailList.innerHTML = '';
                    if (data.models.details) {
                        data.models.details.forEach(d => {
                            const li = document.createElement('li');
                            li.innerHTML = `<span>${d.label}</span> <span class="badge badge-accent">${d.value}</span>`;
                            detailList.appendChild(li);
                        });
                    } else {
                        detailList.innerHTML = '<li style="color: var(--text-muted);">No model loaded</li>';
                    }
                }
                
                if (data.inference) {
                    document.getElementById('inf-requests').innerText = data.inference.total_requests ?? 0;
                    document.getElementById('inf-tokens').innerText = data.inference.total_completion_tokens ?? 0;
                    document.getElementById('inf-prompt-tokens').innerText = data.inference.total_prompt_tokens ?? 0;
                }
                
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
        }

        function applyGraphFilter() {
            if (!Graph) return;
            const selectedType = document.getElementById('file-type-filter').value;
            const query = document.getElementById('node-search').value.toLowerCase();
            
            let filteredNodes = allNodes;
            if (selectedType !== "ALL") {
                filteredNodes = allNodes.filter(n => n.ext === selectedType);
            }
            if (query.length > 0) {
                filteredNodes = filteredNodes.filter(n => {
                    const txt = (n.payload.text || n.payload.data || JSON.stringify(n.payload)).toLowerCase();
                    return txt.includes(query) || String(n.id).toLowerCase().includes(query);
                });
            }
            
            const nodeIds = new Set(filteredNodes.map(n => n.id));
            const filteredLinks = allLinks.filter(l => nodeIds.has(l.source.id || l.source) && nodeIds.has(l.target.id || l.target));
            
            Graph.graphData({ nodes: filteredNodes, links: filteredLinks });
            
            if (currentSelectedNode && !nodeIds.has(currentSelectedNode.id)) {
                document.getElementById('node-info').style.display = "none";
                currentSelectedNode = null;
            }
        }

        async function openGraphModal(collectionName) {
            isModalOpen = true;
            document.getElementById('graph-modal').style.display = "block";
            document.getElementById('modal-title').innerText = `Loading Vectors for ${collectionName}...`;
            document.getElementById('node-info').style.display = "none";
            document.getElementById('graph-container').innerHTML = '';
            document.getElementById('filter-container').style.display = "none";
            document.getElementById('node-search').value = '';
            
            allNodes = [];
            allLinks = [];

            try {
                const res = await fetch(`/api/dashboard/qdrant/${collectionName}/vectors`);
                const data = await res.json();
                
                if(!data.points || data.points.length === 0) {
                    document.getElementById('modal-title').innerText = `${collectionName} - No Vectors Found`;
                    return;
                }

                document.getElementById('modal-title').innerText = `${collectionName} - Graph Network`;

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
                         const match = payloadStr.match(/[\w-]+\.(js|py|tsx|ts|jsx|md|html|css|txt|json)\b/i);
                         if(match) ext = "." + match[1].toLowerCase();
                    }

                    extSet.add(ext);

                    allNodes.push({
                        id: p.id,
                        payload: p.payload || {},
                        ext: ext,
                        val: 1
                    });
                });

                const filterSelect = document.getElementById('file-type-filter');
                filterSelect.innerHTML = '<option value="ALL">All Files</option>';
                const sortedExts = Array.from(extSet).sort();
                sortedExts.forEach(e => {
                    const opt = document.createElement('option');
                    opt.value = e;
                    opt.innerText = e.toUpperCase();
                    filterSelect.appendChild(opt);
                });
                
                if (sortedExts.length > 1 || allNodes.length > 0) {
                    document.getElementById('filter-container').style.display = "flex";
                }

                allLinks = data.links || [];

                Graph = ForceGraph()(document.getElementById('graph-container'))
                    .backgroundColor('transparent')
                    .nodeRelSize(8)
                    .linkDirectionalParticles(link => link.similarity > 0.7 ? 2 : 0)
                    .linkDirectionalParticleSpeed(d => (d.similarity - 0.6) * 0.02)
                    .nodeColor(node => node === currentSelectedNode ? '#ff00ff' : '#00ffcc')
                    .linkColor(link => `rgba(123, 44, 191, ${Math.max(0.1, (link.similarity - 0.5) * 2)})`)
                    .linkWidth(link => Math.max(1, (link.similarity - 0.6) * 15))
                    .onNodeClick(node => {
                        currentSelectedNode = node;
                        Graph.nodeColor(Graph.nodeColor());

                        const infoBox = document.getElementById('node-info');
                        const contentBox = document.getElementById('node-content');
                        infoBox.style.display = "block";
                        
                        let htmlContent = `<div class="property-row"><div class="property-label">Vector ID</div><div class="property-value">${node.id}</div></div>`;
                        
                        function escapeHtml(unsafe) {
                            return String(unsafe)
                                 .replace(/&/g, "&amp;")
                                 .replace(/</g, "&lt;")
                                 .replace(/>/g, "&gt;")
                                 .replace(/"/g, "&quot;")
                                 .replace(/'/g, "&#039;");
                        }

                        if(node.payload.text || node.payload.data) {
                            const mainText = node.payload.text || node.payload.data;
                            htmlContent += `<div class="property-row"><div class="property-label">Primary Text</div><div class="property-value"><pre><code class="language-javascript" style="white-space: pre-wrap; padding: 10px; border-radius: 6px;">${escapeHtml(mainText)}</code></pre></div></div>`;
                        }
                        
                        const payloadStr = JSON.stringify(node.payload, null, 2);
                        htmlContent += `<div class="property-row"><div class="property-label">Raw Payload (JSON)</div><pre><code class="language-json" style="padding: 10px; border-radius: 6px;">${escapeHtml(payloadStr)}</code></pre></div>`;
                        
                        contentBox.innerHTML = htmlContent;
                        
                        contentBox.querySelectorAll('pre code').forEach((block) => {
                            hljs.highlightElement(block);
                        });
                    })
                    .onNodeHover(node => {
                        document.getElementById('graph-container').style.cursor = node ? 'pointer' : null;
                    })
                    .onBackgroundClick(() => {
                        currentSelectedNode = null;
                        Graph.nodeColor(Graph.nodeColor());
                        document.getElementById('node-info').style.display = "none";
                    })
                    .graphData({ nodes: allNodes, links: allLinks });

                setTimeout(() => {
                    Graph.zoomToFit(400);
                }, 500);

            } catch(e) {
                console.error(e);
                document.getElementById('modal-title').innerText = "Error Loading Graph";
            }
        }

        function closeModal() {
            document.getElementById('graph-modal').style.display = "none";
            document.getElementById('node-info').style.display = "none";
            if(Graph) {
                Graph._destructor();
                Graph = null;
            }
            document.getElementById('graph-container').innerHTML = '';
            isModalOpen = false;
        }

        setInterval(fetchStats, 3000);
        fetchStats();
    </script>
</body>
</html>
"""


@dashboard_router.get("/")
@dashboard_router.get("/dashboard")
async def get_dashboard():
    return HTMLResponse(HTML_CONTENT)


async def get_gpu_metrics():
    import subprocess
    result = {"temp": None, "vram_used": None, "vram_total": None, "util": None, "cuda_version": None, "processes": None}
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=temperature.gpu,memory.used,memory.total,utilization.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5
        )
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
        out2 = subprocess.run(
            ["nvidia-smi", "--query-gpu=driver_version",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3
        )
        if out2.returncode == 0:
            result["cuda_version"] = out2.stdout.strip()
    except Exception:
        pass

    try:
        out3 = subprocess.run(
            ["nvidia-smi", "--query-compute-apps=pid,process_name,used_memory",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3
        )
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

    return result


@dashboard_router.get("/api/dashboard/gpu")
async def get_gpu_json():
    return JSONResponse(await get_gpu_metrics())


@dashboard_router.get("/api/dashboard/stats")
async def get_stats():
    import json

    gpu = await get_gpu_metrics()

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
            details.append({"label": "n_gpu_layers", "value": str(ngl)})
            try:
                ctx = cm.n_ctx()
                details.append({"label": "n_ctx", "value": str(ctx)})
            except Exception:
                details.append({"label": "n_ctx", "value": "?"})
            fa_type = (getattr(cp2, 'flash_attn_type', None) if cp2 else
                       getattr(cm, 'flash_attn_type', None))
            if fa_type is None:
                fa = '?'
            elif fa_type == 2:  # LLAMA_FLASH_ATTN_TYPE_ENABLED
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

    return JSONResponse({
        "rag_stats": {
            "indexed_files": len(state.rag_state),
            "pending_events": state.file_event_queue.qsize() if hasattr(state, "file_event_queue") and state.file_event_queue else 0,
            "total_chunks": total_chunks
        },
        "models": models,
        "inference": {
            "total_requests": total_requests,
            "total_prompt_tokens": total_prompt_tokens,
            "total_completion_tokens": total_completion_tokens
        },
        "gpu": gpu,
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
    try:
        res_pts = await state.http_client.post(
            f"http://{QDRANT_HOST}:6333/collections/{collection}/points/scroll",
            json={"limit": 300, "with_payload": True, "with_vector": True},
            timeout=5.0
        )
        if res_pts.status_code == 200:
            raw_points = res_pts.json().get("result", {}).get("points", [])
            
            vectors = []
            for p in raw_points:
                vec = p.get("vector")
                if vec is not None:
                    vectors.append(vec)
                    del p["vector"]
                points_data.append(p)
                
            if vectors:
                vec_mat = np.array(vectors)
                norms = np.linalg.norm(vec_mat, axis=1, keepdims=True)
                norms[norms == 0] = 1
                vec_mat_norm = vec_mat / norms
                sim_matrix = np.dot(vec_mat_norm, vec_mat_norm.T)
                
                for i in range(len(vectors)):
                    similar_indices = np.argsort(sim_matrix[i])[::-1]
                    added_links = 0
                    for j in similar_indices:
                        if i == j:
                            continue
                        sim = float(sim_matrix[i][j])
                        if sim > 0.6:
                            if i < j:
                                links_data.append({
                                    "source": points_data[i]["id"],
                                    "target": points_data[j]["id"],
                                    "similarity": sim
                                })
                            added_links += 1
                        if added_links >= 4:
                            break
                            
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
        
    return JSONResponse({"points": points_data, "links": links_data})
