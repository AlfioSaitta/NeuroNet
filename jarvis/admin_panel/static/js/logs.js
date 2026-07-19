// ═══════════════════════════════════════════════════
// NeuroNet Admin Panel — Log Viewer
// ═══════════════════════════════════════════════════

let logInterval = null;

function openLogModal() {
    switchView('logs');
    loadContainers();
    fetchLogs();
    if (document.getElementById('log-auto-refresh')?.checked) {
        if (logInterval) clearInterval(logInterval);
        logInterval = setInterval(fetchLogs, 5000);
    }
}

function toggleAutoRefresh() {
    if (logInterval) { clearInterval(logInterval); logInterval = null; }
    if (document.getElementById('log-auto-refresh').checked) {
        logInterval = setInterval(fetchLogs, 5000);
    }
}

async function loadContainers() {
    try {
        const res = await fetchWithTimeout('/api/dashboard/containers');
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
        if (e.name === 'AbortError') return;
        console.error('Failed to load containers', e);
        showToast('Failed to load containers', 'error');
    }
}

async function fetchLogs() {
    const container = document.getElementById('log-container-select').value;
    const display = document.getElementById('log-display');
    setLoading(display, true, 'Loading logs...');
    try {
        const res = await fetchWithTimeout(`/api/dashboard/containers/${encodeURIComponent(container)}/logs?tail=500`);
        const data = await res.json();
        if (data.logs) {
            display.textContent = data.logs.map(l => `[${l.container}] ${l.message}`).join('\n');
            requestAnimationFrame(() => { display.scrollTop = display.scrollHeight; });
        } else if (data.error) {
            display.textContent = 'Error: ' + data.error;
        }
    } catch(e) {
        if (e.name === 'AbortError') {
            display.textContent = 'Request timed out';
            return;
        }
        display.textContent = 'Failed to fetch logs: ' + (e.message || e);
    } finally {
        setLoading(display, false);
    }
}

async function restartContainer(name) {
    if (!confirm('Restart container "' + name + '"?')) return;
    try {
        const res = await fetchWithTimeout(`/api/dashboard/containers/${encodeURIComponent(name)}/restart`, { method: 'POST' });
        const data = await res.json();
        if (data.status === 'restarting') {
            showToast('Container restarting...', 'success');
            setTimeout(fetchStats, 3000);
        } else {
            showToast('Failed to restart container', 'error');
        }
    } catch(e) {
        if (e.name === 'AbortError') return;
        console.error('Failed to restart', name, e);
        showToast('Failed to restart container', 'error');
    }
}

async function restartIngestion() {
    if (!confirm('Restart document ingestion?')) return;
    try {
        await fetchWithTimeout('/api/dashboard/ingestion/restart', { method: 'POST' });
        showToast('Ingestion restarting...', 'success');
    } catch(e) {
        if (e.name === 'AbortError') return;
        console.error('Failed to restart ingestion', e);
        showToast('Failed to restart ingestion', 'error');
    }
}
