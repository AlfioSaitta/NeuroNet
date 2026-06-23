import os
import sys
from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse
from config import OLLAMA_BASE, QDRANT_HOST, ALLOWED_USERS
import state

dashboard_router = APIRouter()

HTML_CONTENT = r"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Jarvis Central Dashboard</title>
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
        
        /* Sidebar */
        .sidebar {
            width: 320px;
            background: rgba(10, 15, 20, 0.8);
            backdrop-filter: blur(20px);
            border-right: 1px solid var(--glass-border);
            padding: 30px 20px;
            display: flex;
            flex-direction: column;
            gap: 30px;
            overflow-y: auto;
            z-index: 10;
        }
        .brand {
            text-align: center;
        }
        .brand h1 {
            font-weight: 800; font-size: 2.2rem;
            background: linear-gradient(90deg, var(--primary), var(--secondary));
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            margin: 0; letter-spacing: -1px;
        }
        .brand .subtitle {
            font-family: 'JetBrains Mono', monospace; font-size: 0.8rem;
            color: var(--text-muted); text-transform: uppercase; letter-spacing: 2px; margin-top: 5px;
        }
        
        /* Main Area */
        .main-content {
            flex: 1;
            padding: 40px;
            overflow-y: auto;
            display: flex;
            flex-direction: column;
            gap: 30px;
        }

        .grid-container {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(350px, 1fr));
            gap: 24px;
        }

        .card {
            background: var(--glass-bg);
            border: 1px solid var(--glass-border);
            border-radius: 16px; padding: 24px;
            box-shadow: 0 8px 32px rgba(0,0,0,0.3);
            transition: transform 0.3s ease, box-shadow 0.3s ease;
            position: relative;
        }
        .card:hover {
            transform: translateY(-2px);
            box-shadow: 0 12px 40px rgba(0, 255, 204, 0.05);
            border-color: rgba(0, 255, 204, 0.3);
        }
        .card-header {
            font-size: 1.1rem; font-weight: 600; color: #fff;
            text-transform: uppercase; letter-spacing: 1px;
            display: flex; align-items: center; margin-bottom: 25px;
        }
        
        /* Dots */
        .dot { height: 10px; width: 10px; border-radius: 50%; display: inline-block; margin-right: 12px; box-shadow: 0 0 10px currentColor; }
        .dot-primary { background: var(--primary); color: var(--primary); }
        .dot-secondary { background: var(--secondary); color: var(--secondary); }
        .dot-accent { background: var(--accent); color: var(--accent); }
        .dot-warning { background: var(--warning); color: var(--warning); }
        .dot-danger { background: var(--danger); color: var(--danger); }
        .pulsing { animation: pulse 2s infinite ease-in-out; }
        @keyframes pulse { 0% { opacity: 1; transform: scale(1); } 50% { opacity: 0.5; transform: scale(1.4); } 100% { opacity: 1; transform: scale(1); } }

        /* Metrics */
        .metric-row { display: flex; gap: 20px; align-items: flex-end; margin-bottom: 20px; }
        .metric { flex: 1; }
        .metric .val { font-size: 2rem; font-weight: 800; font-family: 'JetBrains Mono', monospace; margin: 0; line-height: 1; }
        .metric .label { color: var(--text-muted); font-size: 0.8rem; text-transform: uppercase; font-weight: 600; letter-spacing: 1px; margin-top: 8px; }

        /* Lists */
        .data-list { list-style: none; padding: 0; margin: 0; font-family: 'JetBrains Mono', monospace; font-size: 0.85rem; }
        .data-list li { padding: 12px 0; border-bottom: 1px solid rgba(255,255,255,0.05); display: flex; justify-content: space-between; align-items: center; }
        .data-list li:last-child { border-bottom: none; }
        
        .badge { background: rgba(255,255,255,0.05); padding: 4px 10px; border-radius: 8px; font-weight: 600; font-size: 0.75rem; text-transform: uppercase;}
        .badge-primary { background: rgba(0, 255, 204, 0.1); color: var(--primary); border: 1px solid rgba(0, 255, 204, 0.2); }
        .badge-danger { background: rgba(255, 51, 102, 0.1); color: var(--danger); border: 1px solid rgba(255, 51, 102, 0.2); }
        .badge-accent { background: rgba(123, 44, 191, 0.15); color: #d8b4fe; border: 1px solid rgba(123, 44, 191, 0.3); }

        .btn {
            background: rgba(0, 255, 204, 0.1); color: var(--primary);
            border: 1px solid rgba(0, 255, 204, 0.3); border-radius: 6px;
            padding: 6px 12px; cursor: pointer; transition: 0.2s;
            font-family: 'JetBrains Mono', monospace; font-size: 0.75rem; font-weight: 600;
        }
        .btn:hover { background: var(--primary); color: #000; box-shadow: 0 0 15px var(--primary); }

        /* Graph Modal */
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
        .modal-header h2 { margin: 0; font-size: 1.5rem; font-weight: 600; display: flex; align-items: center; gap: 15px;}
        .close-modal { color: #fff; font-size: 32px; cursor: pointer; transition: 0.2s; line-height: 1;}
        .close-modal:hover { color: var(--danger); transform: scale(1.1);}
        
        #graph-container {
            width: 100vw; height: 100vh;
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
        .node-info .property-row { margin-bottom: 15px; }
        .node-info .property-label { font-size: 0.75rem; color: var(--secondary); text-transform: uppercase; font-weight: 600; margin-bottom: 4px; }
        .node-info .property-value { font-size: 0.9rem; line-height: 1.4; color: #fff; word-break: break-word;}
        .node-info pre { margin: 0; font-family: 'JetBrains Mono', monospace; font-size: 0.75rem; border-radius: 8px; border: 1px solid rgba(255,255,255,0.1); }
        
        /* Scrollbar styles */
        ::-webkit-scrollbar { width: 8px; }
        ::-webkit-scrollbar-track { background: rgba(0,0,0,0.2); }
        ::-webkit-scrollbar-thumb { background: rgba(0,255,204,0.3); border-radius: 4px; }
        ::-webkit-scrollbar-thumb:hover { background: rgba(0,255,204,0.6); }
    </style>
</head>
<body>

    <div class="sidebar">
        <div class="brand">
            <h1>JARVIS</h1>
            <div class="subtitle">System Telemetry</div>
        </div>

        <div class="card" style="padding: 20px;">
            <div class="card-header" style="font-size: 0.9rem;"><span class="dot dot-warning"></span> Host Status</div>
            <div class="metric-row" style="flex-direction: column; gap: 15px; align-items: flex-start;">
                <div class="metric">
                    <div class="val" id="sys-uptime" style="font-size: 1.4rem;">0h 0m</div>
                    <div class="label">Uptime</div>
                </div>
                <div class="metric">
                    <div class="val" id="sys-load" style="font-size: 1.4rem; color: var(--warning)">0.00</div>
                    <div class="label">CPU Load Avg</div>
                </div>
                <div class="metric">
                    <div class="val" id="sys-disk" style="font-size: 1.4rem;">0 GB</div>
                    <div class="label">Disk Usage</div>
                </div>
                <div class="metric">
                    <div class="val" id="sys-ram" style="font-size: 1.4rem; color: var(--secondary)">0 MB</div>
                    <div class="label">Agent Memory</div>
                </div>
            </div>
        </div>

        <div class="card" style="padding: 20px;">
            <div class="card-header" style="font-size: 0.9rem;"><span class="dot dot-secondary"></span> Integrations</div>
            <ul class="data-list">
                <li><span>SearXNG</span> <span class="badge" id="health-searxng">...</span></li>
                <li><span>Crawl4AI</span> <span class="badge" id="health-crawl4ai">...</span></li>
                <li><span>Qdrant</span> <span class="badge" id="health-qdrant">...</span></li>
            </ul>
        </div>
    </div>

    <div class="main-content">
        <div class="grid-container">
            
            <!-- Neural Engine -->
            <div class="card">
                <div class="card-header"><span class="dot dot-accent pulsing"></span> Neural Engine (Local Llama)</div>
                <div class="metric-row">
                    <div class="metric">
                        <div class="val" id="total-vram" style="color: #d8b4fe;">0.00</div>
                        <div class="label">GB VRAM Allocated</div>
                    </div>
                    <div class="metric">
                        <div class="val" id="model-count">0</div>
                        <div class="label">Models Active</div>
                    </div>
                </div>
                <ul class="data-list" id="model-list">
                    <li style="color: var(--text-muted); justify-content: center;">Scanning memory...</li>
                </ul>
            </div>

            <!-- Agentic Autonomy -->
            <div class="card">
                <div class="card-header"><span class="dot dot-primary"></span> Agentic Autonomy</div>
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

        <!-- RAG Vector DB -->
        <div class="card" style="flex: 1; display: flex; flex-direction: column;">
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
                    <div class="label">FS Pending Events</div>
                </div>
            </div>
            
            <h3 style="font-size: 0.9rem; color: var(--text-muted); text-transform: uppercase; margin-top: 20px;">Qdrant Collections (Vector Graph)</h3>
            <ul class="data-list" id="qdrant-list" style="flex: 1; overflow-y: auto;">
                <!-- Collections -->
            </ul>
        </div>
    </div>

    <!-- Graph Modal -->
    <div id="graph-modal" class="modal">
        <div class="modal-header">
            <h2><span class="dot dot-primary pulsing"></span> <span id="modal-title">Vector Network</span></h2>
            <div id="filter-container" style="display: none; align-items: center; gap: 15px; background: rgba(0,0,0,0.5); padding: 8px 15px; border-radius: 8px; border: 1px solid var(--glass-border);">
                <input type="text" id="node-search" placeholder="Search content..." class="btn" style="background: rgba(0,0,0,0.8); color: #fff; padding: 4px 8px; border: 1px solid var(--glass-border); max-width: 150px;" onkeyup="applyGraphFilter()">
                <label style="color: var(--text-muted); font-size: 0.75rem; text-transform: uppercase; font-family: 'JetBrains Mono', monospace;">Type:</label>
                <select id="file-type-filter" class="btn" style="background: rgba(0,0,0,0.8); color: #fff; padding: 4px 8px;" onchange="applyGraphFilter()">
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

        async function fetchStats() {
            if (isModalOpen) return; 
            try {
                const res = await fetch('/api/dashboard/stats');
                const data = await res.json();
                
                // RAG
                document.getElementById('indexed-files').innerText = data.rag_stats.indexed_files;
                document.getElementById('pending-queue').innerText = data.rag_stats.pending_events;
                document.getElementById('total-chunks').innerText = data.rag_stats.total_chunks;
                
                // LLM
                let totalVram = 0;
                const modelList = document.getElementById('model-list');
                modelList.innerHTML = '';
                let mCount = 0;
                if (data.ollama_stats && data.ollama_stats.models && data.ollama_stats.models.length > 0) {
                    mCount = data.ollama_stats.models.length;
                    data.ollama_stats.models.forEach(model => {
                        totalVram += model.size_vram;
                        const vramGB = (model.size_vram / 1e9).toFixed(2);
                        const li = document.createElement('li');
                        li.innerHTML = `<span>${model.name.replace(':latest', '')}</span> <span class="badge badge-accent">${vramGB} GB</span>`;
                        modelList.appendChild(li);
                    });
                } else {
                    modelList.innerHTML = '<li style="color: var(--text-muted); justify-content: center;">Nessun modello in VRAM</li>';
                }
                document.getElementById('total-vram').innerText = (totalVram / 1e9).toFixed(2);
                document.getElementById('model-count').innerText = mCount;
                
                // Qdrant Collections
                const qList = document.getElementById('qdrant-list');
                qList.innerHTML = '';
                if(data.qdrant_collections) {
                    data.qdrant_collections.forEach(col => {
                        const li = document.createElement('li');
                        li.innerHTML = `
                            <span>${col}</span> 
                            <button class="btn" onclick="openGraphModal('${col}')">🕸️ Visualize Graph</button>
                        `;
                        qList.appendChild(li);
                    });
                }
                
                // Agent Status
                document.getElementById('active-cron').innerText = data.agent_stats.active_crons;
                document.getElementById('active-todos').innerText = data.agent_stats.active_todos;
                document.getElementById('allowed-users').innerText = data.agent_stats.allowed_users;
                document.getElementById('async-tasks').innerText = data.agent_stats.async_tasks;
                
                // Health
                const setHealth = (id, isUp) => {
                    const el = document.getElementById(id);
                    if(isUp) { el.innerText = "ONLINE"; el.className = "badge badge-primary"; }
                    else { el.innerText = "OFFLINE"; el.className = "badge badge-danger"; }
                };
                setHealth('health-searxng', data.health.searxng);
                setHealth('health-crawl4ai', data.health.crawl4ai);
                setHealth('health-qdrant', data.health.qdrant);
                
                // System 
                document.getElementById('sys-ram').innerText = data.sys_stats.ram_mb + " MB";
                document.getElementById('sys-uptime').innerText = data.sys_stats.uptime;
                document.getElementById('sys-load').innerText = data.sys_stats.load;
                document.getElementById('sys-disk').innerText = data.sys_stats.disk;
                
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
            
            // hide node info if selected node is filtered out
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
                // Fetch vectors and pre-computed links from backend
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
                    // Attempt to extract extension from payload fields
                    const potentialFields = [p.payload.file_path, p.payload.source, p.payload.url];
                    for (let field of potentialFields) {
                        if (typeof field === 'string' && field.includes('.')) {
                            const parts = field.split('?')[0].split('#')[0].split('.');
                            ext = "." + parts[parts.length - 1].toLowerCase();
                            // Keep it simple: limit to known or short alphanumeric extensions
                            if (ext.length > 6 || !/^\.[a-z0-9]+$/.test(ext)) {
                                ext = "Unknown";
                            } else {
                                break;
                            }
                        }
                    }
                    // Fallback to text detection
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

                // Populate filter dropdown
                const filterSelect = document.getElementById('file-type-filter');
                filterSelect.innerHTML = '<option value="ALL">All Files</option>';
                const sortedExts = Array.from(extSet).sort();
                sortedExts.forEach(e => {
                    const opt = document.createElement('option');
                    opt.value = e;
                    opt.innerText = e.toUpperCase();
                    filterSelect.appendChild(opt);
                });
                
                if (sortedExts.length > 1 || (sortedExts.length === 1 && sortedExts[0] !== "Unknown") || allNodes.length > 0) {
                    document.getElementById('filter-container').style.display = "flex";
                }

                allLinks = data.links || [];

                Graph = ForceGraph()(document.getElementById('graph-container'))
                    .backgroundColor('transparent')
                    .nodeRelSize(8)
                    // Particelle che viaggiano sui link
                    .linkDirectionalParticles(link => link.similarity > 0.7 ? 2 : 0)
                    .linkDirectionalParticleSpeed(d => (d.similarity - 0.6) * 0.02)
                    .nodeColor(node => node === currentSelectedNode ? '#ff00ff' : '#00ffcc')
                    .linkColor(link => `rgba(123, 44, 191, ${Math.max(0.1, (link.similarity - 0.5) * 2)})`)
                    .linkWidth(link => Math.max(1, (link.similarity - 0.6) * 15))
                    // Replace hover with click
                    .onNodeClick(node => {
                        currentSelectedNode = node;
                        Graph.nodeColor(Graph.nodeColor()); // trigger color update

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

                        // Mostra eventuali testi/dati speciali in rilievo, interpretandoli come codice formattato
                        if(node.payload.text || node.payload.data) {
                            const mainText = node.payload.text || node.payload.data;
                            htmlContent += `<div class="property-row"><div class="property-label">Primary Text</div><div class="property-value"><pre><code class="language-javascript" style="white-space: pre-wrap; padding: 10px; border-radius: 6px;">${escapeHtml(mainText)}</code></pre></div></div>`;
                        }
                        
                        // Visualizza il payload JSON formattato
                        const payloadStr = JSON.stringify(node.payload, null, 2);
                        htmlContent += `<div class="property-row"><div class="property-label">Raw Payload (JSON)</div><pre><code class="language-json" style="padding: 10px; border-radius: 6px;">${escapeHtml(payloadStr)}</code></pre></div>`;
                        
                        contentBox.innerHTML = htmlContent;
                        
                        // Applica l'highlighting del codice
                        contentBox.querySelectorAll('pre code').forEach((block) => {
                            hljs.highlightElement(block);
                        });
                    })
                    .onNodeHover(node => {
                        // Change cursor only, no info popup
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

        setInterval(fetchStats, 2000);
        fetchStats();
    </script>
</body>
</html>
"""

@dashboard_router.get("/")
@dashboard_router.get("/dashboard")
async def get_dashboard():
    return HTMLResponse(HTML_CONTENT)

@dashboard_router.get("/api/dashboard/qdrant/{collection}/vectors")
async def get_qdrant_vectors(collection: str):
    import numpy as np
    points_data = []
    links_data = []
    try:
        # Retrieve up to 300 vectors to build the graph
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
                    del p["vector"] # Remove from response to save bandwidth
                points_data.append(p)
                
            if vectors:
                # Numpy cosine similarity
                vec_mat = np.array(vectors)
                norms = np.linalg.norm(vec_mat, axis=1, keepdims=True)
                norms[norms == 0] = 1
                vec_mat_norm = vec_mat / norms
                sim_matrix = np.dot(vec_mat_norm, vec_mat_norm.T)
                
                for i in range(len(vectors)):
                    # Get indices sorted by similarity descending
                    similar_indices = np.argsort(sim_matrix[i])[::-1]
                    added_links = 0
                    for j in similar_indices:
                        if i == j:
                            continue
                        sim = float(sim_matrix[i][j])
                        if sim > 0.6:
                            if i < j: # Avoid bidirectional duplicates
                                links_data.append({
                                    "source": points_data[i]["id"],
                                    "target": points_data[j]["id"],
                                    "similarity": sim
                                })
                            added_links += 1
                        if added_links >= 4: # limit max 4 nearest neighbors per node to keep graph clean
                            break
                            
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
        
    return JSONResponse({"points": points_data, "links": links_data})

@dashboard_router.get("/api/dashboard/stats")
async def get_stats():
    import json
    
    # Ollama
    ollama_data = {}
    try:
        res = await state.http_client.get(f"{OLLAMA_BASE}/api/ps", timeout=2.0)
        if res.status_code == 200:
            ollama_data = res.json()
    except Exception:
        pass

    # Qdrant Collections
    qdrant_collections = []
    qdrant_up = False
    try:
        res = await state.http_client.get(f"http://{QDRANT_HOST}:6333/collections", timeout=2.0)
        if res.status_code == 200:
            qdrant_up = True
            c_data = res.json()
            if "result" in c_data and "collections" in c_data["result"]:
                qdrant_collections = [c["name"] for c in c_data["result"]["collections"]]
    except Exception:
        pass

    # Health Pings
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
        
    # Python Process RAM
    try:
        process = open('/proc/self/statm').read().split()[1] # resident set size in pages
        page_size = os.sysconf('SC_PAGE_SIZE')
        ram_mb = round((int(process) * page_size) / (1024 * 1024), 1)
    except:
        ram_mb = 0

    # System Uptime
    try:
        with open('/proc/uptime', 'r') as f:
            uptime_seconds = float(f.readline().split()[0])
            sys_uptime = f"{int(uptime_seconds // 3600)}h {int((uptime_seconds % 3600) // 60)}m"
    except:
        sys_uptime = "N/A"

    # CPU Load
    try:
        with open('/proc/loadavg', 'r') as f:
            sys_load = " ".join(f.readline().split()[0:3])
    except:
        sys_load = "N/A"

    # Disk Space
    try:
        st = os.statvfs('/')
        total_gb = (st.f_blocks * st.f_frsize) / (1024 ** 3)
        free_gb = (st.f_bavail * st.f_frsize) / (1024 ** 3)
        sys_disk = f"{total_gb - free_gb:.1f}G / {total_gb:.1f}G"
    except:
        sys_disk = "N/A"

    # Task & Crons
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

    # RAG Chunks
    total_chunks = sum(len(f_data.get('chunks', [])) for f_data in state.rag_state.values())

    return JSONResponse({
        "rag_stats": {
            "indexed_files": len(state.rag_state),
            "pending_events": state.file_event_queue.qsize() if hasattr(state, "file_event_queue") and state.file_event_queue else 0,
            "total_chunks": total_chunks
        },
        "ollama_stats": ollama_data,
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
