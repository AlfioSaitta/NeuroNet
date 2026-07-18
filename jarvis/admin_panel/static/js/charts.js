// ═══════════════════════════════════════════════════
// NeuroNet Admin Panel — Charts (Chart.js)
// ═══════════════════════════════════════════════════

let tempChart, vramChart, utilChart, ramChart, cpuChart, cpuTempChart, tokPerSecChart;

function createLineChart(canvasId, color, fillColor, yMin, yMax) {
    const ctx = document.getElementById(canvasId).getContext('2d');
    return new Chart(ctx, {
        type: 'line',
        data: { labels: [], datasets: [{ data: [], borderColor: color, backgroundColor: fillColor, fill: true }] },
        options: {
            responsive: true, maintainAspectRatio: false,
            animation: false, spanGaps: true,
            plugins: { legend: { display: false } },
            scales: {
                x: { display: false, grid: { display: false } },
                y: {
                    min: yMin, max: yMax,
                    grid: { color: 'rgba(255,255,255,0.06)' },
                    ticks: { color: '#94a3b8', font: { size: 8, family: "'JetBrains Mono', monospace" }, maxTicksLimit: 4 }
                }
            },
            elements: {
                point: { radius: 0, hitRadius: 2 },
                line: { borderWidth: 1.5, tension: 0.3 }
            }
        }
    });
}

function initCharts() {
    tempChart = createLineChart('chart-temp', '#00b8ff', 'rgba(0,184,255,0.08)', 30, 100);
    vramChart = createLineChart('chart-vram', '#00ffcc', 'rgba(0,255,204,0.08)', 0, 100);
    utilChart = createLineChart('chart-util', '#7b2cbf', 'rgba(123,44,191,0.08)', 0, 100);
    ramChart = createLineChart('chart-ram', '#ffcc00', 'rgba(255,204,0,0.08)', 0, 100);
    cpuChart = createLineChart('chart-cpu', '#ff8833', 'rgba(255,136,51,0.08)', 0, 100);
    cpuTempChart = createLineChart('chart-cpu-temp', '#ff3366', 'rgba(255,51,102,0.08)', 20, 110);
    tokPerSecChart = createLineChart('chart-tok-per-sec', '#00ffcc', 'rgba(0,255,204,0.08)', 0, 20);
}

function updateCharts(history) {
    if (!history || history.length === 0) return;
    const len = history.length;
    const labels = history.map(() => '');
    const temps = history.map(h => h.temp);
    const vramPcts = history.map(h => h.vram_total ? Math.round(h.vram_used / h.vram_total * 100) : 0);
    const utils = history.map(h => h.util ?? 0);

    const last = history[len-1];
    document.getElementById('chart-current-temp').innerText = last.temp + '°C';
    document.getElementById('chart-current-vram').innerText = last.vram_used + ' MiB';
    document.getElementById('chart-current-util').innerText = (last.util ?? 0) + '%';

    tempChart.data.labels = labels;
    tempChart.data.datasets[0].data = temps;
    tempChart.update('none');

    vramChart.data.labels = labels;
    vramChart.data.datasets[0].data = vramPcts;
    vramChart.update('none');

    utilChart.data.labels = labels;
    utilChart.data.datasets[0].data = utils;
    utilChart.update('none');
}

function updateSysCharts(sysHistory) {
    if (!sysHistory || sysHistory.length === 0) return;
    const labels = sysHistory.map(() => '');
    ramChart.data.labels = labels;
    ramChart.data.datasets[0].data = sysHistory.map(h => h.ram_pct);
    ramChart.update('none');
    cpuChart.data.labels = labels;
    cpuChart.data.datasets[0].data = sysHistory.map(h => h.cpu_pct);
    cpuChart.update('none');
    cpuTempChart.data.labels = labels;
    cpuTempChart.data.datasets[0].data = sysHistory.map(h => h.cpu_temp);
    cpuTempChart.update('none');
    const last = sysHistory[sysHistory.length-1];
    document.getElementById('chart-current-ram').innerText = last.ram_pct + '%';
    document.getElementById('chart-current-cpu').innerText = last.cpu_pct + '%';
    document.getElementById('chart-current-cpu-temp').innerText = (last.cpu_temp ?? '--') + '°C';
}

function updateInfCharts(infHistory) {
    if (!infHistory || infHistory.length === 0) return;
    const labels = infHistory.map(() => '');
    tokPerSecChart.data.labels = labels;
    tokPerSecChart.data.datasets[0].data = infHistory.map(h => h.tokens_per_sec);
    tokPerSecChart.update('none');
    const last = infHistory[infHistory.length-1];
    document.getElementById('chart-current-tok-per-sec').innerText = (last.tokens_per_sec ?? 0) + ' tok/s';
}
