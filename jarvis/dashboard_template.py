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
        
        #graph-container { width: 100%; height: 100%; 
            background-image: radial-gradient(circle at 1px 1px, rgba(0, 255, 204, 0.06) 1px, transparent 0);
            background-size: 20px 20px;
        }
        #graph-tooltip {
            position: fixed; padding: 6px 10px; border-radius: 6px; pointer-events: none; z-index: 1020;
            font-family: 'JetBrains Mono', monospace; font-size: 0.7rem; line-height: 1.4;
            background: rgba(10, 15, 25, 0.92); border: 1px solid var(--primary);
            color: var(--text-main); display: none;
            box-shadow: 0 4px 16px rgba(0,0,0,0.5), 0 0 12px rgba(0,255,204,0.08);
            max-width: 320px; white-space: nowrap;
        }
        #graph-controls { display: flex; align-items: center; gap: 8px; }
        #graph-controls button {
            background: rgba(0, 255, 204, 0.1); color: var(--primary);
            border: 1px solid rgba(0, 255, 204, 0.3); border-radius: 6px;
            padding: 2px 8px; cursor: pointer; transition: 0.2s;
            font-family: 'JetBrains Mono', monospace; font-size: 0.7rem; font-weight: 600;
        }
        #graph-controls button:hover { background: var(--primary); color: #000; box-shadow: 0 0 10px var(--primary); }
        #graph-status { font-size: 0.65rem; font-family: 'JetBrains Mono', monospace; color: var(--text-muted); }
        #graph-stats {
            position: absolute; top: 56px; right: 12px; z-index: 1010;
            background: rgba(10, 15, 25, 0.85); border: 1px solid rgba(255,255,255,0.08);
            padding: 6px 10px; border-radius: 6px; font-family: 'JetBrains Mono', monospace;
            font-size: 0.6rem; color: var(--text-muted); display: none; line-height: 1.6;
        }
        #filter-count { font-size: 0.6rem; color: var(--text-muted); font-family: 'JetBrains Mono', monospace; margin-left: 8px; display: none; }
        #min-degree-input {
            background: rgba(0,0,0,0.8); color: #fff; padding: 2px 6px; width: 40px;
            border: 1px solid var(--glass-border); border-radius: 4px;
            font-family: 'JetBrains Mono', monospace; font-size: 0.65rem;
        }
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
                <li><span>MCP v2</span> <span class="badge badge-primary" id="mcp-v2-badge">✔ Streamable HTTP</span></li>
            </ul>
            <div style="display:flex;gap:6px;margin-top:8px;padding-top:8px;border-top:1px solid rgba(255,255,255,0.05);">
                <button class="btn" onclick="restartIngestion()" style="font-size:0.65rem;padding:3px 8px;">⟳ Restart Ingestion</button>
                <button class="btn" onclick="openMemoryGraphModal()" style="font-size:0.65rem;padding:3px 8px;background:rgba(179,136,255,0.15);border-color:rgba(179,136,255,0.3);color:#b388ff;">✧ Memory Graph</button>
            </div>
        </div>

        <div class="card" style="padding: 16px;">
            <div class="card-header" style="font-size: 0.85rem; margin-bottom: 10px;">
                <span class="dot dot-accent pulsing"></span> Telemetry Pipeline
            </div>
            <div class="metric-row" style="gap: 8px; margin-bottom: 6px;">
                <div class="metric">
                    <div class="val" id="tele-trace-count" style="font-size: 1.2rem; color: var(--secondary);">0</div>
                    <div class="label" style="font-size: 0.6rem;">Traces</div>
                </div>
                <div class="metric">
                    <div class="val" id="tele-active-traces" style="font-size: 1.2rem; color: var(--primary);">0</div>
                    <div class="label" style="font-size: 0.6rem;">Active</div>
                </div>
                <div class="metric">
                    <div class="val" id="tele-error-count" style="font-size: 1.2rem; color: var(--danger);">0</div>
                    <div class="label" style="font-size: 0.6rem;">Errors</div>
                </div>
            </div>
            <div style="font-size:0.7rem;color:var(--text-muted);margin-bottom:4px;">Gatekeeper</div>
            <div id="gatekeeper-summary" style="font-size:0.65rem;font-family:'JetBrains Mono',monospace;">
                <div>Bypass rate: <span id="gk-bypass-rate">--</span></div>
                <div>Avg confidence: <span id="gk-avg-conf">--</span></div>
                <div>Classifications: <span id="gk-classified">--</span></div>
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

        <!-- Pipeline Traces -->
        <div class="card fade-in">
            <div class="card-header" style="cursor:pointer;" onclick="toggleTraces()">
                <span class="dot dot-secondary"></span> Recent Pipeline Traces
                <span style="flex:1;"></span>
                <span id="toggle-traces-icon" style="font-size:0.8rem;color:var(--text-muted);">▶</span>
            </div>
            <div id="traces-content" style="display:none;">
                <div style="max-height:300px;overflow-y:auto;">
                    <table style="width:100%;font-size:0.7rem;font-family:'JetBrains Mono',monospace;border-collapse:collapse;">
                        <thead>
                            <tr style="color:var(--text-muted);border-bottom:1px solid rgba(255,255,255,0.1);">
                                <th style="padding:4px 8px;text-align:left;">ID</th>
                                <th style="padding:4px 8px;text-align:right;">Steps</th>
                                <th style="padding:4px 8px;text-align:right;">Duration</th>
                                <th style="padding:4px 8px;text-align:right;">Tokens</th>
                                <th style="padding:4px 8px;text-align:center;">Status</th>
                            </tr>
                        </thead>
                        <tbody id="traces-table-body">
                            <tr><td colspan="5" style="padding:12px;text-align:center;color:var(--text-muted);">No traces yet</td></tr>
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
    </div>

    <div id="graph-modal" class="modal">
        <div class="modal-header">
            <h2><span class="dot dot-primary pulsing"></span> <span id="modal-title">Vector Network</span></h2>
            <div id="graph-controls" style="display:none;">
                <button id="btn-pause-fa2" onclick="toggleFA2()" title="Pause/Resume simulation">⏸</button>
                <button onclick="zoomToFitGraph()" title="Zoom to fit">⊞</button>
                <span id="graph-status"></span>
            </div>
            <div id="filter-container">
                <input type="text" id="node-search" placeholder="Search..." onkeyup="applyGraphFilter()">
                <label style="color: var(--text-muted); font-size: 0.7rem; text-transform: uppercase; font-family: 'JetBrains Mono', monospace;">Type:</label>
                <select id="file-type-filter" onchange="applyGraphFilter()">
                    <option value="ALL">All Files</option>
                </select>
                <label style="color: var(--text-muted); font-size: 0.65rem; text-transform: uppercase; font-family: 'JetBrains Mono', monospace;">Min deg:</label>
                <input type="number" id="min-degree-input" value="0" min="0" onchange="applyGraphFilter()">
                <span id="filter-count"></span>
            </div>
            <div class="close-modal" onclick="closeModal()">&times;</div>
        </div>
        <div id="graph-container"></div>
        <div id="graph-tooltip"></div>
        <div id="graph-stats"></div>
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
        let hoveredNode = null;
        let graphResizeObserver = null;
        let fa2AutoPauseTimer = null;

        const graphFilter = { ext: 'ALL', query: '', minDegree: 0, group: 'ALL' };

        function toggleFA2() {
            if (!fa2Layout) return;
            const btn = document.getElementById('btn-pause-fa2');
            const status = document.getElementById('graph-status');
            if (fa2Layout.running) {
                fa2Layout.stop();
                btn.textContent = '▶';
                status.textContent = '⏸ Paused';
                status.style.color = 'var(--warning)';
            } else {
                fa2Layout.start();
                btn.textContent = '⏸';
                status.textContent = '⚡ Simulating';
                status.style.color = 'var(--primary)';
            }
        }

        function zoomToFitGraph() {
            if (!sigmaInstance) return;
            sigmaInstance.getCamera().animatedReset({ duration: 300 });
        }

        function computeGraphStats(sigmaGraph) {
            const n = sigmaGraph.order;
            const e = sigmaGraph.size;
            const avgDeg = n > 0 ? (2 * e / n) : 0;
            const density = n > 1 ? (2 * e / (n * (n - 1))) : 0;
            return { nodes: n, edges: e, avgDegree: avgDeg.toFixed(1), density: (density * 100).toFixed(2) };
        }

        function updateGraphStats(containerId, stats) {
            const el = document.getElementById(containerId || 'graph-stats');
            if (!el) return;
            el.style.display = 'block';
            el.innerHTML = `N: ${stats.nodes} | E: ${stats.edges} | Avg Deg: ${stats.avgDegree} | ρ: ${stats.density}%`;
        }

        function getNodeDegree(nodeId, links) {
            let deg = 0;
            links.forEach(l => {
                const sId = l.source?.id || l.source;
                const tId = l.target?.id || l.target;
                if (sId === nodeId || tId === nodeId) deg++;
            });
            return deg;
        }

        function computeDegreeMap(links) {
            const deg = {};
            links.forEach(l => {
                const sId = l.source?.id || l.source;
                const tId = l.target?.id || l.target;
                deg[sId] = (deg[sId] || 0) + 1;
                deg[tId] = (deg[tId] || 0) + 1;
            });
            return deg;
        }

        function getConnectedNodeIds(nodeId, links) {
            const connected = new Set();
            connected.add(nodeId);
            links.forEach(l => {
                const sId = l.source?.id || l.source;
                const tId = l.target?.id || l.target;
                if (sId === nodeId) connected.add(tId);
                if (tId === nodeId) connected.add(sId);
            });
            return connected;
        }

        function buildGroupLayout(nodes, getGroupFn) {
            const groups = {};
            nodes.forEach(n => {
                const g = getGroupFn(n);
                if (!groups[g]) groups[g] = [];
                groups[g].push(n.id);
            });
            const groupKeys = Object.keys(groups).sort();
            const positions = {};
            groupKeys.forEach((g, gi) => {
                const ids = groups[g];
                const radius = (gi + 1) * 35;
                ids.forEach((id, ni) => {
                    const angle = (2 * Math.PI * ni) / ids.length;
                    positions[id] = {
                        x: radius * Math.cos(angle) + (Math.random() - 0.5) * 5,
                        y: radius * Math.sin(angle) + (Math.random() - 0.5) * 5,
                    };
                });
            });
            return positions;
        }

        function setupResizeObserver(containerId, renderer) {
            if (graphResizeObserver) graphResizeObserver.disconnect();
            const container = document.getElementById(containerId);
            if (!container || !renderer) return;
            graphResizeObserver = new ResizeObserver(() => {
                try { renderer.getCamera().animatedReset({ duration: 200 }); } catch(e) {}
            });
            graphResizeObserver.observe(container);
        }

        function showGraphTooltip(e, text) {
            const tt = document.getElementById('graph-tooltip');
            if (!tt) return;
            tt.textContent = text;
            tt.style.display = 'block';
            tt.style.left = (e.pageX + 12) + 'px';
            tt.style.top = (e.pageY + 12) + 'px';
        }

        function hideGraphTooltip() {
            const tt = document.getElementById('graph-tooltip');
            if (tt) tt.style.display = 'none';
        }

        function updateFilterCount(sigmaGraph) {
            const el = document.getElementById('filter-count');
            if (!el || !sigmaGraph) return;
            let visible = 0, total = sigmaGraph.order;
            sigmaGraph.forEachNode(n => { if (!sigmaGraph.getNodeAttribute(n, 'hidden')) visible++; });
            el.textContent = `${visible}/${total}`;
            el.style.display = total > 0 ? 'inline' : 'none';
        }

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
            const minDegInput = document.getElementById('min-degree-input');
            graphFilter.minDegree = minDegInput ? parseInt(minDegInput.value) || 0 : 0;
            const groupFilter = document.getElementById('group-type-filter');
            graphFilter.group = groupFilter ? groupFilter.value : 'ALL';
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

                // Compute degree and group positions
                const degree = computeDegreeMap(allLinks);
                const maxDeg = Math.max(1, ...Object.values(degree));
                const nodeCount = allNodes.length;

                // Calculate avg degree for hub labeling
                const degValues = Object.values(degree);
                const avgDeg = degValues.reduce((a, b) => a + b, 0) / Math.max(1, degValues.length);
                const stdDeg = Math.sqrt(degValues.reduce((sq, d) => sq + (d - avgDeg) ** 2, 0) / Math.max(1, degValues.length));
                const hubThreshold = avgDeg + stdDeg;

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

                // --- Smart initial positions by group ---
                const positions = buildGroupLayout(allNodes, n => n.ext);

                // --- Build graphology graph ---
                const sigmaGraph = new window.__graphology();

                data.points.forEach(p => {
                    const ext = allNodes.find(n => n.id === p.id)?.ext || 'Unknown';
                    const pdeg = degree[p.id] || 0;
                    const size = Math.max(3, Math.min(20, 1 + pdeg / maxDeg * 4));
                    const pos = positions[p.id] || { x: Math.random() * 200 - 100, y: Math.random() * 200 - 100 };
                    const isHub = pdeg > hubThreshold;
                    const labelHint = p.payload?.text || p.payload?.data || '';
                    sigmaGraph.addNode(p.id, {
                        label: isHub
                            ? `${EXT_NAMES[ext] || ext} — ${pdeg} connections`
                            : (nodeCount < 200 ? `${EXT_NAMES[ext] || ext} — ${pdeg} conn` : ''),
                        x: pos.x,
                        y: pos.y,
                        size: nodeCount > 500 ? size * 0.6 : size,
                        color: EXT_COLORS[ext] || '#888888',
                        ext: ext,
                        degree: pdeg,
                        payload: p.payload || {},
                    });
                });

                data.links.forEach(l => {
                    const source = l.source?.id || l.source;
                    const target = l.target?.id || l.target;
                    if (sigmaGraph.hasNode(source) && sigmaGraph.hasNode(target)) {
                        const sim = l.similarity || 0.5;
                        const alpha = Math.max(0.15, Math.min(0.8, (sim - 0.35) * 3));
                        const width = Math.max(0.5, Math.min(3, sim * 4));
                        sigmaGraph.addEdge(source, target, {
                            color: `rgba(0, 255, 204, ${alpha})`,
                            size: width,
                            similarity: sim,
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

                // Auto-pause after 30 seconds
                if (fa2AutoPauseTimer) clearTimeout(fa2AutoPauseTimer);
                fa2AutoPauseTimer = setTimeout(() => {
                    if (fa2Layout && fa2Layout.running) {
                        fa2Layout.stop();
                        document.getElementById('btn-pause-fa2').textContent = '▶';
                        document.getElementById('graph-status').textContent = '✓ Stabilized';
                        document.getElementById('graph-status').style.color = 'var(--primary)';
                    }
                }, 30000);

                // Show FA2 controls
                document.getElementById('graph-controls').style.display = 'flex';
                document.getElementById('btn-pause-fa2').textContent = '⏸';
                document.getElementById('graph-status').textContent = '⚡ Simulating';
                document.getElementById('graph-status').style.color = 'var(--primary)';

                // Create sigma renderer with error recovery
                let renderer;
                try {
                    renderer = new window.__sigma(sigmaGraph, document.getElementById('graph-container'), {
                        renderEdgeLabels: false,
                        enableEdgeEvents: true,
                        labelRenderedSizeThreshold: 6,
                        labelDensity: 0.3,
                        minCameraRatio: 0.05,
                        maxCameraRatio: 10,
                        nodeReducer: (node, data) => {
                            const pdeg = sigmaGraph.getNodeAttribute(node, 'degree') || 0;
                            if (graphFilter.minDegree > 0 && pdeg < graphFilter.minDegree) {
                                return { ...data, hidden: true };
                            }
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
                            if (hoveredNode) {
                                const connected = getConnectedNodeIds(hoveredNode, allLinks);
                                if (!connected.has(node)) {
                                    return { ...data, color: '#444', size: data.size * 0.5, label: '' };
                                }
                                if (node === hoveredNode) {
                                    return { ...data, color: '#ff00ff', size: data.size * 2.0 };
                                }
                                return { ...data, size: data.size * 1.3 };
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
                            if (hoveredNode) {
                                const connected = getConnectedNodeIds(hoveredNode, allLinks);
                                const sOk = connected.has(src);
                                const tOk = connected.has(tgt);
                                if (sOk && tOk) {
                                    return { ...data, color: 'rgba(0, 255, 204, 0.7)', size: 2 };
                                }
                                return { ...data, color: 'rgba(100, 100, 100, 0.05)', size: 0.3 };
                            }
                            return data;
                        },
                    });

                    sigmaInstance = renderer;

                    // Show stats
                    const stats = computeGraphStats(sigmaGraph);
                    updateGraphStats('graph-stats', stats);

                    // Resize observer
                    setupResizeObserver('graph-container', renderer);

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

                    // Hover events
                    renderer.on('enterNode', ({ node }) => {
                        document.getElementById('graph-container').style.cursor = 'pointer';
                        hoveredNode = node;
                        const attrs = sigmaGraph.getNodeAttributes(node);
                        const pdeg = attrs.degree || 0;
                        const label = `${EXT_NAMES[attrs.ext] || attrs.ext} — ${pdeg} connections`;
                        showGraphTooltip(
                            window.event || { pageX: 0, pageY: 0 },
                            `${label}\nID: ${node.substring(0, 24)}...`
                        );
                        renderer.refresh();
                    });

                    renderer.on('leaveNode', () => {
                        document.getElementById('graph-container').style.cursor = '';
                        hoveredNode = null;
                        hideGraphTooltip();
                        renderer.refresh();
                    });

                    renderer.on('clickStage', () => {
                        selectedNodeId = null;
                        document.getElementById('node-info').style.display = "none";
                        renderer.refresh();
                    });

                    renderer.on('clickEdge', ({ edge }) => {
                        const [src, tgt] = sigmaGraph.extremities(edge);
                        const sim = sigmaGraph.getEdgeAttribute(edge, 'similarity') || '?';
                        selectedNodeId = null;
                        const infoBox = document.getElementById('node-info');
                        const contentBox = document.getElementById('node-content');
                        infoBox.style.display = "block";
                        contentBox.innerHTML = `
                            <div class="property-row"><div class="property-label">Source</div><div class="property-value">${escapeHtml(src)}</div></div>
                            <div class="property-row"><div class="property-label">Target</div><div class="property-value">${escapeHtml(tgt)}</div></div>
                            <div class="property-row"><div class="property-label">Similarity</div><div class="property-value" style="color:var(--primary)">${typeof sim === 'number' ? (sim * 100).toFixed(1) + '%' : sim}</div></div>
                        `;
                        renderer.refresh();
                    });

                    // Mousemove for tooltip
                    document.getElementById('graph-container').addEventListener('mousemove', (e) => {
                        if (hoveredNode) {
                            const attrs = sigmaGraph.getNodeAttributes(hoveredNode);
                            const pdeg = attrs.degree || 0;
                            showGraphTooltip(e, `${EXT_NAMES[attrs.ext] || attrs.ext} — ${pdeg} connections\nID: ${hoveredNode.substring(0, 24)}...`);
                        }
                    });

                    // Fit view
                    setTimeout(() => {
                        renderer.getCamera().animatedReset({ duration: 400 });
                    }, 200);

                    // Update filter count
                    updateFilterCount(sigmaGraph);

                } catch(e) {
                    console.error(e);
                    document.getElementById('modal-title').innerText = `⚠️ Graph render error: ${e.message}`;
                }

            } catch(e) {
                console.error(e);
                document.getElementById('modal-title').innerText = "Error Loading Graph";
            }
        }

        function closeModal() {
            document.getElementById('graph-modal').style.display = "none";
            document.getElementById('node-info').style.display = "none";
            document.getElementById('graph-legend').style.display = "none";
            document.getElementById('graph-controls').style.display = "none";
            document.getElementById('graph-stats').style.display = "none";
            document.getElementById('filter-count').style.display = "none";
            hideGraphTooltip();
            if(fa2AutoPauseTimer) {
                clearTimeout(fa2AutoPauseTimer);
                fa2AutoPauseTimer = null;
            }
            if(graphResizeObserver) {
                graphResizeObserver.disconnect();
                graphResizeObserver = null;
            }
            if(fa2Layout) {
                fa2Layout.kill();
                fa2Layout = null;
            }
            if(sigmaInstance) {
                sigmaInstance.kill();
                sigmaInstance = null;
            }
            document.getElementById('graph-container').innerHTML = '';
            hoveredNode = null;
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
                const degree = computeDegreeMap(allLinks);
                const maxDeg = Math.max(1, ...Object.values(degree));
                const nodeCount = allNodes.length;

                // Degree stats for hub labels
                const degValues = Object.values(degree);
                const avgDeg = degValues.reduce((a, b) => a + b, 0) / Math.max(1, degValues.length);
                const stdDeg = Math.sqrt(degValues.reduce((sq, d) => sq + (d - avgDeg) ** 2, 0) / Math.max(1, degValues.length));
                const hubThreshold = avgDeg + stdDeg;

                // Add group filter for memory graph
                const filterSelect = document.getElementById('file-type-filter');
                filterSelect.innerHTML = '<option value="ALL">All Types</option><option value="entity">Entity</option><option value="memory">Memory</option>';

                // Show min-degree filter
                const minDegContainer = document.getElementById('min-degree-input');
                if (minDegContainer) minDegContainer.style.display = 'inline-block';

                if (nodeCount > 0) {
                    document.getElementById('filter-container').style.display = "flex";
                }

                // --- Smart positions by group ---
                const positions = buildGroupLayout(allNodes, n => n.group || 'memory');

                // --- Build graphology graph ---
                const sigmaGraph = new window.__graphology();

                allNodes.forEach(p => {
                    const group = p.group || 'memory';
                    const isEntity = group === 'entity';
                    const pdeg = degree[p.id] || 0;
                    const size = isEntity
                        ? Math.max(5, Math.min(25, 2 + pdeg / maxDeg * 6))
                        : Math.max(3, Math.min(15, 1 + pdeg / maxDeg * 4));
                    const pos = positions[p.id] || { x: Math.random() * 200 - 100, y: Math.random() * 200 - 100 };
                    const isHub = pdeg > hubThreshold;
                    const entityName = p.payload?.entity_name || '';
                    const label = isEntity
                        ? (isHub ? `Entity: ${entityName} (${pdeg})` : (nodeCount < 200 ? `Entity: ${entityName}` : ''))
                        : (isHub ? `Memory (${pdeg} connections)` : (nodeCount < 200 ? `Memory (${pdeg})` : ''));
                    sigmaGraph.addNode(p.id, {
                        label: label,
                        x: pos.x,
                        y: pos.y,
                        size: nodeCount > 500 ? size * 0.7 : size,
                        color: isEntity ? '#b388ff' : '#00e5ff',
                        group: group,
                        degree: pdeg,
                        payload: p.payload || {},
                    });
                });

                allLinks.forEach(l => {
                    const source = l.source?.id || l.source;
                    const target = l.target?.id || l.target;
                    if (sigmaGraph.hasNode(source) && sigmaGraph.hasNode(target)) {
                        sigmaGraph.addEdge(source, target, {
                            color: 'rgba(179, 136, 255, 0.35)',
                            size: 1.2,
                            similarity: l.similarity || 0.5,
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

                // Auto-pause after 30 seconds
                if (fa2AutoPauseTimer) clearTimeout(fa2AutoPauseTimer);
                fa2AutoPauseTimer = setTimeout(() => {
                    if (fa2Layout && fa2Layout.running) {
                        fa2Layout.stop();
                        document.getElementById('btn-pause-fa2').textContent = '▶';
                        document.getElementById('graph-status').textContent = '✓ Stabilized';
                        document.getElementById('graph-status').style.color = 'var(--primary)';
                    }
                }, 30000);

                // Show FA2 controls
                document.getElementById('graph-controls').style.display = 'flex';
                document.getElementById('btn-pause-fa2').textContent = '⏸';
                document.getElementById('graph-status').textContent = '⚡ Simulating';
                document.getElementById('graph-status').style.color = 'var(--primary)';

                // Create sigma renderer with error recovery
                let renderer;
                try {
                    renderer = new window.__sigma(sigmaGraph, document.getElementById('graph-container'), {
                        renderEdgeLabels: false,
                        enableEdgeEvents: true,
                        labelRenderedSizeThreshold: 6,
                        labelDensity: 0.3,
                        minCameraRatio: 0.05,
                        maxCameraRatio: 10,
                        nodeReducer: (node, data) => {
                            const pdeg = sigmaGraph.getNodeAttribute(node, 'degree') || 0;
                            if (graphFilter.minDegree > 0 && pdeg < graphFilter.minDegree) {
                                return { ...data, hidden: true };
                            }
                            if (graphFilter.ext !== 'ALL' && data.group !== graphFilter.ext) {
                                return { ...data, hidden: true };
                            }
                            if (hoveredNode) {
                                const connected = getConnectedNodeIds(hoveredNode, allLinks);
                                if (!connected.has(node)) {
                                    return { ...data, color: '#444', size: data.size * 0.5, label: '' };
                                }
                                if (node === hoveredNode) {
                                    return { ...data, color: '#ff00ff', size: data.size * 2.0 };
                                }
                                return { ...data, size: data.size * 1.3 };
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
                            if (hoveredNode) {
                                const connected = getConnectedNodeIds(hoveredNode, allLinks);
                                const sOk = connected.has(src);
                                const tOk = connected.has(tgt);
                                if (sOk && tOk) {
                                    return { ...data, color: 'rgba(179, 136, 255, 0.7)', size: 2 };
                                }
                                return { ...data, color: 'rgba(100, 100, 100, 0.05)', size: 0.3 };
                            }
                            return data;
                        },
                    });

                    sigmaInstance = renderer;

                    // Show stats
                    const stats = computeGraphStats(sigmaGraph);
                    updateGraphStats('graph-stats', stats);

                    // Resize observer
                    setupResizeObserver('graph-container', renderer);

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

                    // Hover events
                    renderer.on('enterNode', ({ node }) => {
                        document.getElementById('graph-container').style.cursor = 'pointer';
                        hoveredNode = node;
                        const attrs = sigmaGraph.getNodeAttributes(node);
                        const isEntity = attrs.group === 'entity';
                        const pdeg = attrs.degree || 0;
                        const label = isEntity
                            ? `Entity: ${attrs.payload.entity_name || '?'} — ${pdeg} connections`
                            : `Memory — ${pdeg} connections`;
                        showGraphTooltip(
                            window.event || { pageX: 0, pageY: 0 },
                            `${label}\nID: ${node.substring(0, 24)}...`
                        );
                        renderer.refresh();
                    });

                    renderer.on('leaveNode', () => {
                        document.getElementById('graph-container').style.cursor = '';
                        hoveredNode = null;
                        hideGraphTooltip();
                        renderer.refresh();
                    });

                    renderer.on('clickStage', () => {
                        selectedNodeId = null;
                        document.getElementById('node-info').style.display = "none";
                        renderer.refresh();
                    });

                    renderer.on('clickEdge', ({ edge }) => {
                        const [src, tgt] = sigmaGraph.extremities(edge);
                        const sim = sigmaGraph.getEdgeAttribute(edge, 'similarity') || '?';
                        selectedNodeId = null;
                        const infoBox = document.getElementById('node-info');
                        const contentBox = document.getElementById('node-content');
                        infoBox.style.display = "block";
                        contentBox.innerHTML = `
                            <div class="property-row"><div class="property-label">Source</div><div class="property-value">${escapeHtml(src)}</div></div>
                            <div class="property-row"><div class="property-label">Target</div><div class="property-value">${escapeHtml(tgt)}</div></div>
                            <div class="property-row"><div class="property-label">Similarity</div><div class="property-value" style="color:var(--primary)">${typeof sim === 'number' ? (sim * 100).toFixed(1) + '%' : sim}</div></div>
                        `;
                        renderer.refresh();
                    });

                    // Mousemove for tooltip
                    document.getElementById('graph-container').addEventListener('mousemove', (e) => {
                        if (hoveredNode) {
                            const attrs = sigmaGraph.getNodeAttributes(hoveredNode);
                            const isEntity = attrs.group === 'entity';
                            const pdeg = attrs.degree || 0;
                            const label = isEntity
                                ? `Entity: ${attrs.payload.entity_name || '?'} — ${pdeg} connections`
                                : `Memory — ${pdeg} connections`;
                            showGraphTooltip(e, `${label}\nID: ${hoveredNode.substring(0, 24)}...`);
                        }
                    });

                    // Fit view
                    setTimeout(() => {
                        renderer.getCamera().animatedReset({ duration: 400 });
                    }, 200);

                    // Update filter count
                    updateFilterCount(sigmaGraph);

                } catch(e) {
                    console.error(e);
                    document.getElementById('modal-title').innerText = `⚠️ Graph render error: ${e.message}`;
                }

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

        // ================================================================
        // TELEMETRY PIPELINE
        // ================================================================
        function toggleTraces() {
            const content = document.getElementById('traces-content');
            const icon = document.getElementById('toggle-traces-icon');
            if (content.style.display === 'none') {
                content.style.display = 'block';
                icon.textContent = '▼';
            } else {
                content.style.display = 'none';
                icon.textContent = '▶';
            }
        }

        async function fetchTelemetry() {
            try {
                const res = await fetch('/api/dashboard/telemetry');
                const data = await res.json();

                // Gatekeeper stats
                const gk = data.gatekeeper;
                if (gk) {
                    const bypassRate = gk.total_classified > 0
                        ? ((gk.bypassed / gk.total_classified) * 100).toFixed(1) + '%'
                        : '--';
                    document.getElementById('gk-bypass-rate').textContent = bypassRate;

                    const avgConf = gk.avg_confidence
                        ? (gk.avg_confidence * 100).toFixed(1) + '%'
                        : '--';
                    document.getElementById('gk-avg-conf').textContent = avgConf;

                    document.getElementById('gk-classified').textContent = gk.total_classified ?? '--';

                    // Trace / error counts
                    document.getElementById('tele-trace-count').textContent = data.recent_traces?.length ?? 0;
                    document.getElementById('tele-error-count').textContent = Object.keys(data.error_counters || {}).length || 0;
                    document.getElementById('tele-active-traces').textContent = data.active_traces?.length ?? 0;
                }

                // Recent traces table
                const tbody = document.getElementById('traces-table-body');
                if (data.recent_traces && data.recent_traces.length > 0) {
                    tbody.innerHTML = '';
                    data.recent_traces.slice(0, 10).forEach(t => {
                        const tr = document.createElement('tr');
                        const duration = t.duration_ms ? (t.duration_ms / 1000).toFixed(1) + 's' : '--';
                        const tokens = t.total_tokens ?? '--';
                        const steps = t.steps?.length ?? 0;
                        const status = t.error ? '❌' : '✓';
                        const shortId = (t.request_id || t.id || '').substring(0, 12);
                        tr.innerHTML = `
                            <td style="padding:4px 8px;color:var(--text-muted);">${escapeHtml(shortId)}</td>
                            <td style="padding:4px 8px;text-align:right;">${steps}</td>
                            <td style="padding:4px 8px;text-align:right;">${duration}</td>
                            <td style="padding:4px 8px;text-align:right;">${tokens}</td>
                            <td style="padding:4px 8px;text-align:center;">${status}</td>
                        `;
                        tr.style.cursor = 'pointer';
                        tr.onmouseenter = () => { tr.style.background = 'rgba(0,255,204,0.05)'; };
                        tr.onmouseleave = () => { tr.style.background = ''; };
                        tr.onclick = () => {
                            const gatekeeperInfo = t.gatekeeper
                                ? `Gatekeeper: ${t.gatekeeper.intent} (${(t.gatekeeper.confidence*100).toFixed(0)}%)\n`
                                : '';
                            const stepInfo = (t.steps || [])
                                .map(s => `  ${s.step}: ${s.status} (${s.duration_ms}ms)`)
                                .join('\n');
                            const llmCalls = (t.llm_calls || [])
                                .map(l => `  LLM: ${l.model || '?'} — ${l.prompt_tokens || 0}↑ ${l.completion_tokens || 0}↓`)
                                .join('\n');
                            alert(
                                `Trace: ${t.request_id}\n` +
                                `Duration: ${duration}\n` +
                                `Total tokens: ${tokens}\n` +
                                (t.error ? `Error: ${t.error}\n` : '') +
                                `\n${gatekeeperInfo}` +
                                `\nSteps:\n${stepInfo || '  (none)'}` +
                                `\n\nLLM Calls:\n${llmCalls || '  (none)'}`
                            );
                        };
                        tbody.appendChild(tr);
                    });
                } else {
                    tbody.innerHTML = '<tr><td colspan="5" style="padding:12px;text-align:center;color:var(--text-muted);">No traces yet</td></tr>';
                }
            } catch (err) {
                console.error('Failed to fetch telemetry', err);
            }
        }

        // Fetch telemetry on load and poll
        fetchTelemetry();
        setInterval(fetchTelemetry, 5000);
    </script>
</body>
</html>
"""
