// ═══════════════════════════════════════════════════
// NeuroNet Admin Panel — Management views (Code Graph, Models, Tasks, Cron, Settings, Analytics)
// ═══════════════════════════════════════════════════

// ── Code Graph ──

async function loadGraphCollections() {
    const tbody = document.getElementById('graph-collections-body');
    const countEl = document.getElementById('graph-collection-count');
    showLoading('graph-collections-body', 'Loading collections...');
    try {
        const res = await fetchWithTimeout('/api/dashboard/rag/collections');
        const data = await res.json();
        if (!data.collections || data.collections.length === 0) {
            tbody.innerHTML = '<tr><td colspan="4" style="text-align:center;color:var(--text-muted);">No collections</td></tr>';
            if (countEl) countEl.textContent = '';
            return;
        }
        tbody.innerHTML = data.collections.map(c => `
            <tr>
                <td style="font-family:'JetBrains Mono',monospace;">${c.name}</td>
                <td>${c.points ?? '?'}</td>
                <td>${c.dimension ?? '?'}</td>
                <td class="actions"><button class="btn" onclick="openGraphModal('${c.name}')" style="font-size:0.6rem;padding:2px 6px;">⟐ Graph</button></td>
            </tr>
        `).join('');
        if (countEl) countEl.textContent = data.collections.length + ' collection' + (data.collections.length !== 1 ? 's' : '');
    } catch(e) {
        if (e.name === 'AbortError') return;
        tbody.innerHTML = '<tr><td colspan="4" style="text-align:center;color:var(--danger);">Error loading collections</td></tr>';
    }
}

async function triggerGraphReindex() {
    try {
        const res = await fetchWithTimeout('/api/dashboard/rag/reindex', { method: 'POST' });
        const data = await res.json();
        showToast(data.status === 'ok' ? 'Re-index started' : 'Error: ' + (data.error || 'unknown'), data.status === 'ok' ? 'success' : 'error');
    } catch(e) {
        if (e.name === 'AbortError') return;
        showToast('Error: ' + e.message, 'error');
    }
}

async function deleteGraphCollection() {
    const name = document.getElementById('graph-delete-name').value.trim();
    if (!name) return showToast('Enter a collection name', 'error');
    if (!confirm('Delete collection "' + name + '"?')) return;
    try {
        const res = await fetchWithTimeout('/api/dashboard/rag/collection/delete', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({name: name}) });
        const data = await res.json();
        if (data.status === 'ok') { showToast('Collection deleted', 'success'); loadGraphCollections(); }
        else showToast('Error: ' + (data.error || 'unknown'), 'error');
    } catch(e) {
        if (e.name === 'AbortError') return;
        showToast('Error: ' + e.message, 'error');
    }
}

// ── Models ──

async function loadModelsData() {
    const tbody = document.getElementById('models-list-body');
    showLoading('models-list-body', 'Loading models...');
    try {
        const res = await fetchWithTimeout('/api/dashboard/models');
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
    } catch(e) {
        if (e.name === 'AbortError') return;
        tbody.innerHTML = '<tr><td colspan="3" style="text-align:center;color:var(--danger);">Error loading models</td></tr>';
    }
}

async function switchModel(path) {
    if (!confirm('Switch to this model?')) return;
    try {
        const res = await fetchWithTimeout('/api/dashboard/models/switch', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({path: path}) });
        const data = await res.json();
        showToast(data.status === 'ok' ? data.message : 'Error: ' + (data.error || 'unknown'), data.status === 'ok' ? 'success' : 'error');
        if (data.status === 'ok') loadModelsData();
    } catch(e) {
        if (e.name === 'AbortError') return;
        showToast('Error: ' + e.message, 'error');
    }
}

// ── Tasks ──

async function loadTasksData() {
    const tbody = document.getElementById('tasks-list-body');
    showLoading('tasks-list-body', 'Loading tasks...');
    try {
        const res = await fetchWithTimeout('/api/dashboard/tasks');
        const data = await res.json();
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
    } catch(e) {
        if (e.name === 'AbortError') return;
        document.getElementById('tasks-list-body').innerHTML = '<tr><td colspan="5" style="text-align:center;color:var(--danger);">Error</td></tr>';
    }
}

async function addTask() {
    const desc = document.getElementById('task-desc-input').value.trim();
    if (!desc) return;
    const priority = document.getElementById('task-priority-input').value;
    try {
        const res = await fetchWithTimeout('/api/dashboard/tasks', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({description: desc, priority: priority}) });
        const data = await res.json();
        if (data.status === 'ok') {
            document.getElementById('task-desc-input').value = '';
            loadTasksData();
            showToast('Task created', 'success');
        } else showToast('Error: ' + (data.error || 'unknown'), 'error');
    } catch(e) {
        if (e.name === 'AbortError') return;
        showToast('Error: ' + e.message, 'error');
    }
}

async function deleteTask(taskId) {
    if (!confirm('Delete task ' + taskId + '?')) return;
    try {
        const res = await fetchWithTimeout('/api/dashboard/tasks/' + taskId, { method: 'DELETE' });
        const data = await res.json();
        if (data.status === 'ok') { loadTasksData(); showToast('Task deleted', 'success'); }
        else showToast('Error: ' + (data.error || 'unknown'), 'error');
    } catch(e) {
        if (e.name === 'AbortError') return;
        showToast('Error: ' + e.message, 'error');
    }
}

// ── CRON ──

async function loadCronData() {
    const tbody = document.getElementById('cron-list-body');
    showLoading('cron-list-body', 'Loading cron jobs...');
    try {
        const res = await fetchWithTimeout('/api/dashboard/cron');
        const data = await res.json();
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
    } catch(e) {
        if (e.name === 'AbortError') return;
        document.getElementById('cron-list-body').innerHTML = '<tr><td colspan="4" style="text-align:center;color:var(--danger);">Error</td></tr>';
    }
}

// ── Analytics ──

async function loadAnalyticsData() {
    try {
        const [infRes, teleRes] = await Promise.all([
            fetchWithTimeout('/api/dashboard/analytics/inference'),
            fetchWithTimeout('/api/dashboard/telemetry')
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

let _settingsOriginal = {};
let _settingsDirty = false;

async function loadSettingsData() {
    const container = document.getElementById('settings-categories');
    try {
        const [setRes, sysRes] = await Promise.all([
            fetchWithTimeout('/api/dashboard/settings'),
            fetchWithTimeout('/api/dashboard/system/info')
        ]);
        const setData = await setRes.json();
        const sysData = await sysRes.json();

        // Build settings categories
        const categories = {};
        if (setData.settings && setData.order) {
            for (const key of setData.order) {
                const s = setData.settings[key];
                if (!s || !s.category) continue;
                if (!categories[s.category]) categories[s.category] = [];
                categories[s.category].push({ key, ...s });
            }
        }

        _settingsOriginal = {};
        let html = '';
        for (const [cat, items] of Object.entries(categories)) {
            html += '<div class="card settings-card p-16">';
            html += `<div class="card-header card-header-sm">${cat}</div>`;
            for (const item of items) {
                _settingsOriginal[item.key] = item.value;
                html += '<div class="setting-row">';
                html += `<div class="setting-info"><div class="setting-label">${escapeHtml(item.label)}</div>`;
                if (item.description) html += `<div class="setting-desc">${escapeHtml(item.description)}</div>`;
                html += '</div><div class="setting-control">';
                if (!item.editable) {
                    // Read-only display
                    if (item.type === 'bool') {
                        html += `<span class="badge ${item.value ? 'badge-primary' : 'badge-warning'}">${item.value ? 'Enabled' : 'Disabled'}</span>`;
                    } else {
                        html += `<span class="mono text-sm" style="color:var(--text-muted);">${item.value == null ? '--' : escapeHtml(String(item.value))}</span>`;
                    }
                } else if (item.type === 'bool') {
                    html += `<label class="toggle-switch"><input type="checkbox" id="set-${item.key}" ${item.value ? 'checked' : ''} onchange="markSettingsDirty()"><span class="slider"></span></label>`;
                } else if (item.type === 'number') {
                    html += `<input type="number" id="set-${item.key}" value="${escapeHtml(String(item.value ?? ''))}" oninput="markSettingsDirty()" class="mono text-sm">`;
                } else {
                    html += `<input type="text" id="set-${item.key}" value="${escapeHtml(String(item.value ?? ''))}" oninput="markSettingsDirty()" class="mono text-sm">`;
                }
                html += '</div></div>';
            }
            html += '</div>';
        }

        container.innerHTML = html;

        // Save bar (separate container for sticky positioning)
        const saveBarContainer = document.getElementById('settings-save-bar-container');
        saveBarContainer.innerHTML = `<div class="settings-save-bar">
            <span class="save-status" id="settings-save-status"></span>
            <button class="btn-reset hidden" onclick="resetSettings()" id="settings-reset-btn">↺ Reset</button>
            <button class="btn-primary" onclick="saveSettings()" id="settings-save-btn" disabled>💾 Save Changes</button>
        </div>`;

        // System info
        const sysCard = document.getElementById('system-info-card');
        sysCard.classList.remove('hidden');
        sysCard.innerHTML = `<div class="card p-16">
            <div class="card-header card-header-sm"><span class="dot dot-warning"></span> System Info</div>
            <div class="sys-grid">${Object.entries(sysData).map(([k, v]) =>
                `<div class="sys-row"><span class="sys-label">${k.replace(/_/g, ' ')}</span><span class="sys-value">${v == null ? '--' : escapeHtml(String(v))}</span></div>`
            ).join('')}</div>
        </div>`;
        _settingsDirty = false;
    } catch(e) {
        if (e.name === 'AbortError') return;
        console.error('Settings load error', e);
        container.innerHTML = '<div class="card p-16"><div class="text-center text-muted p-16">Failed to load settings</div></div>';
    }
}

window.markSettingsDirty = function() {
    _settingsDirty = true;
    const btn = document.getElementById('settings-save-btn');
    if (btn) btn.disabled = false;
    const resetBtn = document.getElementById('settings-reset-btn');
    if (resetBtn) resetBtn.classList.remove('hidden');
    const status = document.getElementById('settings-save-status');
    if (status) { status.textContent = '⚠️ Unsaved changes'; status.className = 'save-status'; }
};

window.saveSettings = async function() {
    const btn = document.getElementById('settings-save-btn');
    const status = document.getElementById('settings-save-status');
    btn.disabled = true;
    btn.textContent = '⏳ Saving...';
    status.textContent = 'Saving...';
    status.className = 'save-status';

    const payload = {};
    for (const key of Object.keys(_settingsOriginal)) {
        const el = document.getElementById('set-' + key);
        if (!el) continue;
        if (el.type === 'checkbox') {
            payload[key] = el.checked;
        } else if (el.type === 'number') {
            payload[key] = el.value !== '' ? Number(el.value) : null;
        } else {
            payload[key] = el.value;
        }
    }

    try {
        const res = await fetchWithTimeout('/api/dashboard/settings', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload),
        }, 15000);
        const data = await res.json();
        if (data.status === 'ok') {
            status.textContent = '✅ ' + data.message;
            status.className = 'save-status success';
            _settingsDirty = false;
    document.getElementById('settings-reset-btn').classList.add('hidden');
        } else {
            const errList = data.errors ? data.errors.join('; ') : '';
            status.textContent = '⚠️ ' + (data.message || 'Partial error') + (errList ? ': ' + errList : '');
            status.className = 'save-status error';
            btn.disabled = false;
            btn.textContent = '💾 Save Changes';
            return;
        }
    } catch(e) {
        if (e.name === 'AbortError') return;
        status.textContent = '❌ Network error: ' + e.message;
        status.className = 'save-status error';
        btn.disabled = false;
        btn.textContent = '💾 Save Changes';
        return;
    }

    btn.textContent = '✓ Saved';
    setTimeout(() => { btn.textContent = '💾 Save Changes'; btn.disabled = !_settingsDirty; }, 2000);
};

window.resetSettings = function() {
    for (const [key, val] of Object.entries(_settingsOriginal)) {
        const el = document.getElementById('set-' + key);
        if (!el) continue;
        if (el.type === 'checkbox') el.checked = !!val;
        else el.value = val == null ? '' : String(val);
    }
    _settingsDirty = false;
    const btn = document.getElementById('settings-save-btn');
    if (btn) btn.disabled = true;
    document.getElementById('settings-reset-btn').style.display = 'none';
    const status = document.getElementById('settings-save-status');
    if (status) { status.textContent = '↺ Reset to original values'; status.className = 'save-status'; }
};


