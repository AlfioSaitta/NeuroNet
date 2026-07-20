// ═══════════════════════════════════════════════════
// NeuroNet Admin Panel — Management views (Code Graph, Models, Tasks, Cron, Settings, Analytics)
// ═══════════════════════════════════════════════════

// ── Projects ──

async function loadProjects() {
    const container = document.getElementById('projects-container');
    const countEl = document.getElementById('projects-count');
    if (!container) return;
    container.innerHTML = '<p class="text-muted" style="text-align:center;padding:2rem;">Loading...</p>';
    try {
        const res = await fetchWithTimeout('/api/projects');
        const data = await res.json();
        if (!data.projects || data.projects.length === 0) {
            container.innerHTML = '<p class="text-muted" style="text-align:center;padding:2rem;">No projects</p>';
            if (countEl) countEl.textContent = '';
            return;
        }
        container.innerHTML = data.projects.map(p => {
            const pathShort = p.path ? p.path.replace(/^.*?([^/]{2,}[^/]{0,20})$/, '...$1') : '—';
            const lastIdx = p.last_indexed ? new Date(p.last_indexed * 1000).toLocaleString() : 'never';
            const badgeClass = 'badge-' + (p.source || 'orphan');
            return `<div class="card project-card" style="margin:0;">
                <div class="project-card-header">
                    <span class="mono fw-600">${escapeHtml(p.name)}</span>
                    <span class="${badgeClass}">${escapeHtml(p.source || 'orphan')}</span>
                    <span class="flex-1"></span>
                    <span class="status-dot status-${p.status || 'red'}" title="Status: ${p.status}"></span>
                </div>
                <div class="project-card-body">
                    <div class="project-stat">
                        <span class="stat-label">Points</span>
                        <span class="stat-value">${p.points ?? '?'}</span>
                    </div>
                    <div class="project-stat">
                        <span class="stat-label">Dimension</span>
                        <span class="stat-value">${p.dimension ?? '?'}</span>
                    </div>
                    <div class="project-stat" style="grid-column:span 2;">
                        <span class="stat-label">Path</span>
                        <span class="stat-value mono text-muted text-xs" title="${escapeHtml(p.path || '')}">${escapeHtml(pathShort)}</span>
                    </div>
                    <div class="project-stat" style="grid-column:span 2;">
                        <span class="stat-label">Collection</span>
                        <span class="stat-value mono text-muted text-xs">${escapeHtml(p.collection_name)}</span>
                    </div>
                    <div class="project-stat">
                        <span class="stat-label">Last Indexed</span>
                        <span class="stat-value text-muted text-xs">${lastIdx}</span>
                    </div>
                    <div class="project-stat">
                        <span class="stat-label">Status</span>
                        <span class="stat-value text-xs">${p.status || 'unknown'}</span>
                    </div>
                </div>
                <div class="project-card-actions">
                    <button class="btn btn-xs" onclick="reindexProject('${escapeHtml(p.name)}')">⟳ Re-index</button>
                    <button class="btn btn-xs btn-outline" onclick="deleteProjectCollection('${escapeHtml(p.name)}')">🗑️ Delete Collection</button>
                </div>
            </div>`;
        }).join('');
        if (countEl) countEl.textContent = data.projects.length + ' project' + (data.projects.length !== 1 ? 's' : '');
    } catch(e) {
        if (e.name === 'AbortError') return;
        container.innerHTML = '<p class="text-muted" style="text-align:center;padding:2rem;color:var(--danger);">Error loading projects</p>';
    }
}

async function reindexProject(name) {
    try {
        const res = await fetchWithTimeout('/api/projects/reindex', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({name}),
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            showToast(err.detail || 'Re-index failed', 'error');
            return;
        }
        const data = await res.json();
        showToast(data.message || 'Re-index started', 'success');
        setTimeout(loadProjects, 2000);
    } catch(e) {
        showToast('Error: ' + e.message, 'error');
    }
}

async function deleteProjectCollection(name) {
    if (!confirm('Delete collection for "' + name + '"? This cannot be undone.')) return;
    try {
        const res = await fetchWithTimeout('/api/projects/' + encodeURIComponent(name) + '/collection', { method: 'DELETE' });
        if (res.ok) {
            showToast('Collection for ' + name + ' deleted', 'success');
            loadProjects();
        } else {
            const err = await res.json().catch(() => ({}));
            showToast('Error: ' + (err.detail || 'Cannot delete'), 'error');
        }
    } catch(e) {
        showToast('Error: ' + e.message, 'error');
    }
}

function openRegisterProjectModal() {
    document.getElementById('register-project-modal').style.display = 'flex';
    document.getElementById('register-project-path').value = '';
    document.getElementById('register-project-name').value = '';
    document.getElementById('register-project-msg').textContent = '';
    loadAvailableProjects();
}

function closeRegisterProjectModal() {
    document.getElementById('register-project-modal').style.display = 'none';
}

async function registerProject(event) {
    event.preventDefault();
    const path = document.getElementById('register-project-path').value.trim();
    const name = document.getElementById('register-project-name').value.trim();
    const msgEl = document.getElementById('register-project-msg');
    if (!path || !name) { msgEl.textContent = 'Both fields required'; return; }
    msgEl.textContent = 'Registering...';
    try {
        const res = await fetchWithTimeout('/api/projects/register', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({path, name}),
        });
        const data = await res.json();
        if (res.ok) {
            msgEl.textContent = '';
            showToast(data.message || 'Project registered', 'success');
            closeRegisterProjectModal();
            loadProjects();
        } else {
            msgEl.textContent = data.detail || 'Registration failed';
        }
    } catch(e) {
        msgEl.textContent = 'Connection error';
    }
}

async function loadAvailableProjects() {
    const listEl = document.getElementById('available-projects-list');
    if (!listEl) return;
    listEl.innerHTML = '<p class="text-muted">Loading...</p>';
    try {
        const res = await fetchWithTimeout('/api/projects/available');
        const data = await res.json();
        if (!data.candidates || data.candidates.length === 0) {
            listEl.innerHTML = '<p class="text-muted">No unindexed projects found in workspace.</p>';
            return;
        }
        listEl.innerHTML = data.candidates.map(c =>
            `<div class="clickable-candidate" onclick="document.getElementById('register-project-path').value='${escapeHtml(c.path)}';document.getElementById('register-project-name').value='${escapeHtml(c.name)}';" style="cursor:pointer;padding:4px 8px;border-radius:4px;display:flex;gap:8px;align-items:center;">
                <span class="badge badge-${c.source}">${escapeHtml(c.source)}</span>
                <span class="mono text-sm">${escapeHtml(c.name)}</span>
                <span class="text-muted text-xs">${escapeHtml(c.path)}</span>
            </div>`
        ).join('');
    } catch(e) {
        listEl.innerHTML = '<p class="text-muted" style="color:var(--danger);">Error loading candidates</p>';
    }
}

async function populateProjectSelect(selectedProjects) {
    const select = document.getElementById('user-allowed-projects');
    if (!select || select.tagName !== 'SELECT') return;
    try {
        const res = await fetch('/api/projects');
        const data = await res.json();
        select.innerHTML = '<option value="*">* (All projects)</option>';
        if (data.projects) {
            data.projects.forEach(p => {
                const opt = document.createElement('option');
                opt.value = p.name;
                opt.textContent = p.name;
                if (selectedProjects && selectedProjects.includes(p.name)) {
                    opt.selected = true;
                }
                select.appendChild(opt);
            });
        }
        if (selectedProjects && selectedProjects.length === 1 && selectedProjects[0] === '*') {
            select.value = ['*'];
        }
    } catch(e) {
        // Fallback a input text se API non risponde
        const val = (selectedProjects || []).join(', ');
        select.outerHTML = '<input type="text" id="user-allowed-projects" placeholder="e.g. NeuroNet, SlotBuilder or * for all" value="' + escapeHtml(val) + '">';
    }
}

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

        // Advanced mode toggle
        const advancedMode = localStorage.getItem('settings_advanced_mode') === 'true';
        let advancedCount = 0;
        let visibleCount = 0;
        for (const [, items] of Object.entries(categories)) {
            for (const item of items) {
                if (!item.basic) advancedCount++;
                if (advancedMode || item.basic) visibleCount++;
            }
        }

        let html = '';
        html += '<div class="settings-mode-bar">';
        html += '<div class="settings-mode-info">';
        html += `<span class="settings-mode-label">Simple Mode</span>`;
        html += `<label class="toggle-switch toggle-switch-sm"><input type="checkbox" id="advanced-mode-toggle" ${advancedMode ? 'checked' : ''} onchange="toggleAdvancedMode()"><span class="slider"></span></label>`;
        html += `<span class="settings-mode-label">Advanced Mode</span>`;
        if (!advancedMode && advancedCount > 0) {
            html += `<span class="settings-mode-extra">+${advancedCount} advanced settings hidden</span>`;
        }
        html += '</div></div>';

        for (const [cat, items] of Object.entries(categories)) {
            // Filter items and check if any are visible
            const visibleItems = items.filter(item => advancedMode || item.basic);
            if (visibleItems.length === 0) continue;

            html += '<div class="card settings-card p-16">';
            html += `<div class="card-header card-header-sm">${cat}</div>`;
            for (const item of visibleItems) {
                _settingsOriginal[item.key] = item.value;
                html += '<div class="setting-row">';
                html += '<div class="setting-info"><div class="setting-label">';
                html += escapeHtml(item.label);
                if (item.restart_required) {
                    html += ' <span class="restart-badge" title="Requires restart to take effect">⚡</span>';
                }
                if (item.unit) {
                    html += ` <span class="unit-label">${escapeHtml(item.unit)}</span>`;
                }
                html += '</div>';
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
                    html += `<input type="number" id="set-${item.key}" value="${escapeHtml(String(item.value ?? ''))}" oninput="markSettingsDirty()" class="mono text-sm" ${item.min != null ? `min="${item.min}"` : ''} ${item.max != null ? `max="${item.max}"` : ''} ${item.step != null ? `step="${item.step}"` : ''}>`;
                } else if (item.type === 'float') {
                    html += `<input type="number" id="set-${item.key}" value="${escapeHtml(String(item.value ?? ''))}" oninput="markSettingsDirty()" class="mono text-sm" ${item.step != null ? `step="${item.step}"` : 'step="any"'} ${item.min != null ? `min="${item.min}"` : ''} ${item.max != null ? `max="${item.max}"` : ''}>`;
                } else if (item.type === 'select') {
                    const opts = item.options || [];
                    html += `<select id="set-${item.key}" onchange="markSettingsDirty()" class="mono text-sm">`;
                    for (const o of opts) {
                        const sel = String(item.value) === o ? ' selected' : '';
                        html += `<option value="${escapeHtml(o)}"${sel}>${escapeHtml(o)}</option>`;
                    }
                    html += `</select>`;
                } else if (item.type === 'secret') {
                    html += `<div style="display:flex;gap:4px;align-items:center;"><input type="password" id="set-${item.key}" value="${escapeHtml(String(item.value ?? ''))}" oninput="markSettingsDirty()" class="mono text-sm" style="flex:1;min-width:100px;"><button type="button" onclick="toggleSecret('set-${item.key}')" style="background:none;border:none;cursor:pointer;color:var(--text-muted);font-size:0.8rem;padding:4px;" title="Show/Hide">👁️</button></div>`;
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
        } else if (el.tagName === 'SELECT') {
            payload[key] = el.value;
        } else if (el.type === 'number') {
            payload[key] = el.value !== '' ? parseFloat(el.value) : null;
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
        else if (el.tagName === 'SELECT') el.value = val == null ? '' : String(val);
        else el.value = val == null ? '' : String(val);
    }
    _settingsDirty = false;
    const btn = document.getElementById('settings-save-btn');
    if (btn) btn.disabled = true;
    document.getElementById('settings-reset-btn').style.display = 'none';
    const status = document.getElementById('settings-save-status');
    if (status) { status.textContent = '↺ Reset to original values'; status.className = 'save-status'; }
};

window.toggleSecret = function(inputId) {
    const el = document.getElementById(inputId);
    if (el) el.type = el.type === 'password' ? 'text' : 'password';
};

window.toggleAdvancedMode = function() {
    const enabled = document.getElementById('advanced-mode-toggle').checked;
    localStorage.setItem('settings_advanced_mode', enabled);
    // Reload settings to re-render with new filter
    loadSettingsData();
};

// ═══════════════════════════════════════════════════
// Users CRUD
// ═══════════════════════════════════════════════════

async function loadUsers() {
    const tbody = document.getElementById('users-table-body');
    const countEl = document.getElementById('users-count');
    if (!tbody) return;
    tbody.innerHTML = '<tr><td colspan="7" class="empty-row">Loading...</td></tr>';
    try {
        const res = await fetchWithTimeout('/api/users');
        if (!res.ok) { tbody.innerHTML = '<tr><td colspan="7" class="empty-row">Access denied</td></tr>'; return; }
        const users = await res.json();
        if (!users.length) {
            tbody.innerHTML = '<tr><td colspan="7" class="empty-row">No users yet</td></tr>';
            if (countEl) countEl.textContent = '0 users';
            return;
        }
        tbody.innerHTML = users.map(u => {
            const projects = Array.isArray(u.allowed_projects) ? u.allowed_projects.join(', ') : (u.allowed_projects || '');
            return `<tr>
                <td><strong>${escapeHtml(u.username)}</strong></td>
                <td><span class="user-role-badge role-${u.role === 'admin' ? 'admin' : 'user'}">${u.role}</span></td>
                <td>${escapeHtml(u.display_name || '')}</td>
                <td class="text-muted">${u.telegram_id || '—'}</td>
                <td class="text-xs" style="max-width:200px;overflow:hidden;text-overflow:ellipsis;">${projects || '—'}</td>
                <td>${u.is_active ? '<span class="text-primary">Active</span>' : '<span class="text-muted">Inactive</span>'}</td>
                <td class="actions">
                    <button class="btn btn-xs" onclick="openUserModal('${u.id}')">Edit</button>
                    <button class="btn btn-xs btn-outline" onclick="deleteUser('${u.id}')">Delete</button>
                </td>
            </tr>`;
        }).join('');
        if (countEl) countEl.textContent = users.length + ' user' + (users.length !== 1 ? 's' : '');
    } catch (e) {
        if (e.name === 'AbortError') return;
        tbody.innerHTML = '<tr><td colspan="7" class="empty-row" style="color:var(--danger);">Error loading users</td></tr>';
    }
}

function openUserModal(userId) {
    const modal = document.getElementById('user-modal');
    const title = document.getElementById('user-modal-title');
    const idField = document.getElementById('user-id');
    const usernameField = document.getElementById('user-username');
    const passwordField = document.getElementById('user-password');
    const displayField = document.getElementById('user-display-name');
    const roleField = document.getElementById('user-role');
    const tgField = document.getElementById('user-telegram-id');
    const activeField = document.getElementById('user-is-active');

    if (!userId) {
        // New user
        title.textContent = 'New User';
        idField.value = '';
        usernameField.value = '';
        passwordField.value = '';
        passwordField.required = true;
        passwordField.placeholder = 'Required for new users';
        displayField.value = '';
        roleField.value = 'user';
        tgField.value = '';
        activeField.checked = true;
        populateProjectSelect([]);
    } else {
        // Edit existing
        title.textContent = 'Edit User';
        passwordField.required = false;
        passwordField.placeholder = 'Leave empty to keep current';
        // Load user data
        fetch('/api/users/' + userId).then(r => r.json()).then(u => {
            if (!u) return;
            idField.value = u.id;
            usernameField.value = u.username || '';
            displayField.value = u.display_name || '';
            roleField.value = u.role || 'user';
            tgField.value = u.telegram_id || '';
            activeField.checked = u.is_active !== false;
            const projects = Array.isArray(u.allowed_projects) ? u.allowed_projects : [];
            populateProjectSelect(projects);
        }).catch(() => {});
    }
    modal.style.display = 'block';
}

function closeUserModal() {
    document.getElementById('user-modal').style.display = 'none';
}

async function saveUser(e) {
    e.preventDefault();
    const id = document.getElementById('user-id').value;
    const username = document.getElementById('user-username').value.trim();
    const password = document.getElementById('user-password').value;
    const displayName = document.getElementById('user-display-name').value.trim();
    const role = document.getElementById('user-role').value;
    const telegramId = document.getElementById('user-telegram-id').value.trim();
    const projectsEl = document.getElementById('user-allowed-projects');
    const isActive = document.getElementById('user-is-active').checked;

    if (!username) { showToast('Username required'); return; }

    let allowed_projects = [];
    if (projectsEl && projectsEl.tagName === 'SELECT') {
        const selected = Array.from(projectsEl.selectedOptions).map(o => o.value);
        if (selected.includes('*')) {
            allowed_projects = ['*'];
        } else {
            allowed_projects = selected;
        }
    } else if (projectsEl) {
        // Fallback a input text
        const projectsRaw = projectsEl.value.trim();
        if (projectsRaw === '*') {
            allowed_projects = ['*'];
        } else if (projectsRaw) {
            allowed_projects = projectsRaw.split(',').map(s => s.trim()).filter(Boolean);
        }
    }

    const body = { username, display_name: displayName, role, telegram_id: telegramId || null, allowed_projects, is_active: isActive };
    if (password) body.password = password;

    try {
        const isNew = !id;
        const res = await fetch(id ? '/api/users/' + id : '/api/users', {
            method: id ? 'PUT' : 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (res.ok) {
            const data = await res.json().catch(() => ({}));
            closeUserModal();
            loadUsers();
            // If a new user was created, show the API key
            if (isNew && data.api_key) {
                showToast('✅ User created! API key: ' + data.api_key + ' (copy now)');
            } else {
                showToast(id ? '✅ User updated.' : '✅ User created.');
            }
        } else {
            const data = await res.json().catch(() => ({}));
            showToast('Error: ' + (data.detail || 'Unknown error'));
        }
    } catch (e) {
        showToast('Connection error');
    }
}

async function deleteUser(userId) {
    if (!confirm('Delete this user? This action cannot be undone.')) return;
    try {
        const res = await fetch('/api/users/' + userId, { method: 'DELETE' });
        if (res.ok) {
            showToast('✅ User deleted.');
            loadUsers();
        } else {
            const data = await res.json().catch(() => ({}));
            showToast('Error: ' + (data.detail || 'Cannot delete'));
        }
    } catch (e) {
        showToast('Connection error');
    }
}


