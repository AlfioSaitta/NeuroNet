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
