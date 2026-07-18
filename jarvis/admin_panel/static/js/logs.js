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
            requestAnimationFrame(() => { display.scrollTop = display.scrollHeight; });
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
