// ═══════════════════════════════════════════════════
// NeuroNet Admin Panel — Core: theme, sidebar, view switching
// ═══════════════════════════════════════════════════

// ── Auth ──────────────────────────────────────────────

window.currentUser = null;

async function checkAuth() {
    try {
        const res = await fetch('/api/auth/me');
        if (res.status === 401) {
            window.location.href = '/admin/login';
            return;
        }
        const user = await res.json();
        window.currentUser = user;
        // Show user info in navbar dropdown
        const ui = document.getElementById('user-info');
        if (ui) {
            ui.style.display = 'flex';
            const displayName = user.display_name || user.username;
            document.getElementById('user-name').textContent = displayName;
            document.getElementById('dropdown-user-name').textContent = displayName;
            const roleLabel = user.role === 'admin' ? 'Admin' : 'User';
            document.getElementById('dropdown-user-role').textContent = roleLabel;
            const badge = document.getElementById('user-role-badge');
            if (badge) {
                badge.textContent = roleLabel;
                badge.className = 'user-role-badge role-' + (user.role === 'admin' ? 'admin' : 'user');
            }
        }
        updateSidebarForRole(user.role);
    } catch (e) {
        window.location.href = '/admin/login';
    }
}

function updateSidebarForRole(role) {
    const isAdmin = role === 'admin';
    document.querySelectorAll('.admin-only').forEach(el => {
        el.style.display = isAdmin ? '' : 'none';
    });
}

async function logout() {
    await fetch('/api/auth/logout', { method: 'POST' });
    window.location.href = '/admin/login';
}

// ── User dropdown ──────────────────────────────

function toggleUserDropdown(e) {
    if (e) e.stopPropagation();
    document.getElementById('user-info').classList.toggle('open');
}

function closeUserDropdown() {
    document.getElementById('user-info').classList.remove('open');
}

// Close dropdown on outside click
document.addEventListener('click', function(e) {
    const dd = document.getElementById('user-info');
    if (dd && dd.classList.contains('open') && !dd.contains(e.target)) {
        dd.classList.remove('open');
    }
});

// ── View switching ────────────────────────────────────

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
    if (viewName === 'models') loadModelsData();
    if (viewName === 'users') loadUsers();
    if (viewName === 'profile') { loadProfile(); loadApiKeys(); }
    if (viewName === 'tasks') { loadTasksData(); loadCronData(); }
    if (viewName === 'logs') { document.getElementById('log-container-select').value = 'all'; loadContainers(); fetchLogs(); if (document.getElementById('log-auto-refresh')?.checked) { if (logInterval) clearInterval(logInterval); logInterval = setInterval(fetchLogs, 5000); } }
    if (viewName === 'analytics') loadAnalyticsData();
    if (viewName === 'settings') loadSettingsData();
    if (viewName === 'graph') loadGraphCollections();
    if (viewName === 'monitor') { resizeAllCharts(); /* already polls via setInterval */ }
}

// ── Theme ──────────────────────────────────────────────

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

// ── Profile functions ────────────────────────────────

async function loadProfile() {
    try {
        const res = await fetch('/api/auth/me');
        if (!res.ok) return;
        const user = await res.json();
        document.getElementById('profile-username').textContent = user.username;
        const roleEl = document.getElementById('profile-role');
        if (roleEl) {
            roleEl.textContent = user.role === 'admin' ? 'Admin' : 'User';
            roleEl.className = 'role-badge role-' + (user.role === 'admin' ? 'admin' : 'user');
        }
        // Telegram
        const tgInput = document.getElementById('telegram-id');
        const rmBtn = document.getElementById('telegram-remove-btn');
        if (tgInput && user.telegram_id) {
            tgInput.value = user.telegram_id;
            rmBtn.style.display = '';
        } else if (rmBtn) {
            rmBtn.style.display = 'none';
        }
    } catch (e) { /* ignore */ }
}

async function loadApiKeys() {
    try {
        const res = await fetch('/api/auth/api-key');
        if (!res.ok) return;
        const data = await res.json();
        const container = document.getElementById('api-key-list');
        if (!container) return;
        if (!data.keys || data.keys.length === 0) {
            container.innerHTML = '<p class="text-muted text-sm">No API keys yet.</p>';
            return;
        }
        container.innerHTML = data.keys.map(k => {
            const status = k.is_active ? 'Active' : 'Revoked';
            const lastUsed = k.last_used_at ? new Date(k.last_used_at * 1000).toLocaleString() : 'Never';
            const revokeBtn = k.is_active
                ? `<button class="btn btn-xs btn-outline" onclick="revokeApiKey('${k.id}')">Revoke</button>`
                : '';
            const copyBtn = k.is_active
                ? `<button class="btn btn-xs" onclick="copyFullApiKey('${k.id}')" title="Copy full key (available for 5 min after generation)">📋</button>`
                : '';
            return `<div class="api-key-row">
                <code class="api-key-prefix">${k.key_prefix}...</code>
                <span class="text-xs text-muted">${k.name || ''}</span>
                <span class="text-xs ${k.is_active ? 'text-primary' : 'text-muted'}">${status}</span>
                <span class="text-xs text-muted">Last: ${lastUsed}</span>
                ${copyBtn}
                ${revokeBtn}
            </div>`;
        }).join('');
    } catch (e) { /* ignore */ }
}

async function generateNewKey() {
    try {
        const res = await fetch('/api/auth/api-key', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ rotate: false, name: 'default' }),
        });
        if (!res.ok) return;
        const data = await res.json();
        const display = document.getElementById('api-key-new-display');
        const valueEl = document.getElementById('api-key-value');
        if (display && valueEl) {
            valueEl.textContent = data.key;
            display.style.display = 'block';
            display.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }
        loadApiKeys();
    } catch (e) { /* ignore */ }
}

async function regenerateApiKey() {
    if (!confirm('Generate a new API key? The current key will be revoked and cannot be recovered.')) return;
    try {
        const res = await fetch('/api/auth/api-key', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ rotate: true, name: 'default' }),
        });
        if (!res.ok) return;
        const data = await res.json();
        // Show the new key in a warning card
        const display = document.getElementById('api-key-new-display');
        const valueEl = document.getElementById('api-key-value');
        if (display && valueEl) {
            valueEl.textContent = data.key;
            display.style.display = 'block';
        }
        // Reload key list
        loadApiKeys();
    } catch (e) { /* ignore */ }
}

let _newApiKeyText = '';

function _copyText(text) {
    // Try modern clipboard API first, fallback to legacy execCommand
    if (navigator.clipboard && navigator.clipboard.writeText) {
        return navigator.clipboard.writeText(text);
    }
    return new Promise((resolve, reject) => {
        const ta = document.createElement('textarea');
        ta.value = text;
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        try {
            document.execCommand('copy');
            document.body.removeChild(ta);
            resolve();
        } catch (e) {
            document.body.removeChild(ta);
            reject(e);
        }
    });
}

async function copyFullApiKey(keyId) {
    try {
        const res = await fetch(`/api/auth/api-key/${keyId}/reveal`);
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            showToast(err.detail || 'Cannot recover this key (expired). Generate a new one.', 4000);
            return;
        }
        const data = await res.json();
        await _copyText(data.key);
        showToast('📋 Full API key copied to clipboard!');
    } catch (e) {
        showToast('Failed to copy key. Try again.', 3000);
    }
}

function copyNewApiKey() {
    const el = document.getElementById('api-key-value');
    if (!el || !el.textContent) return;
    _copyText(el.textContent).then(() => {
        showToast('API key copied to clipboard!');
        el.style.transition = 'background 0.2s';
        el.style.background = 'rgba(var(--primary-rgb), 0.15)';
        setTimeout(() => { el.style.background = ''; }, 400);
    }).catch(() => { /* fallback */ });
}

async function revokeApiKey(keyId) {
    if (!confirm('Revoke this API key? This cannot be undone.')) return;
    try {
        const res = await fetch(`/api/auth/api-key/${keyId}/revoke`, { method: 'POST' });
        if (res.ok) {
            showToast('API key revoked.');
            loadApiKeys();
        }
    } catch (e) { /* ignore */ }
}

async function changePassword(e) {
    e.preventDefault();
    const oldPw = document.getElementById('old-password').value;
    const newPw = document.getElementById('new-password').value;
    const msg = document.getElementById('password-msg');
    if (!oldPw || !newPw) { msg.textContent = 'Both fields required.'; return; }
    if (newPw.length < 8) { msg.textContent = 'Password must be 8+ characters.'; return; }
    try {
        const res = await fetch('/api/auth/change-password', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ old_password: oldPw, new_password: newPw }),
        });
        if (res.ok) {
            msg.textContent = '✅ Password changed.';
            msg.style.color = 'var(--primary)';
            document.getElementById('old-password').value = '';
            document.getElementById('new-password').value = '';
        } else {
            const data = await res.json().catch(() => ({}));
            msg.textContent = data.detail || 'Error changing password.';
            msg.style.color = 'var(--danger)';
        }
    } catch (e) {
        msg.textContent = 'Connection error.';
        msg.style.color = 'var(--danger)';
    }
}

async function saveTelegram(e) {
    e.preventDefault();
    const tgId = document.getElementById('telegram-id').value.trim();
    const msg = document.getElementById('telegram-msg');
    try {
        const res = await fetch('/api/auth/telegram', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ telegram_id: tgId || null }),
        });
        if (res.ok) {
            msg.textContent = '✅ Telegram ID saved.';
            msg.style.color = 'var(--primary)';
            const rmBtn = document.getElementById('telegram-remove-btn');
            if (rmBtn) rmBtn.style.display = tgId ? '' : 'none';
        } else {
            const data = await res.json().catch(() => ({}));
            msg.textContent = data.detail || 'Error saving Telegram ID.';
            msg.style.color = 'var(--danger)';
        }
    } catch (e) {
        msg.textContent = 'Connection error.';
        msg.style.color = 'var(--danger)';
    }
}

async function removeTelegram() {
    document.getElementById('telegram-id').value = '';
    document.getElementById('telegram-form').dispatchEvent(new Event('submit'));
}
