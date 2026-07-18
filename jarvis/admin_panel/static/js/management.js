// ═══════════════════════════════════════════════════
// NeuroNet Admin Panel — Management views (RAG, Models, Tasks, Cron, Settings, Analytics)
// ═══════════════════════════════════════════════════

// ── RAG ──

async function loadRAGData() {
    try {
        const res = await fetch('/api/dashboard/rag/collections');
        const data = await res.json();
        const tbody = document.getElementById('rag-collections-body');
        if (!data.collections || data.collections.length === 0) {
            tbody.innerHTML = '<tr><td colspan="4" style="text-align:center;color:var(--text-muted);">No collections</td></tr>';
            return;
        }
        tbody.innerHTML = data.collections.map(c => `
            <tr>
                <td style="font-family:'JetBrains Mono',monospace;">${c.name}</td>
                <td>${c.points ?? '?'}</td>
                <td>${c.dimension ?? '?'}</td>
                <td class="actions"><button class="btn" onclick="openGraphModal('${c.name}')" style="font-size:0.6rem;padding:2px 6px;">Graph</button></td>
            </tr>
        `).join('');
    } catch(e) {
        document.getElementById('rag-collections-body').innerHTML = '<tr><td colspan="4" style="text-align:center;color:var(--danger);">Error loading collections</td></tr>';
    }
}

async function triggerReindex() {
    try {
        const res = await fetch('/api/dashboard/rag/reindex', { method: 'POST' });
        const data = await res.json();
        showToast(data.status === 'ok' ? 'Re-index started' : 'Error: ' + (data.error || 'unknown'), data.status === 'ok' ? 'success' : 'error');
    } catch(e) { showToast('Error: ' + e.message, 'error'); }
}

async function deleteCollection() {
    const name = document.getElementById('rag-delete-name').value.trim();
    if (!name) return showToast('Enter a collection name', 'error');
    if (!confirm('Delete collection "' + name + '"?')) return;
    try {
        const res = await fetch('/api/dashboard/rag/collection/delete', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({name: name}) });
        const data = await res.json();
        if (data.status === 'ok') { showToast('Collection deleted', 'success'); loadRAGData(); }
        else showToast('Error: ' + (data.error || 'unknown'), 'error');
    } catch(e) { showToast('Error: ' + e.message, 'error'); }
}

// ── Models ──

async function loadModelsData() {
    try {
        const res = await fetch('/api/dashboard/models');
        const data = await res.json();
        // Current model
        const cm = data.current;
        if (cm) {
            document.getElementById('cm-name').textContent = cm.name || '--';
            document.getElementById('cm-ngl').textContent = cm.n_gpu_layers ?? '--';
            document.getElementById('cm-ctx').textContent = cm.n_ctx ?? '--';
        } else {
            document.getElementById('cm-name').textContent = 'No model loaded';
        }
        // Available models
        const tbody = document.getElementById('models-list-body');
        if (!data.available || data.available.length === 0) {
            tbody.innerHTML = '<tr><td colspan="3" style="text-align:center;color:var(--text-muted);">No models found in models/ directory</td></tr>';
            return;
        }
        tbody.innerHTML = data.available.map(m => `
            <tr>
                <td style="font-family:'JetBrains Mono',monospace;font-size:0.75rem;">${m.name}</td>
                <td>${m.size_gb} GB</td>
                <td class="actions"><button class="btn" onclick="switchModel('${m.path}')" style="font-size:0.6rem;padding:2px 6px;">Switch</button></td>
            </tr>
        `).join('');
    } catch(e) { document.getElementById('models-list-body').innerHTML = '<tr><td colspan="3" style="text-align:center;color:var(--danger);">Error loading models</td></tr>'; }
}

async function switchModel(path) {
    if (!confirm('Switch to this model?')) return;
    try {
        const res = await fetch('/api/dashboard/models/switch', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({path: path}) });
        const data = await res.json();
        showToast(data.status === 'ok' ? data.message : 'Error: ' + (data.error || 'unknown'), data.status === 'ok' ? 'success' : 'error');
        if (data.status === 'ok') loadModelsData();
    } catch(e) { showToast('Error: ' + e.message, 'error'); }
}

// ── Tasks ──

async function loadTasksData() {
    try {
        const res = await fetch('/api/dashboard/tasks');
        const data = await res.json();
        const tbody = document.getElementById('tasks-list-body');
        if (!data.tasks || data.tasks.length === 0) {
            tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:var(--text-muted);">No tasks</td></tr>';
            return;
        }
        tbody.innerHTML = data.tasks.map(t => `
            <tr>
                <td style="font-family:'JetBrains Mono',monospace;font-size:0.7rem;color:var(--text-muted);">${t.id}</td>
                <td>${escapeHtml(t.description)}</td>
                <td><span class="badge ${t.priority === 'high' ? 'badge-danger' : t.priority === 'medium' ? 'badge-warning' : 'badge-primary'}">${t.priority}</span></td>
                <td><span class="badge ${t.status === 'done' ? 'badge-primary' : 'badge-warning'}">${t.status}</span></td>
                <td class="actions"><button class="btn" onclick="deleteTask('${t.id}')" style="font-size:0.6rem;padding:2px 6px;background:rgba(255,51,102,0.1);color:var(--danger);border-color:rgba(255,51,102,0.3);">✕</button></td>
            </tr>
        `).join('');
    } catch(e) { document.getElementById('tasks-list-body').innerHTML = '<tr><td colspan="5" style="text-align:center;color:var(--danger);">Error</td></tr>'; }
}

async function addTask() {
    const desc = document.getElementById('task-desc-input').value.trim();
    if (!desc) return;
    const priority = document.getElementById('task-priority-input').value;
    try {
        const res = await fetch('/api/dashboard/tasks', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({description: desc, priority: priority}) });
        const data = await res.json();
        if (data.status === 'ok') {
            document.getElementById('task-desc-input').value = '';
            loadTasksData();
            showToast('Task created', 'success');
        } else showToast('Error: ' + (data.error || 'unknown'), 'error');
    } catch(e) { showToast('Error: ' + e.message, 'error'); }
}

async function deleteTask(taskId) {
    if (!confirm('Delete task ' + taskId + '?')) return;
    try {
        const res = await fetch('/api/dashboard/tasks/' + taskId, { method: 'DELETE' });
        const data = await res.json();
        if (data.status === 'ok') { loadTasksData(); showToast('Task deleted', 'success'); }
        else showToast('Error: ' + (data.error || 'unknown'), 'error');
    } catch(e) { showToast('Error: ' + e.message, 'error'); }
}

// ── CRON ──

async function loadCronData() {
    try {
        const res = await fetch('/api/dashboard/cron');
        const data = await res.json();
        const tbody = document.getElementById('cron-list-body');
        if (!data.jobs || data.jobs.length === 0) {
            tbody.innerHTML = '<tr><td colspan="4" style="text-align:center;color:var(--text-muted);">No cron jobs</td></tr>';
            return;
        }
        tbody.innerHTML = data.jobs.map(j => `
            <tr>
                <td style="font-family:'JetBrains Mono',monospace;">${escapeHtml(j.name)}</td>
                <td style="font-family:'JetBrains Mono',monospace;font-size:0.7rem;">${escapeHtml(j.schedule || j.trigger)}</td>
                <td style="font-size:0.75rem;">${escapeHtml(j.action)}</td>
                <td><span class="badge ${j.enabled ? 'badge-primary' : 'badge-warning'}">${j.enabled ? 'Active' : 'Paused'}</span></td>
            </tr>
        `).join('');
    } catch(e) { document.getElementById('cron-list-body').innerHTML = '<tr><td colspan="4" style="text-align:center;color:var(--danger);">Error</td></tr>'; }
}

// ── Analytics ──

async function loadAnalyticsData() {
    try {
        const [infRes, teleRes] = await Promise.all([
            fetch('/api/dashboard/analytics/inference'),
            fetch('/api/dashboard/telemetry')
        ]);
        const inf = await infRes.json();
        const tele = await teleRes.json();

        if (inf.counters) {
            document.getElementById('ana-total-req').textContent = inf.counters.total_requests ?? 0;
            document.getElementById('ana-prompt-tok').textContent = inf.counters.total_prompt_tokens ?? 0;
            document.getElementById('ana-compl-tok').textContent = inf.counters.total_completion_tokens ?? 0;
        }
        if (tele.error_counters) {
            const errCount = Object.values(tele.error_counters).reduce((a, b) => a + b, 0);
            document.getElementById('ana-error-count').textContent = errCount;
        }
        const gk = inf.gatekeeper || tele.gatekeeper;
        if (gk) {
            const bypassRate = gk.total_classified > 0 ? ((gk.bypassed / gk.total_classified) * 100).toFixed(1) + '%' : '--';
            const avgConf = gk.avg_confidence ? (gk.avg_confidence * 100).toFixed(1) + '%' : '--';
            document.getElementById('ana-gk-bypass').textContent = bypassRate;
            document.getElementById('ana-gk-conf').textContent = avgConf;
            document.getElementById('ana-gk-class').textContent = gk.total_classified ?? '--';
        }
        // Error distribution
        const errDiv = document.getElementById('error-distribution');
        if (tele.error_counters && Object.keys(tele.error_counters).length > 0) {
            errDiv.innerHTML = Object.entries(tele.error_counters).map(([k, v]) =>
                `<div style="display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid rgba(255,255,255,0.04);"><span>${escapeHtml(k)}</span><span style="color:var(--danger);font-weight:600;">${v}</span></div>`
            ).join('');
        } else {
            errDiv.innerHTML = '<span style="color:var(--text-muted);">No errors recorded</span>';
        }
    } catch(e) {
        console.error('Analytics load error', e);
    }
}

// ── Settings ──

async function loadSettingsData() {
    try {
        const [setRes, sysRes] = await Promise.all([
            fetch('/api/dashboard/settings'),
            fetch('/api/dashboard/system/info')
        ]);
        const setData = await setRes.json();
        const sysData = await sysRes.json();

        const setDiv = document.getElementById('settings-list');
        if (setData.settings) {
            setDiv.innerHTML = Object.entries(setData.settings).map(([k, v]) =>
                `<div style="display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid rgba(255,255,255,0.04);"><span style="color:var(--secondary);">${k}</span><span>${v == null ? '--' : escapeHtml(String(v))}</span></div>`
            ).join('');
        }

        const sysDiv = document.getElementById('system-info');
        sysDiv.innerHTML = Object.entries(sysData).map(([k, v]) =>
            `<div style="display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid rgba(255,255,255,0.04);"><span style="color:var(--secondary);">${k.replace(/_/g, ' ')}</span><span>${v == null ? '--' : escapeHtml(String(v))}</span></div>`
        ).join('');
    } catch(e) {
        console.error('Settings load error', e);
    }
}

// ── Graph Collections list ──

async function loadGraphCollections() {
    try {
        const res = await fetch('/api/dashboard/stats');
        const data = await res.json();
        const list = document.getElementById('qdrant-graph-list');
        if (data.qdrant_collections && data.qdrant_collections.length > 0) {
            list.innerHTML = data.qdrant_collections.map(c => {
                const name = typeof c === 'string' ? c : c.name;
                const pts = typeof c === 'string' ? '' : (c.points ?? '');
                return `<li style="display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid rgba(255,255,255,0.04);"><span style="font-family:'JetBrains Mono',monospace;">${name} ${pts ? '<span style="color:var(--text-muted);font-size:0.7rem;">('+pts+' pts)</span>' : ''}</span><button class="btn" onclick="openGraphModal('${name}')">Visualize</button></li>`;
            }).join('');
        } else {
            list.innerHTML = '<li style="color:var(--text-muted);">No collections found</li>';
        }
    } catch(e) { document.getElementById('qdrant-graph-list').innerHTML = '<li style="color:var(--danger);">Error loading collections</li>'; }
}
