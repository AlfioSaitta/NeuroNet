// ═══════════════════════════════════════════════════
// NeuroNet Admin Panel — Graph Visualization (Sigma.js)
// ═══════════════════════════════════════════════════

let isModalOpen = false;
let sigmaInstance = null;
let fa2Layout = null;
let selectedNodeId = null;
let allNodes = [];
let allLinks = [];
let hoveredNode = null;
let graphResizeObserver = null;
let fa2AutoPauseTimer = null;

const graphFilter = { ext: 'ALL', query: '', minDegree: 0, group: 'ALL' };

const EXT_COLORS = {
    '.py': '#3572A5', '.js': '#F7DF1E', '.ts': '#3178C6', '.tsx': '#3178C6',
    '.jsx': '#61DAFB', '.md': '#083FA1', '.html': '#E34F26', '.css': '#563D7C',
    '.json': '#292929', '.txt': '#888888', '.yaml': '#6CB4EE', '.yml': '#6CB4EE',
    '.go': '#00ADD8', '.rs': '#DEA584', '.cpp': '#F34B7D', '.c': '#555555',
    '.java': '#ED8B00', '.sql': '#E38C00',
    'entity': '#b388ff', 'memory': '#00e5ff',
};
const EXT_NAMES = { '.py': 'Python', '.js': 'JavaScript', '.ts': 'TypeScript', '.tsx': 'TSX',
    '.jsx': 'JSX', '.md': 'Markdown', '.html': 'HTML', '.css': 'CSS',
    '.json': 'JSON', '.yaml': 'YAML', '.yml': 'YAML', '.go': 'Go',
    '.rs': 'Rust', '.cpp': 'C++', '.c': 'C', '.java': 'Java', '.sql': 'SQL',
    'entity': 'Entity', 'memory': 'Memory',
};

function toggleFA2() {
    if (!fa2Layout) return;
    const btn = document.getElementById('btn-pause-fa2');
    const status = document.getElementById('graph-status');
    if (fa2Layout.running) {
        fa2Layout.stop();
        btn.textContent = '▶';
        status.textContent = '⏸ Paused';
        status.style.color = 'var(--warning)';
    } else {
        fa2Layout.start();
        btn.textContent = '⏸';
        status.textContent = '⚡ Simulating';
        status.style.color = 'var(--primary)';
    }
}

function zoomToFitGraph() {
    if (!sigmaInstance) return;
    sigmaInstance.getCamera().animatedReset({ duration: 300 });
}

function computeGraphStats(sigmaGraph) {
    const n = sigmaGraph.order;
    const e = sigmaGraph.size;
    const avgDeg = n > 0 ? (2 * e / n) : 0;
    const density = n > 1 ? (2 * e / (n * (n - 1))) : 0;
    return { nodes: n, edges: e, avgDegree: avgDeg.toFixed(1), density: (density * 100).toFixed(2) };
}

function updateGraphStats(containerId, stats) {
    const el = document.getElementById(containerId || 'graph-stats');
    if (!el) return;
    el.style.display = 'block';
    el.innerHTML = `N: ${stats.nodes} | E: ${stats.edges} | Avg Deg: ${stats.avgDegree} | ρ: ${stats.density}%`;
}

function getNodeDegree(nodeId, links) {
    let deg = 0;
    links.forEach(l => {
        const sId = l.source?.id || l.source;
        const tId = l.target?.id || l.target;
        if (sId === nodeId || tId === nodeId) deg++;
    });
    return deg;
}

function computeDegreeMap(links) {
    const deg = {};
    links.forEach(l => {
        const sId = l.source?.id || l.source;
        const tId = l.target?.id || l.target;
        deg[sId] = (deg[sId] || 0) + 1;
        deg[tId] = (deg[tId] || 0) + 1;
    });
    return deg;
}

function getConnectedNodeIds(nodeId, links) {
    const connected = new Set();
    connected.add(nodeId);
    links.forEach(l => {
        const sId = l.source?.id || l.source;
        const tId = l.target?.id || l.target;
        if (sId === nodeId) connected.add(tId);
        if (tId === nodeId) connected.add(sId);
    });
    return connected;
}

function buildGroupLayout(nodes, getGroupFn) {
    const groups = {};
    nodes.forEach(n => {
        const g = getGroupFn(n);
        if (!groups[g]) groups[g] = [];
        groups[g].push(n.id);
    });
    const groupKeys = Object.keys(groups).sort();
    const positions = {};
    groupKeys.forEach((g, gi) => {
        const ids = groups[g];
        const radius = (gi + 1) * 35;
        ids.forEach((id, ni) => {
            const angle = (2 * Math.PI * ni) / ids.length;
            positions[id] = {
                x: radius * Math.cos(angle) + (Math.random() - 0.5) * 5,
                y: radius * Math.sin(angle) + (Math.random() - 0.5) * 5,
            };
        });
    });
    return positions;
}

function setupResizeObserver(containerId, renderer) {
    if (graphResizeObserver) graphResizeObserver.disconnect();
    const container = document.getElementById(containerId);
    if (!container || !renderer) return;
    graphResizeObserver = new ResizeObserver(() => {
        try { renderer.getCamera().animatedReset({ duration: 200 }); } catch(e) {}
    });
    graphResizeObserver.observe(container);
}

function showGraphTooltip(e, text) {
    const tt = document.getElementById('graph-tooltip');
    if (!tt) return;
    tt.textContent = text;
    tt.style.display = 'block';
    tt.style.left = (e.pageX + 12) + 'px';
    tt.style.top = (e.pageY + 12) + 'px';
}

function hideGraphTooltip() {
    const tt = document.getElementById('graph-tooltip');
    if (tt) tt.style.display = 'none';
}

function updateFilterCount(sigmaGraph) {
    const el = document.getElementById('filter-count');
    if (!el || !sigmaGraph) return;
    let visible = 0, total = sigmaGraph.order;
    sigmaGraph.forEachNode(n => { if (!sigmaGraph.getNodeAttribute(n, 'hidden')) visible++; });
    el.textContent = `${visible}/${total}`;
    el.style.display = total > 0 ? 'inline' : 'none';
}

function applyGraphFilter() {
    if (!sigmaInstance) return;
    graphFilter.ext = document.getElementById('file-type-filter').value;
    graphFilter.query = document.getElementById('node-search').value.toLowerCase();
    const minDegInput = document.getElementById('min-degree-input');
    graphFilter.minDegree = minDegInput ? parseInt(minDegInput.value) || 0 : 0;
    const groupFilter = document.getElementById('group-type-filter');
    graphFilter.group = groupFilter ? groupFilter.value : 'ALL';
    document.getElementById('node-info').style.display = "none";
    selectedNodeId = null;
    sigmaInstance.refresh();
}

function closeModal() {
    document.getElementById('graph-modal').style.display = "none";
    document.getElementById('node-info').style.display = "none";
    document.getElementById('graph-legend').style.display = "none";
    document.getElementById('graph-controls').style.display = "none";
    document.getElementById('graph-stats').style.display = "none";
    document.getElementById('filter-count').style.display = "none";
    hideGraphTooltip();
    if(fa2AutoPauseTimer) {
        clearTimeout(fa2AutoPauseTimer);
        fa2AutoPauseTimer = null;
    }
    if(graphResizeObserver) {
        graphResizeObserver.disconnect();
        graphResizeObserver = null;
    }
    if(fa2Layout) {
        fa2Layout.kill();
        fa2Layout = null;
    }
    if(sigmaInstance) {
        sigmaInstance.kill();
        sigmaInstance = null;
    }
    document.getElementById('graph-container').innerHTML = '';
    hoveredNode = null;
    isModalOpen = false;
}

// ── Shared Sigma renderer (extracted from duplicated code) ──

async function renderSigmaGraph(config) {
    const {
        points, links, title, errorPrefix, filterField,
        createNode, createEdge, getLegendHTML, setupFilter,
        onNodeClick, onEdgeClick, hoverLabel,
    } = config;

    isModalOpen = true;
    document.getElementById('graph-modal').style.display = "block";
    document.getElementById('modal-title').innerText = title;
    document.getElementById('node-info').style.display = "none";
    document.getElementById('graph-container').innerHTML = '';
    document.getElementById('filter-container').style.display = "none";
    document.getElementById('node-search').value = '';
    selectedNodeId = null;
    graphFilter.ext = 'ALL';
    graphFilter.query = '';

    allNodes = points;
    allLinks = links;

    if (!points || points.length === 0) {
        document.getElementById('modal-title').innerText = `${errorPrefix} — No data found`;
        return;
    }

    // Compute degree
    const degree = computeDegreeMap(links);
    const maxDeg = Math.max(1, ...Object.values(degree));
    const nodeCount = points.length;

    // Degree stats for hub labels
    const degValues = Object.values(degree);
    const avgDeg = degValues.reduce((a, b) => a + b, 0) / Math.max(1, degValues.length);
    const stdDeg = Math.sqrt(degValues.reduce((sq, d) => sq + (d - avgDeg) ** 2, 0) / Math.max(1, degValues.length));
    const hubThreshold = avgDeg + stdDeg;

    // Legend
    const legendEl = document.getElementById('graph-legend');
    legendEl.style.display = 'flex';
    legendEl.innerHTML = typeof getLegendHTML === 'function' ? getLegendHTML() : '';

    // Setup filter
    if (typeof setupFilter === 'function') setupFilter();
    if (nodeCount > 0) {
        document.getElementById('filter-container').style.display = "flex";
    }

    // Smart initial positions by group
    const positions = buildGroupLayout(points, n => n.ext || n.group || 'unknown');

    // Build graphology graph
    const sigmaGraph = new window.__graphology();

    points.forEach(p => {
        const pdeg = degree[p.id] || 0;
        const nodeAttrs = typeof createNode === 'function'
            ? createNode(p, pdeg, maxDeg, hubThreshold, nodeCount)
            : {};
        const pos = positions[p.id] || { x: Math.random() * 200 - 100, y: Math.random() * 200 - 100 };
        sigmaGraph.addNode(p.id, {
            x: pos.x,
            y: pos.y,
            degree: pdeg,
            payload: p.payload || {},
            ...nodeAttrs,
        });
    });

    links.forEach(l => {
        const source = l.source?.id || l.source;
        const target = l.target?.id || l.target;
        if (sigmaGraph.hasNode(source) && sigmaGraph.hasNode(target)) {
            const edgeAttrs = typeof createEdge === 'function'
                ? createEdge(l, source, target)
                : { color: 'rgba(0, 255, 204, 0.4)', size: 1, similarity: 0.5 };
            sigmaGraph.addEdge(source, target, edgeAttrs);
        }
    });

    // Warm-up with synchronous FA2
    window.__fa2.assign(sigmaGraph, {
        iterations: nodeCount > 500 ? 80 : 50,
        settings: {
            barnesHutOptimize: nodeCount > 500,
            gravity: 0.5,
            scalingRatio: nodeCount > 500 ? 5 : 2,
        },
    });

    // Continuous FA2 in Web Worker
    const layout = new window.__fa2Worker(sigmaGraph, {
        settings: {
            barnesHutOptimize: nodeCount > 500,
            gravity: 0.5,
            scalingRatio: nodeCount > 500 ? 5 : 2,
        },
    });
    layout.start();
    fa2Layout = layout;

    // Auto-pause after 30 seconds
    if (fa2AutoPauseTimer) clearTimeout(fa2AutoPauseTimer);
    fa2AutoPauseTimer = setTimeout(() => {
        if (fa2Layout && fa2Layout.running) {
            fa2Layout.stop();
            document.getElementById('btn-pause-fa2').textContent = '▶';
            document.getElementById('graph-status').textContent = '✓ Stabilized';
            document.getElementById('graph-status').style.color = 'var(--primary)';
        }
    }, 30000);

    // Show FA2 controls
    document.getElementById('graph-controls').style.display = 'flex';
    document.getElementById('btn-pause-fa2').textContent = '⏸';
    document.getElementById('graph-status').textContent = '⚡ Simulating';
    document.getElementById('graph-status').style.color = 'var(--primary)';

    // Create sigma renderer with error recovery
    let renderer;
    try {
        renderer = new window.__sigma(sigmaGraph, document.getElementById('graph-container'), {
            renderEdgeLabels: false,
            enableEdgeEvents: true,
            labelRenderedSizeThreshold: 6,
            labelDensity: 0.3,
            minCameraRatio: 0.05,
            maxCameraRatio: 10,
            nodeReducer: (node, data) => {
                const pdeg = sigmaGraph.getNodeAttribute(node, 'degree') || 0;
                if (graphFilter.minDegree > 0 && pdeg < graphFilter.minDegree) {
                    return { ...data, hidden: true };
                }
                const filterVal = data[filterField];
                if (graphFilter.ext !== 'ALL' && filterVal !== graphFilter.ext) {
                    return { ...data, hidden: true };
                }
                if (graphFilter.query) {
                    const payload = data.payload || {};
                    const txt = (payload.text || payload.data || JSON.stringify(payload)).toLowerCase();
                    if (!txt.includes(graphFilter.query) && !String(node).toLowerCase().includes(graphFilter.query)) {
                        return { ...data, hidden: true };
                    }
                }
                if (hoveredNode) {
                    const connected = getConnectedNodeIds(hoveredNode, allLinks);
                    if (!connected.has(node)) {
                        return { ...data, color: '#444', size: data.size * 0.5, label: '' };
                    }
                    if (node === hoveredNode) {
                        return { ...data, color: '#ff00ff', size: data.size * 2.0 };
                    }
                    return { ...data, size: data.size * 1.3 };
                }
                if (node === selectedNodeId) {
                    return { ...data, color: '#ff00ff', size: data.size * 1.5 };
                }
                return data;
            },
            edgeReducer: (edge, data) => {
                const [src, tgt] = sigmaGraph.extremities(edge);
                const srcAttrs = sigmaGraph.getNodeAttributes(src);
                const tgtAttrs = sigmaGraph.getNodeAttributes(tgt);
                if (srcAttrs.hidden || tgtAttrs.hidden) return { ...data, hidden: true };
                if (hoveredNode) {
                    const connected = getConnectedNodeIds(hoveredNode, allLinks);
                    const sOk = connected.has(src);
                    const tOk = connected.has(tgt);
                    if (sOk && tOk) {
                        return { ...data, color: 'rgba(0, 255, 204, 0.7)', size: 2 };
                    }
                    return { ...data, color: 'rgba(100, 100, 100, 0.05)', size: 0.3 };
                }
                return data;
            },
        });

        sigmaInstance = renderer;

        // Show stats
        const stats = computeGraphStats(sigmaGraph);
        updateGraphStats('graph-stats', stats);

        // Resize observer
        setupResizeObserver('graph-container', renderer);

        // Node click
        renderer.on('clickNode', ({ node }) => {
            selectedNodeId = node;
            const attrs = sigmaGraph.getNodeAttributes(node);
            const infoBox = document.getElementById('node-info');
            const contentBox = document.getElementById('node-content');
            infoBox.style.display = "block";
            contentBox.innerHTML = typeof onNodeClick === 'function'
                ? onNodeClick(node, attrs, sigmaGraph)
                : '<div class="property-row"><div class="property-label">Node</div><div class="property-value">' + escapeHtml(node) + '</div></div>';
            contentBox.querySelectorAll('pre code').forEach((block) => {
                hljs.highlightElement(block);
            });
            renderer.refresh();
        });

        // Hover events
        renderer.on('enterNode', ({ node }) => {
            document.getElementById('graph-container').style.cursor = 'pointer';
            hoveredNode = node;
            const attrs = sigmaGraph.getNodeAttributes(node);
            const label = typeof hoverLabel === 'function'
                ? hoverLabel(node, attrs)
                : `Node — ${attrs.degree || 0} connections`;
            showGraphTooltip(
                window.event || { pageX: 0, pageY: 0 },
                `${label}\nID: ${node.substring(0, 24)}...`
            );
            renderer.refresh();
        });

        renderer.on('leaveNode', () => {
            document.getElementById('graph-container').style.cursor = '';
            hoveredNode = null;
            hideGraphTooltip();
            renderer.refresh();
        });

        renderer.on('clickStage', () => {
            selectedNodeId = null;
            document.getElementById('node-info').style.display = "none";
            renderer.refresh();
        });

        renderer.on('clickEdge', ({ edge }) => {
            const [src, tgt] = sigmaGraph.extremities(edge);
            const sim = sigmaGraph.getEdgeAttribute(edge, 'similarity') || '?';
            selectedNodeId = null;
            const infoBox = document.getElementById('node-info');
            const contentBox = document.getElementById('node-content');
            infoBox.style.display = "block";
            contentBox.innerHTML = typeof onEdgeClick === 'function'
                ? onEdgeClick(edge, src, tgt, sigmaGraph)
                : `
                    <div class="property-row"><div class="property-label">Source</div><div class="property-value">${escapeHtml(src)}</div></div>
                    <div class="property-row"><div class="property-label">Target</div><div class="property-value">${escapeHtml(tgt)}</div></div>
                    <div class="property-row"><div class="property-label">Similarity</div><div class="property-value" style="color:var(--primary)">${typeof sim === 'number' ? (sim * 100).toFixed(1) + '%' : sim}</div></div>
                `;
            renderer.refresh();
        });

        // Mousemove for tooltip
        document.getElementById('graph-container').addEventListener('mousemove', (e) => {
            if (hoveredNode) {
                const attrs = sigmaGraph.getNodeAttributes(hoveredNode);
                const label = typeof hoverLabel === 'function'
                    ? hoverLabel(hoveredNode, attrs)
                    : `Node — ${attrs.degree || 0} connections`;
                showGraphTooltip(e, `${label}\nID: ${hoveredNode.substring(0, 24)}...`);
            }
        });

        // Fit view
        setTimeout(() => {
            renderer.getCamera().animatedReset({ duration: 400 });
        }, 200);

        // Update filter count
        updateFilterCount(sigmaGraph);

    } catch(e) {
        console.error(e);
        document.getElementById('modal-title').innerText = `⚠️ ${errorPrefix} render error: ${e.message}`;
    }
}

// ── Vector Graph ──

async function openGraphModal(collectionName) {
    try {
        const res = await fetchWithTimeout(`/api/dashboard/qdrant/${collectionName}/vectors`, {}, 30000);
        const data = await res.json();

        const points = data.points || [];
        const links = data.links || [];
        const nodeCount = points.length;

        if (nodeCount === 0) {
            isModalOpen = true;
            document.getElementById('graph-modal').style.display = "block";
            document.getElementById('modal-title').innerText = `${collectionName} - No Vectors Found`;
            return;
        }

        // Build extension set for legend
        const extSet = new Set();
        points.forEach(p => {
            let ext = "Unknown";
            const potentialFields = [p.payload.file_path, p.payload.source, p.payload.url];
            for (let field of potentialFields) {
                if (typeof field === 'string' && field.includes('.')) {
                    const parts = field.split('?')[0].split('#')[0].split('.');
                    ext = "." + parts[parts.length - 1].toLowerCase();
                    if (ext.length > 6 || !/^\.[a-z0-9]+$/.test(ext)) {
                        ext = "Unknown";
                    } else {
                        break;
                    }
                }
            }
            if (ext === "Unknown" && p.payload) {
                const payloadStr = JSON.stringify(p.payload);
                const match = payloadStr.match(/[\w-]+\.(js|py|tsx|ts|jsx|md|html|css|txt|json|yaml|yml|go|rs|cpp|c|java|sql)\b/i);
                if(match) ext = "." + match[1].toLowerCase();
            }
            extSet.add(ext);
            p.ext = ext;
        });

        const sortedExts = Array.from(extSet).sort();
        const legendHTML = sortedExts.map(ext =>
            `<div class="legend-row"><span class="legend-dot" style="background:${EXT_COLORS[ext] || '#888'}"></span> ${EXT_NAMES[ext] || ext}</div>`
        ).join('');

        // Legend can also be built directly in renderSigmaGraph
        const customLegendHTML = sortedExts.map(ext =>
            `<div class="legend-row"><span class="legend-dot" style="background:${EXT_COLORS[ext] || '#888'}"></span> ${EXT_NAMES[ext] || ext}</div>`
        ).join('');

        await renderSigmaGraph({
            points,
            links,
            title: `${collectionName} — Graph Network (${nodeCount} vectors)`,
            errorPrefix: 'Vector Graph',
            filterField: 'ext',
            getLegendHTML: () => customLegendHTML,
            setupFilter: () => {
                const filterSelect = document.getElementById('file-type-filter');
                filterSelect.innerHTML = '<option value="ALL">All Files</option>';
                sortedExts.forEach(e => {
                    const opt = document.createElement('option');
                    opt.value = e;
                    opt.innerText = EXT_NAMES[e] || e.toUpperCase();
                    filterSelect.appendChild(opt);
                });
            },
            createNode: (p, pdeg, maxDeg, hubThreshold, nc) => {
                const ext = p.ext || 'Unknown';
                const size = Math.max(3, Math.min(20, 1 + pdeg / maxDeg * 4));
                const isHub = pdeg > hubThreshold;
                return {
                    label: isHub
                        ? `${EXT_NAMES[ext] || ext} — ${pdeg} connections`
                        : (nc < 200 ? `${EXT_NAMES[ext] || ext} — ${pdeg} conn` : ''),
                    size: nc > 500 ? size * 0.6 : size,
                    color: EXT_COLORS[ext] || '#888888',
                    ext: ext,
                };
            },
            createEdge: (l) => {
                const sim = l.similarity || 0.5;
                return {
                    color: `rgba(0, 255, 204, ${Math.max(0.15, Math.min(0.8, (sim - 0.35) * 3))})`,
                    size: Math.max(0.5, Math.min(3, sim * 4)),
                    similarity: sim,
                };
            },
            onNodeClick: (node, attrs) => {
                let html = `<div class="property-row"><div class="property-label">Vector ID</div><div class="property-value">${escapeHtml(node)}</div></div>`;
                if(attrs.payload.text || attrs.payload.data) {
                    const mainText = attrs.payload.text || attrs.payload.data;
                    html += `<div class="property-row"><div class="property-label">Primary Text</div><div class="property-value"><pre><code class="language-javascript" style="white-space: pre-wrap; padding: 8px; border-radius: 4px;">${escapeHtml(mainText)}</code></pre></div></div>`;
                }
                const payloadStr = JSON.stringify(attrs.payload, null, 2);
                html += `<div class="property-row"><div class="property-label">Raw Payload (JSON)</div><pre><code class="language-json" style="padding: 8px; border-radius: 4px;">${escapeHtml(payloadStr)}</code></pre></div>`;
                return html;
            },
            onEdgeClick: (edge, src, tgt, sigmaGraph) => {
                const sim = sigmaGraph.getEdgeAttribute(edge, 'similarity') || '?';
                return `
                    <div class="property-row"><div class="property-label">Source</div><div class="property-value">${escapeHtml(src)}</div></div>
                    <div class="property-row"><div class="property-label">Target</div><div class="property-value">${escapeHtml(tgt)}</div></div>
                    <div class="property-row"><div class="property-label">Similarity</div><div class="property-value" style="color:var(--primary)">${typeof sim === 'number' ? (sim * 100).toFixed(1) + '%' : sim}</div></div>
                `;
            },
            hoverLabel: (node, attrs) => {
                return `${EXT_NAMES[attrs.ext] || attrs.ext} — ${attrs.degree || 0} connections`;
            },
        });
    } catch(e) {
        console.error(e);
        document.getElementById('modal-title').innerText = "Error Loading Graph";
    }
}

// ── Memory Graph ──

async function openMemoryGraphModal() {
    try {
        const res = await fetchWithTimeout('/api/dashboard/graph/memory', {}, 30000);
        const data = await res.json();

        const points = data.points || [];
        const links = data.links || [];

        if (points.length === 0) {
            isModalOpen = true;
            document.getElementById('graph-modal').style.display = "block";
            document.getElementById('modal-title').innerText = 'Memory Graph — no entity links yet. Run /api/graph/reindex first.';
            return;
        }

        const msg = data.memory_count ? ` (${data.entity_count} entities ↔ ${data.memory_count} memories)` : '';
        const nodeCount = points.length;

        await renderSigmaGraph({
            points,
            links,
            title: `Memory Entity Graph${msg} (${nodeCount} nodes, ${links.length} links)`,
            errorPrefix: 'Memory Graph',
            filterField: 'group',
            getLegendHTML: () => `
                <div class="legend-row"><span class="legend-dot" style="background:#b388ff"></span> Entity</div>
                <div class="legend-row"><span class="legend-dot" style="background:#00e5ff"></span> Memory</div>
            `,
            setupFilter: () => {
                const filterSelect = document.getElementById('file-type-filter');
                filterSelect.innerHTML = '<option value="ALL">All Types</option><option value="entity">Entity</option><option value="memory">Memory</option>';
                const minDegContainer = document.getElementById('min-degree-input');
                if (minDegContainer) minDegContainer.style.display = 'inline-block';
            },
            createNode: (p, pdeg, maxDeg, hubThreshold, nc) => {
                const group = p.group || 'memory';
                const isEntity = group === 'entity';
                const size = isEntity
                    ? Math.max(5, Math.min(25, 2 + pdeg / maxDeg * 6))
                    : Math.max(3, Math.min(15, 1 + pdeg / maxDeg * 4));
                const isHub = pdeg > hubThreshold;
                const entityName = p.payload?.entity_name || '';
                return {
                    label: isEntity
                        ? (isHub ? `Entity: ${entityName} (${pdeg})` : (nc < 200 ? `Entity: ${entityName}` : ''))
                        : (isHub ? `Memory (${pdeg} connections)` : (nc < 200 ? `Memory (${pdeg})` : '')),
                    size: nc > 500 ? size * 0.7 : size,
                    color: isEntity ? '#b388ff' : '#00e5ff',
                    group: group,
                };
            },
            createEdge: () => ({
                color: 'rgba(179, 136, 255, 0.35)',
                size: 1.2,
                similarity: 0.5,
            }),
            onNodeClick: (node, attrs) => {
                const isEntity = attrs.group === 'entity';
                let html = `<div class="property-row"><div class="property-label">Type</div><div class="property-value">${isEntity ? '🔮 Entity' : '🧠 Memory'}</div></div>`;
                if (isEntity) {
                    html += `<div class="property-row"><div class="property-label">Entity Name</div><div class="property-value">${escapeHtml(attrs.payload.entity_name)}</div></div>`;
                    html += `<div class="property-row"><div class="property-label">Connected Memories</div><div class="property-value">${attrs.payload.connected_memories || 0}</div></div>`;
                    if (attrs.payload.entity_type) {
                        html += `<div class="property-row"><div class="property-label">Entity Type</div><div class="property-value">${escapeHtml(attrs.payload.entity_type)}</div></div>`;
                    }
                } else {
                    const memText = attrs.payload.memory || '';
                    html += `<div class="property-row"><div class="property-label">Memory Excerpt</div><div class="property-value"><pre><code class="language-javascript" style="white-space: pre-wrap; padding: 8px; border-radius: 4px;">${escapeHtml(memText)}</code></pre></div></div>`;
                    if (attrs.payload.entity_count) {
                        html += `<div class="property-row"><div class="property-label">Connected Entities</div><div class="property-value">${attrs.payload.entity_count}</div></div>`;
                    }
                }
                html += `<div class="property-row"><div class="property-label">Node ID</div><div class="property-value" style="font-size:0.7rem;color:var(--text-muted);">${escapeHtml(node)}</div></div>`;
                return html;
            },
            onEdgeClick: (edge, src, tgt, sigmaGraph) => {
                const sim = sigmaGraph.getEdgeAttribute(edge, 'similarity') || '?';
                return `
                    <div class="property-row"><div class="property-label">Source</div><div class="property-value">${escapeHtml(src)}</div></div>
                    <div class="property-row"><div class="property-label">Target</div><div class="property-value">${escapeHtml(tgt)}</div></div>
                    <div class="property-row"><div class="property-label">Similarity</div><div class="property-value" style="color:var(--primary)">${typeof sim === 'number' ? (sim * 100).toFixed(1) + '%' : sim}</div></div>
                `;
            },
            hoverLabel: (node, attrs) => {
                const isEntity = attrs.group === 'entity';
                return isEntity
                    ? `Entity: ${attrs.payload.entity_name || '?'} — ${attrs.degree || 0} connections`
                    : `Memory — ${attrs.degree || 0} connections`;
            },
        });
    } catch(e) {
        console.error(e);
        document.getElementById('modal-title').innerText = "Error Loading Memory Graph";
    }
}
