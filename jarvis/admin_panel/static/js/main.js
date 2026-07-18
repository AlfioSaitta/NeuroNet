// ═══════════════════════════════════════════════════
// NeuroNet Admin Panel — Core: theme, sidebar, view switching
// ═══════════════════════════════════════════════════

function switchView(viewName) {
    document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
    document.querySelectorAll('.sidebar-item').forEach(i => i.classList.remove('active'));
    const view = document.getElementById('view-' + viewName);
    if (view) view.classList.add('active');
    const item = document.querySelector(`.sidebar-item[data-view="${viewName}"]`);
    if (item) item.classList.add('active');
    // Close mobile sidebar
    const sb = document.getElementById('sidebar');
    if (sb && window.innerWidth <= 600) sb.classList.remove('open');
    // Dispatch view load events
    if (viewName === 'chat') { document.getElementById('chat-input')?.focus(); loadSessionList(); loadChatHistory(); }
    if (viewName === 'rag') loadRAGData();
    if (viewName === 'models') loadModelsData();
    if (viewName === 'tasks') { loadTasksData(); loadCronData(); }
    if (viewName === 'logs') { loadContainers(); fetchLogs(); if (document.getElementById('log-auto-refresh')?.checked) { if (logInterval) clearInterval(logInterval); logInterval = setInterval(fetchLogs, 5000); } }
    if (viewName === 'analytics') loadAnalyticsData();
    if (viewName === 'settings') loadSettingsData();
    if (viewName === 'graph') loadGraphCollections();
    if (viewName === 'monitor') { /* already polls via setInterval */ }
}

function toggleTheme() {
    const html = document.documentElement;
    const isLight = html.getAttribute('data-theme') === 'light';
    html.setAttribute('data-theme', isLight ? 'dark' : 'light');
    document.getElementById('theme-icon').textContent = isLight ? '🌙' : '☀️';
    document.getElementById('theme-label').textContent = isLight ? 'Dark' : 'Light';
    localStorage.setItem('neuronet-theme', isLight ? 'dark' : 'light');
    // Re-init mermaid with correct theme
    try { mermaid.initialize({ startOnLoad: false, theme: isLight ? 'default' : 'dark' }); } catch(e) {}
}

function toggleSidebar() {
    document.getElementById('sidebar')?.classList.toggle('open');
}

function toggleChatSidebar() {
    document.getElementById('chat-session-sidebar')?.classList.toggle('open');
}

// Close overlays on Escape
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
        document.getElementById('sidebar')?.classList.remove('open');
        document.getElementById('chat-session-sidebar')?.classList.remove('open');
    }
});

// Init theme from localStorage
(function() {
    const saved = localStorage.getItem('neuronet-theme');
    if (saved === 'light') {
        document.documentElement.setAttribute('data-theme', 'light');
        document.getElementById('theme-icon').textContent = '☀️';
        document.getElementById('theme-label').textContent = 'Light';
    }
})();
