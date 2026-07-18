// ═══════════════════════════════════════════════════
// NeuroNet Admin Panel — Telemetry Polling (stats + pipeline + sessions)
// ═══════════════════════════════════════════════════

// ── Domain-specific update functions ──

function updateGPU(g) {
    if (!g) return;
    document.getElementById('gpu-temp').innerText = g.temp ?? '--';
    const navbarGpuTemp = document.getElementById('navbar-gpu-temp');
    const navbarGpuDot = document.getElementById('navbar-gpu-dot');
    if (g.temp != null) {
        navbarGpuTemp.innerText = g.temp + '°C';
        const gpuColor = g.temp > 80 ? 'var(--danger)' : g.temp > 65 ? 'var(--warning)' : 'var(--primary)';
        navbarGpuDot.style.background = gpuColor;
        navbarGpuDot.style.color = gpuColor;
    }
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

function updateFeatures(f) {
    if (!f) return;
    const container = document.getElementById('features-list');
    container.innerHTML = '';
    const labels = {
        llm: 'LLM Engine', embeddings: 'Embeddings', rag: 'RAG (Qdrant)',
        memory: 'Memory (mem0)', ast_parser: 'AST Parser',
        file_watcher: 'File Watcher', telegram: 'Telegram Bot',
        cron: 'Cron Scheduler', searxng: 'SearXNG',
        crawl4ai: 'Crawl4AI', whisper: 'Voice I/O', userbots: 'Userbots',
        synaptiq: 'Synaptiq'
    };
    for (const [key, label] of Object.entries(labels)) {
        const active = f[key];
        const div = document.createElement('div');
        div.className = 'text-sm flex items-center gap-4';
        div.innerHTML = `<span style="color:${active ? 'var(--primary)' : 'var(--text-muted)'};">${active ? '✓' : '○'}</span> ${label}`;
        container.appendChild(div);
    }
}

function updateRAGStats(rs) {
    if (!rs) return;
    document.getElementById('indexed-files').innerText = rs.indexed_files ?? 0;
    document.getElementById('pending-queue').innerText = rs.pending_events ?? 0;
    document.getElementById('total-chunks').innerText = rs.total_chunks ?? 0;
}

function updateModels(m) {
    if (!m) return;
    const chatName = m.chat_model || 'N/A';
    const embedName = m.embed_model || 'N/A';
    document.getElementById('model-chat-side').innerText = chatName.split('/').pop();
    document.getElementById('model-embed-side').innerText = embedName.split('/').pop();
    document.getElementById('navbar-model').innerText = chatName.split('/').pop();
    const sideList = document.getElementById('model-details-side');
    sideList.innerHTML = '';
    if (m.details) {
        m.details.forEach(d => {
            const li = document.createElement('li');
            li.style.fontSize = '0.7rem';
            li.innerHTML = `<span>${d.label}</span> <span class="badge badge-accent">${d.value}</span>`;
            sideList.appendChild(li);
        });
    } else {
        sideList.innerHTML = '<li class="text-muted text-sm">No model loaded</li>';
    }
}

function updateInference(inf) {
    if (!inf) return;
    document.getElementById('inf-requests').innerText = inf.total_requests ?? 0;
    document.getElementById('inf-tokens').innerText = inf.total_completion_tokens ?? 0;
    document.getElementById('inf-prompt-tokens').innerText = inf.total_prompt_tokens ?? 0;
}

function updateQdrantCollections(collections) {
    const qList = document.getElementById('qdrant-list');
    qList.innerHTML = '';
    if (collections && collections.length > 0) {
        collections.forEach(col => {
            const li = document.createElement('li');
            const name = typeof col === 'string' ? col : col.name;
            const points = typeof col === 'string' ? '' : (col.points ?? '');
            li.innerHTML = `
                <span>${name}${points ? ' <span class="text-muted text-xs">('+points+' pts)</span>' : ''}</span>
                <button class="btn" onclick="openGraphModal('${name}')">Graph</button>
            `;
            qList.appendChild(li);
        });
    } else {
        qList.innerHTML = '<li class="text-muted">No collections</li>';
    }
}

function updateAgentStats(agentStats) {
    if (!agentStats) return;
    document.getElementById('active-cron').innerText = agentStats.active_crons ?? 0;
    document.getElementById('active-todos').innerText = agentStats.active_todos ?? 0;
    document.getElementById('allowed-users').innerText = agentStats.allowed_users ?? 0;
    document.getElementById('async-tasks').innerText = agentStats.async_tasks ?? 0;
}

function setHealth(id, isUp) {
    const el = document.getElementById(id);
    if(isUp) { el.innerText = "ONLINE"; el.className = "badge badge-primary"; }
    else { el.innerText = "OFFLINE"; el.className = "badge badge-danger"; }
}

function updateHealth(health) {
    if (!health) return;
    setHealth('health-searxng', health.searxng);
    setHealth('health-crawl4ai', health.crawl4ai);
    setHealth('health-qdrant', health.qdrant);
    const allUp = health.searxng && health.crawl4ai && health.qdrant;
    const navbarServices = document.getElementById('navbar-services');
    const navbarHealthDot = document.getElementById('navbar-health-dot');
    if (navbarServices) {
        navbarServices.innerText = allUp ? 'All OK' : 'Issues';
        navbarServices.style.color = allUp ? 'var(--primary)' : 'var(--danger)';
    }
    if (navbarHealthDot) {
        navbarHealthDot.style.background = allUp ? 'var(--primary)' : 'var(--danger)';
        navbarHealthDot.style.color = allUp ? 'var(--primary)' : 'var(--danger)';
    }
}

function updateSysStats(sysStats) {
    if (!sysStats) return;
    document.getElementById('sys-ram').innerText = sysStats.ram_mb + " MB";
    document.getElementById('sys-uptime').innerText = sysStats.uptime || '--';
    document.getElementById('sys-load').innerText = sysStats.load || '--';
    document.getElementById('sys-disk').innerText = sysStats.disk || '--';
    const navbarUptime = document.getElementById('navbar-uptime');
    if (navbarUptime) {
        const uptimeStr = sysStats.uptime || '--';
        const parts = uptimeStr.split(' ');
        navbarUptime.innerText = parts[0] || uptimeStr;
    }
}

function updateSynaptiq(sy) {
    if (!sy) return;
    const syReady = sy.initialized && sy.available;
    const syBadge = document.getElementById('sy-status-badge');
    if (syBadge) {
        if (!sy.available) {
            syBadge.innerText = 'NOT INSTALLED';
            syBadge.className = 'badge badge-danger';
        } else if (syReady) {
            syBadge.innerText = 'ACTIVE';
            syBadge.className = 'badge badge-primary';
        } else {
            syBadge.innerText = 'IDLE';
            syBadge.className = 'badge badge-warning';
        }
    }
    const syHealth = document.getElementById('health-synaptiq');
    if (syHealth) {
        if (!sy.available) {
            syHealth.innerText = 'N/A';
            syHealth.className = 'badge';
        } else if (syReady) {
            syHealth.innerText = 'ONLINE';
            syHealth.className = 'badge badge-primary';
        } else {
            syHealth.innerText = 'IDLE';
            syHealth.className = 'badge badge-warning';
        }
    }
    document.getElementById('sy-nodes').innerText = sy.nodes_count ?? '--';
    document.getElementById('sy-relations').innerText = sy.relationships_count ?? '--';
}

// ── Model distribution from recent traces ──

function updateModelDistribution(traces) {
    const el = document.getElementById('model-distribution');
    if (!traces || traces.length === 0) {
        el.innerHTML = '<span class="text-muted">No data</span>';
        return;
    }
    const models = {};
    traces.forEach(t => {
        const m = t.model_used || 'unknown';
        models[m] = (models[m] || 0) + 1;
    });
    const total = traces.length;
    el.innerHTML = Object.entries(models)
        .sort((a, b) => b[1] - a[1])
        .map(([m, c]) => {
            const pct = ((c / total) * 100).toFixed(0);
            const name = m.split('/').pop() || m;
            return `<div style="display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px solid rgba(255,255,255,0.04);">
                <span style="overflow:hidden;text-overflow:ellipsis;max-width:140px;" title="${escapeHtml(m)}">${escapeHtml(name)}</span>
                <span class="text-primary">${c} (${pct}%)</span>
            </div>`;
        })
        .join('');
}

// ── Session updates ──

function updateSessionTelemetry(sessions) {
    if (!sessions) return;
    const stats = sessions.stats;
    if (stats) {
        document.getElementById('tele-sessions-count').textContent = stats.total_sessions ?? 0;
        document.getElementById('tele-sessions-turns').textContent = stats.total_turns ?? 0;
    }
    // Recent sessions table
    const tbody = document.getElementById('sessions-table-body');
    const list = sessions.recent;
    if (list && list.length > 0) {
        tbody.innerHTML = list.map(s => {
            const shortId = (s.conversation_id || s.id || '').substring(0, 16);
            const lastAct = s.last_activity ? new Date(s.last_activity * 1000).toLocaleString() : '--';
            const duration = s.duration_hours != null
                ? (s.duration_hours < 1 ? (s.duration_hours * 60).toFixed(0) + 'm' : s.duration_hours.toFixed(1) + 'h')
                : '--';
            const model = s.model || '--';
            return `<tr onclick="openSessionDetail('${escapeHtml(s.conversation_id || s.id || '')}')" style="cursor:pointer;">
                <td class="mono text-muted" style="padding:4px 8px;font-size:0.65rem;">${escapeHtml(shortId)}</td>
                <td style="padding:4px 8px;text-align:right;">${s.turn_count ?? s.turns ?? 0}</td>
                <td style="padding:4px 8px;text-align:right;">${s.total_tokens ?? '--'}</td>
                <td style="padding:4px 8px;text-align:right;">${duration}</td>
                <td style="padding:4px 8px;text-align:center;font-size:0.65rem;">${escapeHtml(model.split('/').pop() || model)}</td>
                <td style="padding:4px 8px;text-align:right;font-size:0.65rem;color:var(--text-muted);">${lastAct}</td>
            </tr>`;
        }).join('');
    } else {
        tbody.innerHTML = '<tr><td colspan="6" class="text-center text-muted">No sessions yet</td></tr>';
    }
}

// ── Stats Poll (3s) ──

window.fetchStats = async function() {
    if (isModalOpen) return;
    try {
        const res = await fetch('/api/dashboard/stats');
        const data = await res.json();

        updateGPU(data.gpu);

        if (data.gpu_history) updateCharts(data.gpu_history);

        updateFeatures(data.features);

        updateRAGStats(data.rag_stats);

        updateModels(data.models);

        updateInference(data.inference);

        if (data.inference_history && data.inference_history.length > 0) {
            const lastInf = data.inference_history[data.inference_history.length-1];
            document.getElementById('inf-tok-per-sec').innerText = lastInf.tokens_per_sec ?? 0;
        }

        if (data.sys_history) updateSysCharts(data.sys_history);
        if (data.inference_history) updateInfCharts(data.inference_history);

        updateQdrantCollections(data.qdrant_collections);

        updateAgentStats(data.agent_stats);

        updateHealth(data.health);

        updateSysStats(data.sys_stats);

        updateSynaptiq(data.synaptiq);

        // Session stats from telemetry
        const tel = data.telemetry;
        if (tel && tel.sessions) {
            document.getElementById('tele-sessions-count').textContent = tel.sessions.total_sessions ?? 0;
            document.getElementById('tele-sessions-turns').textContent = tel.sessions.total_turns ?? 0;
        }

    } catch (err) {
        console.error('Failed to fetch telemetry', err);
    }
};

// ── Telemetry Pipeline Poll (5s) ──

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

function toggleSessions() {
    const content = document.getElementById('sessions-content');
    const icon = document.getElementById('toggle-sessions-icon');
    if (content.style.display === 'none') {
        content.style.display = 'block';
        icon.textContent = '▼';
    } else {
        content.style.display = 'none';
        icon.textContent = '▶';
    }
}

// ── Trace Detail Modal ──

function openTraceModal(trace) {
    const modal = document.getElementById('trace-modal');
    document.getElementById('trace-modal-id').textContent = (trace.request_id || trace.id || '').substring(0, 16);

    // Overview
    const duration = trace.duration_ms ? (trace.duration_ms / 1000).toFixed(2) + 's' : '--';
    const tokens = (trace.total_tokens ?? (trace.total_prompt_tokens + trace.total_completion_tokens)) || '--';
    const model = (trace.model_used || '').split('/').pop() || '--';
    const ttft = trace.ttft_ms != null ? trace.ttft_ms.toFixed(0) + 'ms' : '--';
    const tokPerSec = trace.generation_speed_tok_s != null ? trace.generation_speed_tok_s.toFixed(1) : '--';
    const streaming = trace.is_streaming ? '✓' : '✗';
    document.getElementById('td-overview').innerHTML = `
        <div class="td-row"><span class="td-label">Duration</span><span>${duration}</span></div>
        <div class="td-row"><span class="td-label">Total Tokens</span><span>${tokens}</span></div>
        <div class="td-row"><span class="td-label">Prompt / Completion</span><span>${trace.total_prompt_tokens ?? '--'} / ${trace.total_completion_tokens ?? '--'}</span></div>
        <div class="td-row"><span class="td-label">Model</span><span class="text-primary">${escapeHtml(model)}</span></div>
        <div class="td-row"><span class="td-label">TTFT</span><span>${ttft}</span></div>
        <div class="td-row"><span class="td-label">Generation Speed</span><span>${tokPerSec} tok/s</span></div>
        <div class="td-row"><span class="td-label">Streaming</span><span>${streaming}</span></div>
        <div class="td-row"><span class="td-label">Steps</span><span>${(trace.steps || []).length}</span></div>
        <div class="td-row"><span class="td-label">LLM Calls</span><span>${(trace.llm_calls || []).length}</span></div>
        ${trace.error ? `<div class="td-row"><span class="td-label">Error</span><span class="text-danger">${escapeHtml(trace.error)}</span></div>` : ''}
    `;

    // Gatekeeper
    const gk = trace.gatekeeper;
    document.getElementById('td-gatekeeper').innerHTML = gk ? `
        <div class="td-row"><span class="td-label">Intent</span><span class="text-primary">${escapeHtml(gk.intent || '--')}</span></div>
        <div class="td-row"><span class="td-label">Confidence</span><span>${gk.confidence != null ? (gk.confidence * 100).toFixed(0) + '%' : '--'}</span></div>
        <div class="td-row"><span class="td-label">Project</span><span>${escapeHtml(gk.project || '--')}</span></div>
        <div class="td-row"><span class="td-label">Bypassed</span><span>${gk.bypassed ? '✓' : '✗'}</span></div>
        <div class="td-row"><span class="td-label">Gatekeeper Model</span><span>${escapeHtml(trace.gatekeeper_model || '--')}</span></div>
    ` : '<span class="text-muted">No gatekeeper data</span>';

    // RAG & Memory
    document.getElementById('td-rag-memory').innerHTML = `
        <div class="td-row"><span class="td-label">RAG Context Length</span><span>${trace.rag_ctx_len ?? '--'}</span></div>
        <div class="td-row"><span class="td-label">RAG Project</span><span>${escapeHtml(trace.rag_project || '--')}</span></div>
        <div class="td-row"><span class="td-label">Memory Records</span><span>${trace.memory_records ?? '--'}</span></div>
        <div class="td-row"><span class="td-label">Memory Search</span><span>${trace.memory_search_ms != null ? trace.memory_search_ms.toFixed(0) + 'ms' : '--'}</span></div>
    `;

    // Web & Synaptiq
    document.getElementById('td-web-synaptiq').innerHTML = `
        <div class="td-row"><span class="td-label">Web Search</span><span>${trace.web_search_performed ? '✓' : '✗'}</span></div>
        <div class="td-row"><span class="td-label">Web Search Duration</span><span>${trace.web_search_duration_ms != null ? trace.web_search_duration_ms.toFixed(0) + 'ms' : '--'}</span></div>
        <div class="td-row"><span class="td-label">Synaptiq</span><span>${trace.synaptiq_performed ? '✓' : '✗'}</span></div>
        <div class="td-row"><span class="td-label">Synaptiq Chars</span><span>${trace.synaptiq_chars ?? '--'}</span></div>
    `;

    // Compression
    document.getElementById('td-compression').innerHTML = `
        <div class="td-row"><span class="td-label">Raw Size</span><span>${trace.compression_raw_size ?? '--'}</span></div>
        <div class="td-row"><span class="td-label">Is Raw Fallback</span><span>${trace.compression_is_raw ? '✓' : '✗'}</span></div>
    `;

    // Tools
    const toolNames = trace.tool_names;
    document.getElementById('td-tools').innerHTML = `
        <div class="td-row"><span class="td-label">Tool Calls</span><span>${trace.tool_calls_count ?? '--'}</span></div>
        <div class="td-row"><span class="td-label">Agentic Depth</span><span>${trace.agentic_loop_depth ?? '--'}</span></div>
        <div class="td-row"><span class="td-label">Tools Used</span><span>${toolNames && toolNames.length > 0 ? escapeHtml(toolNames.join(', ')) : 'none'}</span></div>
    `;

    // LLM Calls
    const llmCalls = trace.llm_calls;
    document.getElementById('td-llm-calls').innerHTML = llmCalls && llmCalls.length > 0
        ? llmCalls.map((l, i) => `
            <div style="padding:8px 0;border-bottom:1px solid rgba(255,255,255,0.04);">
                <div class="td-row"><span class="td-label">Call #${i + 1}</span><span>${escapeHtml(l.model || l.model_name || '?')}</span></div>
                <div class="td-row"><span class="td-label">Tokens</span><span>${l.prompt_tokens || 0}↑ ${l.completion_tokens || 0}↓</span></div>
                ${l.duration_ms ? `<div class="td-row"><span class="td-label">Duration</span><span>${(l.duration_ms / 1000).toFixed(1)}s</span></div>` : ''}
            </div>
        `).join('')
        : '<span class="text-muted">No LLM calls</span>';

    // Steps
    const steps = trace.steps;
    document.getElementById('td-steps').innerHTML = steps && steps.length > 0
        ? steps.map(s => {
            const stepDuration = s.duration_ms != null ? (s.duration_ms / 1000).toFixed(2) + 's' : '';
            const statusIcon = s.status === 'error' ? '❌' : s.status === 'skipped' ? '⏭' : s.status === 'completed' || s.status === 'ok' ? '✓' : '⋯';
            return `<div style="padding:6px 0;border-bottom:1px solid rgba(255,255,255,0.03);display:flex;justify-content:space-between;align-items:center;">
                <span><span style="margin-right:6px;">${statusIcon}</span>${escapeHtml(s.step || s.name || '?')}</span>
                <span class="text-muted" style="font-size:0.65rem;">${stepDuration}${s.status ? ' · ' + s.status : ''}</span>
            </div>`;
        }).join('')
        : '<span class="text-muted">No steps recorded</span>';

    modal.style.display = 'block';
}

function closeTraceModal() {
    document.getElementById('trace-modal').style.display = 'none';
}

// Close modal on outside click
document.addEventListener('click', function(e) {
    const modal = document.getElementById('trace-modal');
    if (e.target === modal) {
        modal.style.display = 'none';
    }
});

// Escape key closes modal
document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') {
        document.getElementById('trace-modal').style.display = 'none';
    }
});

// ── Session Detail (fetches full session data) ──

async function openSessionDetail(convId) {
    if (!convId) return;
    try {
        const res = await fetch('/api/dashboard/telemetry');
        const data = await res.json();
        // We don't have a direct /api/dashboard/session/{id} endpoint, use the MCP tool via main
        // Fallback: search sessions and find the matching one
        // For now, show toast notification
        showToast('Session ' + convId.substring(0, 12) + ' — use MCP tool get_session for full detail', 'info');
    } catch(e) {
        console.error('Session detail error', e);
    }
}

// ── Telemetry fetch ──

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
        }

        // Trace / error counts (sempre, anche senza gatekeeper)
        document.getElementById('tele-trace-count').textContent = data.recent_traces?.length ?? 0;
        document.getElementById('tele-error-count').textContent = Object.keys(data.error_counters || {}).length || 0;
        document.getElementById('tele-active-traces').textContent = data.active_traces?.length ?? 0;

        // Model distribution
        updateModelDistribution(data.recent_traces);

        // Sessions
        if (data.sessions) {
            updateSessionTelemetry(data.sessions);
        }

        // Recent traces table
        const tbody = document.getElementById('traces-table-body');
        if (data.recent_traces && data.recent_traces.length > 0) {
            tbody.innerHTML = '';
            data.recent_traces.slice(0, 10).forEach(t => {
                const tr = document.createElement('tr');
                const duration = t.duration_ms ? (t.duration_ms / 1000).toFixed(1) + 's' : '--';
                const tokens = (t.total_tokens ?? (t.total_prompt_tokens + t.total_completion_tokens)) || '--';
                const steps = t.steps?.length ?? 0;
                const status = t.error ? '❌' : '✓';
                const shortId = (t.request_id || t.id || '').substring(0, 8);
                const model = (t.model_used || '').split('/').pop() || '--';
                const ttft = t.ttft_ms != null ? t.ttft_ms.toFixed(0) : '--';
                const tokPerSec = t.generation_speed_tok_s != null ? t.generation_speed_tok_s.toFixed(1) : '--';
                const toolCalls = t.tool_calls_count || t.agentic_loop_depth || 0;
                const ragUsed = t.rag_ctx_len > 0 ? '📚' : '—';
                const memUsed = t.memory_records > 0 ? '🧠' : '—';
                tr.innerHTML = `
                    <td class="mono text-muted" style="padding:4px 8px;font-size:0.65rem;">${escapeHtml(shortId)}</td>
                    <td style="padding:4px 8px;text-align:right;font-size:0.65rem;max-width:80px;overflow:hidden;text-overflow:ellipsis;" title="${escapeHtml(t.model_used || '')}">${escapeHtml(model)}</td>
                    <td style="padding:4px 8px;text-align:right;">${tokens}</td>
                    <td style="padding:4px 8px;text-align:right;">${duration}</td>
                    <td style="padding:4px 8px;text-align:right;color:var(--secondary);">${ttft}ms</td>
                    <td style="padding:4px 8px;text-align:right;color:var(--primary);">${tokPerSec}</td>
                    <td style="padding:4px 8px;text-align:center;">${toolCalls > 0 ? '🔧' : '—'}</td>
                    <td style="padding:4px 8px;text-align:center;">${ragUsed}</td>
                    <td style="padding:4px 8px;text-align:center;">${memUsed}</td>
                    <td style="padding:4px 8px;text-align:center;">${status}</td>
                `;
                tr.style.cursor = 'pointer';
                tr.onmouseenter = () => { tr.style.background = 'rgba(0,255,204,0.05)'; };
                tr.onmouseleave = () => { tr.style.background = ''; };
                tr.onclick = () => { openTraceModal(t); };
                tbody.appendChild(tr);
            });
        } else {
            tbody.innerHTML = '<tr><td colspan="10" style="padding:12px;text-align:center;color:var(--text-muted);">No traces yet</td></tr>';
        }
    } catch (err) {
        console.error('Failed to fetch telemetry', err);
    }
}

// ── Page Visibility API ──
// Pause/resume polling when tab is hidden/visible

document.addEventListener('visibilitychange', () => {
    const statsId = window._statsInterval;
    const telemetryId = window._telemetryInterval;
    if (document.hidden) {
        if (statsId) clearInterval(statsId);
        if (telemetryId) clearInterval(telemetryId);
        window._statsInterval = null;
        window._telemetryInterval = null;
    } else {
        if (!window._statsInterval) {
            fetchStats();
            window._statsInterval = setInterval(fetchStats, 3000);
        }
        if (!window._telemetryInterval) {
            fetchTelemetry();
            window._telemetryInterval = setInterval(fetchTelemetry, 5000);
        }
    }
});
