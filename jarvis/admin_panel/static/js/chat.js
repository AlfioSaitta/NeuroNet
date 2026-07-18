// ═══════════════════════════════════════════════════
// NeuroNet Admin Panel — Chat (SSE streaming, markdown, images)
// ═══════════════════════════════════════════════════

let chatImages = [];
let isChatStreaming = false;
let chatConvId = 'dashboard_default';
let abortController = null;
let currentSessionId = 'dashboard_default';

// Mermaid initialized inside DOMContentLoaded below

function showChatView(show) {
    switchView(show ? 'chat' : 'monitor');
}

// ── Session Management ──

async function loadSessionList() {
    try {
        const resp = await fetch('/api/dashboard/sessions?limit=50');
        const data = await resp.json();
        const list = document.getElementById('chat-session-list');
        list.innerHTML = '';
        let found = false;
        for (const s of data.sessions || []) {
            const item = document.createElement('div');
            const isActive = s.conversation_id === currentSessionId;
            if (isActive) found = true;
            item.className = 'session-item' + (isActive ? ' active' : '');
            const title = s.title || s.conversation_id;
            const dt = s.last_activity ? new Date(s.last_activity * 1000).toLocaleString() : '';
            const turns = s.turn_count || 0;
            item.innerHTML = `<div class="si-title-row"><span class="si-title">${escHtml(title.substring(0, 50))}</span>
                <button class="si-del-btn" title="Delete session">✕</button></div>
                <div class="si-meta"><span>${turns} turns</span><span>${dt}</span></div>`;
            item.onclick = () => switchSession(s.conversation_id);
            item.querySelector('.si-del-btn').onclick = (e) => {
                e.stopPropagation();
                deleteSession(s.conversation_id);
            };
            list.appendChild(item);
        }
        // Always show current session if not in list (legacy session)
        if (!found && currentSessionId) {
            const item = document.createElement('div');
            item.className = 'session-item active';
            item.innerHTML = `<div class="si-title-row"><span class="si-title">${escHtml(currentSessionId)}</span></div>
                <div class="si-meta"><span>current</span></div>`;
            item.onclick = () => switchSession(currentSessionId);
            list.prepend(item);
        }
    } catch (e) {
        console.error('Failed to load sessions', e);
    }
}

async function createNewSession() {
    try {
        const resp = await fetch('/api/dashboard/sessions', { method: 'POST' });
        const data = await resp.json();
        if (data.conversation_id) {
            await switchSession(data.conversation_id);
        }
    } catch (e) {
        console.error('Failed to create session', e);
    }
}

async function switchSession(convId) {
    // Clear current messages
    const container = document.getElementById('chat-messages');
    const emptyState = document.getElementById('chat-empty-state');
    container.querySelectorAll('.msg-bubble, .typing-indicator').forEach(m => m.remove());
    currentSessionId = convId;
    chatConvId = convId;
    emptyState.style.display = 'flex';
    // Reload session list to update active highlight
    await loadSessionList();
    // Load messages
    await loadChatHistory();
    scrollChat();
}

async function loadChatHistory() {
    try {
        // Try ChatSessionStore first
        const resp = await fetch('/api/dashboard/sessions/' + encodeURIComponent(currentSessionId) + '/messages');
        const data = await resp.json();
        const container = document.getElementById('chat-messages');
        const emptyState = document.getElementById('chat-empty-state');
        const msgs = container.querySelectorAll('.msg-bubble, .typing-indicator');
        msgs.forEach(m => m.remove());

        if (!data.messages || data.messages.length === 0) {
            // Fallback to legacy chat-history
            const resp2 = await fetch('/api/dashboard/chat-history?conversation_id=' + encodeURIComponent(chatConvId));
            const data2 = await resp2.json();
            if (data2.messages && data2.messages.length > 0) {
                emptyState.style.display = 'none';
                for (const msg of data2.messages) {
                    appendMessage(msg.role, msg.content, false, msg.metrics, msg.timestamp);
                }
                scrollChat();
                return;
            }
            emptyState.style.display = 'flex';
            return;
        }
        emptyState.style.display = 'none';
        for (const msg of data.messages) {
            // Convert ChatSessionStore format to metrics display format
            const ts = msg.timestamp ? msg.timestamp * 1000 : null; // Unix → JS timestamp
            const promptTok = msg.prompt_tokens || 0;
            const completionTok = msg.completion_tokens || 0;
            const durationMs = msg.duration_ms || 0;
            let metrics = null;
            if (durationMs > 0 || promptTok + completionTok > 0) {
                const tokPerSec = durationMs > 0
                    ? Math.round((completionTok / (durationMs / 1000)) * 10) / 10
                    : 0;
                metrics = {
                    ttft_ms: durationMs,
                    tok_per_sec: tokPerSec,
                    tokens: promptTok + completionTok,
                };
            }
            appendMessage(msg.role, msg.content, false, metrics, ts);
        }
        scrollChat();
    } catch (e) {
        console.error('Failed to load chat history', e);
    }
}

async function deleteSession(convId) {
    if (!confirm('Delete this session and all its messages?')) return;
    try {
        const resp = await fetch('/api/dashboard/sessions/' + encodeURIComponent(convId), { method: 'DELETE' });
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        // If deleting the currently active session, create a new one
        if (convId === currentSessionId) {
            await createNewSession();
        } else {
            await loadSessionList();
        }
    } catch (e) {
        console.error('Failed to delete session', e);
        alert('Failed to delete session: ' + e.message);
    }
}

function escHtml(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

function appendMessage(role, content, isStreaming, metrics, timestamp) {
    const container = document.getElementById('chat-messages');
    const emptyState = document.getElementById('chat-empty-state');
    emptyState.style.display = 'none';

    // Remove typing indicator if present
    const typingEl = container.querySelector('.typing-indicator');
    if (typingEl) typingEl.remove();

    let bubble = container.querySelector('.msg-bubble.streaming');
    if (isStreaming && role === 'assistant') {
        if (bubble) {
            bubble.innerHTML = renderMarkdown(content);
            bubble.dataset.fullContent = (bubble.dataset.fullContent || '') + content;
            applyCodeCopy(bubble);
            return bubble;
        }
    }

    bubble = document.createElement('div');
    bubble.className = 'msg-bubble ' + role;
    if (isStreaming) {
        bubble.classList.add('streaming');
        bubble.dataset.fullContent = content || '';
    }

    const contentDiv = document.createElement('div');
    contentDiv.className = 'msg-content';
    contentDiv.innerHTML = renderMarkdown(content || '');
    bubble.appendChild(contentDiv);

    // Determine display timestamp
    let timeStr;
    if (timestamp) {
        const d = new Date(timestamp);
        if (!isNaN(d.getTime())) {
            timeStr = d.toLocaleTimeString();
        } else {
            // Could be a formatted string like "2024-01-15 10:30:00"
            const parsed = new Date(timestamp.replace(' ', 'T'));
            timeStr = !isNaN(parsed.getTime()) ? parsed.toLocaleTimeString() : timestamp;
        }
    } else {
        timeStr = new Date().toLocaleTimeString();
    }

    const meta = document.createElement('div');
    meta.className = 'msg-meta';
    if (role === 'assistant' && metrics && metrics.ttft_ms != null) {
        const tokStr = (metrics.tok_per_sec != null)
            ? `<span style="color:var(--primary);font-family:'JetBrains Mono',monospace;font-size:0.65rem;">TTFT ${metrics.ttft_ms}ms · ${metrics.tok_per_sec} tok/s · ${metrics.tokens || '?'} tok</span>`
            : `<span style="color:var(--text-muted);font-size:0.65rem;">TTFT ${metrics.ttft_ms}ms</span>`;
        meta.innerHTML = `${timeStr} · ${tokStr}`;
    } else {
        meta.textContent = timeStr;
    }
    bubble.appendChild(meta);
    container.appendChild(bubble);

    if (!isStreaming) {
        applyCodeCopy(bubble);
        runMermaid(bubble);
    }
    scrollChat();
    return bubble;
}

function renderMarkdown(text) {
    if (!text) return '';
    let html = marked.parse(text, { breaks: true, gfm: true });
    html = DOMPurify.sanitize(html, { ADD_TAGS: ['svg', 'path', 'circle', 'rect', 'g', 'defs', 'linearGradient', 'stop', 'text', 'tspan', 'marker', 'polygon', 'polyline', 'ellipse', 'line'], ADD_ATTR: ['viewBox', 'xmlns', 'd', 'fill', 'stroke', 'stroke-width', 'stroke-linecap', 'stroke-linejoin', 'cx', 'cy', 'r', 'x', 'y', 'width', 'height', 'rx', 'ry', 'points', 'transform', 'style', 'class', 'id', 'ref', 'marker-end', 'marker-start', 'marker-mid', 'orient', 'refX', 'refY', 'pathLength'] });
    // Wrap tables for responsive
    html = html.replace(/<table>/g, '<div style="overflow-x:auto"><table>').replace(/<\/table>/g, '</table></div>');
    return html;
}

function applyCodeCopy(container) {
    container.querySelectorAll('pre code').forEach((codeBlock) => {
        const pre = codeBlock.closest('pre');
        if (!pre || pre.querySelector('.copy-btn')) return;
        const btn = document.createElement('button');
        btn.className = 'copy-btn';
        btn.textContent = '📋 Copy';
        btn.onclick = async () => {
            try {
                await navigator.clipboard.writeText(codeBlock.textContent);
                btn.textContent = '✅ Copied!';
                setTimeout(() => { btn.textContent = '📋 Copy'; }, 2000);
            } catch { btn.textContent = '❌ Failed'; }
        };
        pre.style.position = 'relative';
        pre.appendChild(btn);
    });
}

function runMermaid(container) {
    container.querySelectorAll('.mermaid').forEach((el) => {
        try { mermaid.run({ nodes: [el] }); } catch (e) { console.warn('Mermaid render failed', e); }
    });
    // Also handle ```mermaid code blocks
    container.querySelectorAll('pre code.language-mermaid').forEach((codeBlock) => {
        const pre = codeBlock.closest('pre');
        if (!pre || pre.querySelector('.mermaid-rendered')) return;
        const wrapper = document.createElement('div');
        wrapper.className = 'mermaid mermaid-rendered';
        wrapper.textContent = codeBlock.textContent;
        pre.replaceWith(wrapper);
        try { mermaid.run({ nodes: [wrapper] }); } catch (e) { console.warn('Mermaid render failed', e); }
    });
}

function updateStreamingMessage(content) {
    const container = document.getElementById('chat-messages');
    let bubble = container.querySelector('.msg-bubble.streaming');
    if (!bubble) {
        bubble = appendMessage('assistant', '', true);
    }
    const fullContent = (bubble.dataset.fullContent || '') + content;
    bubble.dataset.fullContent = fullContent;
    const contentDiv = bubble.querySelector('.msg-content');
    if (contentDiv) {
        contentDiv.innerHTML = renderMarkdown(fullContent);
    }
    applyCodeCopy(bubble);
    scrollChat();
}

function finishStreamingMessage(fullText, ttftMs, tokPerSec, tokens, durationMs) {
    const container = document.getElementById('chat-messages');
    let bubble = container.querySelector('.msg-bubble.streaming');
    if (bubble) {
        bubble.classList.remove('streaming');
        const contentDiv = bubble.querySelector('.msg-content');
        if (contentDiv) {
            contentDiv.innerHTML = renderMarkdown(fullText || bubble.dataset.fullContent || '');
        }
        delete bubble.dataset.fullContent;
        applyCodeCopy(bubble);
        runMermaid(bubble);
        // Add metrics to meta
        const meta = bubble.querySelector('.msg-meta');
        if (meta && ttftMs != null) {
            const timeStr = new Date().toLocaleTimeString();
            const metricsStr = (tokPerSec != null)
                ? `<span style="color:var(--primary);font-family:'JetBrains Mono',monospace;font-size:0.65rem;">TTFT ${ttftMs}ms · ${tokPerSec} tok/s · ${tokens} tok</span>`
                : `<span style="color:var(--text-muted);font-size:0.65rem;">TTFT ${ttftMs}ms</span>`;
            meta.innerHTML = `${timeStr} · ${metricsStr}`;
        }
    }
}

function scrollChat() {
    const container = document.getElementById('chat-messages');
    if (container) container.scrollTop = container.scrollHeight;
}

function addTypingIndicator() {
    const container = document.getElementById('chat-messages');
    const typingEl = container.querySelector('.typing-indicator');
    if (typingEl) return;
    const div = document.createElement('div');
    div.className = 'typing-indicator';
    div.innerHTML = '<span></span><span></span><span></span>';
    container.appendChild(div);
    scrollChat();
}

async function sendChatMessage() {
    const input = document.getElementById('chat-input');
    const text = input.value.trim();
    if (!text && chatImages.length === 0) return;
    if (isChatStreaming) return;

    // Show user message
    appendMessage('user', text, false);

    // Clear input
    input.value = '';
    input.style.height = 'auto';

    const imagesToSend = [...chatImages];
    chatImages = [];
    updateImagePreviews();

    // Show typing indicator
    addTypingIndicator();

    isChatStreaming = true;
    document.getElementById('chat-send-btn').disabled = true;
    abortController = new AbortController();

    try {
        const resp = await fetch('/api/dashboard/chat/stream', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                message: text,
                conversation_id: chatConvId,
                images: imagesToSend.length > 0 ? imagesToSend : undefined
            }),
            signal: abortController.signal
        });

        if (!resp.ok) throw new Error('HTTP ' + resp.status);

        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop() || '';

            for (const line of lines) {
                if (!line.startsWith('data: ')) continue;
                const data = JSON.parse(line.slice(6));
                if (data.error) {
                    console.error('Chat error:', data.error);
                    finishStreamingMessage('');
                    appendMessage('assistant', '⚠️ Error: ' + data.error, false);
                    break;
                }
                if (data.content) {
                    updateStreamingMessage(data.content);
                }
                if (data.done) {
                    finishStreamingMessage(data.full_text || '', data.ttft_ms, data.tok_per_sec, data.tokens, data.duration_ms);
                }
            }
        }
    } catch (e) {
        if (e.name !== 'AbortError') {
            console.error('Chat stream failed:', e);
            finishStreamingMessage('');
            appendMessage('assistant', '⚠️ Connection error: ' + e.message, false);
        }
    } finally {
        isChatStreaming = false;
        document.getElementById('chat-send-btn').disabled = false;
        abortController = null;
        const container = document.getElementById('chat-messages');
        const typingEl = container.querySelector('.typing-indicator');
        if (typingEl) typingEl.remove();
        scrollChat();
    }
}

function sendSuggested(text) {
    document.getElementById('chat-input').value = text;
    sendChatMessage();
}

function handleInputKeydown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendChatMessage();
    }
    // Auto-resize
    const el = e.target;
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 120) + 'px';
}

// ── Image handling ──
function handleFileSelect(e) {
    const files = e.target.files;
    for (const file of files) {
        if (!file.type.startsWith('image/')) continue;
        if (chatImages.length >= 4) break;
        const reader = new FileReader();
        reader.onload = (ev) => {
            const b64 = ev.target.result.split(',')[1];
            chatImages.push(b64);
            updateImagePreviews();
        };
        reader.readAsDataURL(file);
    }
    e.target.value = '';
}

function updateImagePreviews() {
    const container = document.getElementById('chat-image-previews');
    container.innerHTML = '';
    for (let i = 0; i < chatImages.length; i++) {
        const wrapper = document.createElement('div');
        wrapper.className = 'chat-img-preview';
        wrapper.innerHTML = '<img src="data:image/jpeg;base64,' + chatImages[i] + '" alt="Preview">' +
            '<button class="remove-img" onclick="removeImage(' + i + ')" title="Remove">✕</button>';
        container.appendChild(wrapper);
    }
}

function removeImage(index) {
    chatImages.splice(index, 1);
    updateImagePreviews();
}

// ── All event listeners registered on DOMContentLoaded ──
document.addEventListener('DOMContentLoaded', () => {
    // Init mermaid
    mermaid.initialize({ startOnLoad: false, theme: 'dark', themeVariables: { primaryColor: '#00ffcc', primaryTextColor: '#f8fafc', primaryBorderColor: '#00ffcc', lineColor: '#00b8ff', secondaryColor: '#7b2cbf', tertiaryColor: '#05070a' } });

    // Handle paste (images from clipboard)
    document.addEventListener('paste', (e) => {
        if (!document.getElementById('view-chat').classList.contains('active')) return;
        const items = e.clipboardData.items;
        for (const item of items) {
            if (item.type.startsWith('image/') && chatImages.length < 4) {
                const file = item.getAsFile();
                if (!file) continue;
                const reader = new FileReader();
                reader.onload = (ev) => {
                    const b64 = ev.target.result.split(',')[1];
                    chatImages.push(b64);
                    updateImagePreviews();
                };
                reader.readAsDataURL(file);
            }
        }
    });

    // Handle drag-and-drop on chat input area
    const chatInputContainer = document.getElementById('chat-input-container');
    if (chatInputContainer) {
        chatInputContainer.addEventListener('dragover', (e) => {
            e.preventDefault();
            e.stopPropagation();
            chatInputContainer.style.borderColor = 'var(--primary)';
        });
        chatInputContainer.addEventListener('dragleave', (e) => {
            e.preventDefault();
            e.stopPropagation();
            chatInputContainer.style.borderColor = '';
        });
        chatInputContainer.addEventListener('drop', (e) => {
            e.preventDefault();
            e.stopPropagation();
            chatInputContainer.style.borderColor = '';
            const files = e.dataTransfer.files;
            for (const file of files) {
                if (!file.type.startsWith('image/')) continue;
                if (chatImages.length >= 4) break;
                const reader = new FileReader();
                reader.onload = (ev) => {
                    const b64 = ev.target.result.split(',')[1];
                    chatImages.push(b64);
                    updateImagePreviews();
                };
                reader.readAsDataURL(file);
            }
        });
    }

    // Focus input when pressing / anywhere
    document.addEventListener('keydown', (e) => {
        if (e.key === '/' && !['INPUT', 'TEXTAREA'].includes(e.target.tagName)) {
            if (document.getElementById('view-chat').classList.contains('active')) {
                e.preventDefault();
                document.getElementById('chat-input').focus();
            }
        }
    });
});
