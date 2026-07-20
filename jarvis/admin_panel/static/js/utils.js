// ═══════════════════════════════════════════════════
// NeuroNet Admin Panel — Shared Utilities
// ═══════════════════════════════════════════════════

function showToast(msg, type) {
    let toast = document.getElementById('toast');
    if (!toast) {
        toast = document.createElement('div');
        toast.id = 'toast';
        toast.className = 'toast';
        document.body.appendChild(toast);
    }
    toast.textContent = msg;
    toast.className = 'toast ' + (type || '');
    toast.style.display = 'block';
    setTimeout(() => { toast.style.display = 'none'; }, 3000);
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

// ── Fetch with timeout & abort ──

async function fetchWithTimeout(url, options = {}, timeoutMs = 15000) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), timeoutMs);
    try {
        const res = await fetch(url, { ...options, signal: controller.signal });
        // Redirect to login on session expiry (401), unless already on login page
        if (res.status === 401 && !window.location.pathname.includes('/login')) {
            window.location.href = '/admin/login';
            throw new Error('Session expired');
        }
        return res;
    } finally {
        clearTimeout(timeout);
    }
}

// ── Debounce utility ──

function debounce(fn, delayMs = 300) {
    let timer;
    return function(...args) {
        clearTimeout(timer);
        timer = setTimeout(() => fn.apply(this, args), delayMs);
    };
}

// ── Loading state helpers ──

function setLoading(el, loading, msg) {
    if (!el) return;
    if (loading) {
        el.dataset.originalText = el.innerText || el.textContent;
        el.innerHTML = '<span style="opacity:0.5;">' + (msg || 'Loading...') + '</span>';
        el.style.pointerEvents = 'none';
    } else {
        if (el.dataset.originalText) {
            el.innerText = el.dataset.originalText;
            delete el.dataset.originalText;
        }
        el.style.pointerEvents = '';
    }
}

function showLoading(containerId, msg) {
    const el = document.getElementById(containerId);
    if (!el) return;
    el.dataset.originalHtml = el.innerHTML;
    el.innerHTML = '<div class="text-center text-muted" style="padding:20px;"><span style="opacity:0.5;">' + (msg || 'Loading...') + '</span></div>';
}

function clearLoading(containerId) {
    const el = document.getElementById(containerId);
    if (!el || !el.dataset.originalHtml) return;
    el.innerHTML = el.dataset.originalHtml;
    delete el.dataset.originalHtml;
}
