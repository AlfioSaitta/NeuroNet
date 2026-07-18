// ═══════════════════════════════════════════════════
// NeuroNet Admin Panel — Telemetry Polling (stats + pipeline)
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
                    <td class="text-muted" style="padding:4px 8px;">${escapeHtml(shortId)}</td>
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
