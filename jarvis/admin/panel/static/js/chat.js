// ═══════════════════════════════════════════════════
// NeuroNet Admin Panel — Chat v3 (interattiva)
// ═══════════════════════════════════════════════════

// ── State ──
let chatImages = [];
let isChatStreaming = false;
let chatConvId = 'dashboard_default';
let abortController = null;
let currentSessionId = 'dashboard_default';
let isManuallyScrolled = false;
let _lastUserMessageText = '';

// Streaming state machine (mutalmente esclusivo con isChatStreaming)
let _streamBubble = null;
let _streamFullContent = '';
let _streamEnded = false;
let _msgCounter = 0;

// ── Session Management ──

async function loadSessionList() {
    try {
        const resp = await fetchWithTimeout('/api/dashboard/sessions?limit=50', {}, 10000);
        const data = await resp.json();
        const list = document.getElementById('chat-session-list');
        list.innerHTML = '';
        let found = false;
        for (const s of data.sessions || []) {
            const isActive = s.conversation_id === currentSessionId;
            if (isActive) found = true;
            const item = document.createElement('div');
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
        const resp = await fetchWithTimeout('/api/dashboard/sessions', { method: 'POST' }, 10000);
        const data = await resp.json();
        if (data.conversation_id) {
            await switchSession(data.conversation_id);
        }
    } catch (e) {
        console.error('Failed to create session', e);
    }
}

async function switchSession(convId) {
    const container = document.getElementById('chat-messages');
    const emptyState = document.getElementById('chat-empty-state');
    container.querySelectorAll('.msg-bubble, .typing-indicator').forEach(m => m.remove());
    currentSessionId = convId;
    chatConvId = convId;
    emptyState.style.display = 'flex';
    _streamBubble = null;
    _streamFullContent = '';
    _streamEnded = false;
    await loadSessionList();
    await loadChatHistory();
    scrollChat();
    isManuallyScrolled = false;
    hideScrollDownBtn();
}

async function loadChatHistory() {
    try {
        const resp = await fetchWithTimeout('/api/dashboard/sessions/' + encodeURIComponent(currentSessionId) + '/messages', {}, 15000);
        const data = await resp.json();
        const container = document.getElementById('chat-messages');
        const emptyState = document.getElementById('chat-empty-state');
        container.querySelectorAll('.msg-bubble, .typing-indicator').forEach(m => m.remove());

        let messages = data.messages;
        // Fallback: legacy chat-history endpoint
        if (!messages || messages.length === 0) {
            try {
                const resp2 = await fetchWithTimeout('/api/dashboard/chat-history?conversation_id=' + encodeURIComponent(chatConvId), {}, 15000);
                const data2 = await resp2.json();
                messages = data2.messages;
            } catch {
                messages = null;
            }
        }

        if (!messages || messages.length === 0) {
            emptyState.style.display = 'flex';
            return;
        }

        emptyState.style.display = 'none';
        for (let i = 0; i < messages.length; i++) {
            const msg = messages[i];
            // Timestamp: supporta number (unix sec) o string (ISO)
            let ts = null;
            if (msg.timestamp != null) {
                const d = typeof msg.timestamp === 'number'
                    ? new Date(msg.timestamp * 1000)
                    : new Date(msg.timestamp);
                if (!isNaN(d.getTime())) ts = d.getTime();
            }
            // Metrics
            const promptTok = msg.prompt_tokens || 0;
            const completionTok = msg.completion_tokens || 0;
            const durationMs = msg.duration_ms || 0;
            let metrics = null;
            if (durationMs > 0 || promptTok + completionTok > 0) {
                const tokPerSec = durationMs > 0
                    ? Math.round((completionTok / (durationMs / 1000)) * 10) / 10
                    : 0;
                metrics = { ttft_ms: durationMs, tok_per_sec: tokPerSec, tokens: promptTok + completionTok };
            }
            const modelName = msg.model || null;
            appendMessage(msg.role, msg.content, { metrics, timestamp: ts, index: i, modelName });
        }
    } catch (e) {
        console.error('Failed to load chat history', e);
    }
}

async function deleteSession(convId) {
    if (!confirm('Delete this session and all its messages?')) return;
    try {
        const resp = await fetchWithTimeout('/api/dashboard/sessions/' + encodeURIComponent(convId), { method: 'DELETE' }, 10000);
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
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

// ── Auto-scroll management ──

function scrollChat() {
    const container = document.getElementById('chat-messages');
    if (!container) return;
    if (!isManuallyScrolled) {
        container.scrollTop = container.scrollHeight;
    }
}

function onChatScroll() {
    const container = document.getElementById('chat-messages');
    if (!container) return;
    const threshold = 80;
    const atBottom = container.scrollHeight - container.scrollTop - container.clientHeight < threshold;
    if (atBottom) {
        isManuallyScrolled = false;
        hideScrollDownBtn();
    } else {
        isManuallyScrolled = true;
    }
}

function showScrollDownBtn() {
    const btn = document.getElementById('scroll-down-btn');
    if (btn) btn.classList.add('visible');
}

function hideScrollDownBtn() {
    const btn = document.getElementById('scroll-down-btn');
    if (btn) btn.classList.remove('visible');
}

function scrollToBottom() {
    isManuallyScrolled = false;
    scrollChat();
    hideScrollDownBtn();
}

// ── Markdown Rendering Pipeline ──
// Renderizza markdown → HTML safe via DOMPurify.
// CRITICO: breaks=false — lascia che markdown gestisca i paragrafi
// naturalmente (doppio \n = <p>, singolo \n = spazio). breaks:true
// convertiva ogni \n in <br>, distruggendo liste/code/header.

function renderMarkdown(text) {
    if (!text) return '';
    let html = marked.parse(text, { gfm: true, breaks: false });
    html = DOMPurify.sanitize(html, {
        ADD_TAGS: ['svg', 'path', 'circle', 'rect', 'g', 'defs', 'linearGradient', 'stop', 'text', 'tspan', 'marker', 'polygon', 'polyline', 'ellipse', 'line'],
        ADD_ATTR: ['viewBox', 'xmlns', 'd', 'fill', 'stroke', 'stroke-width', 'stroke-linecap', 'stroke-linejoin', 'cx', 'cy', 'r', 'x', 'y', 'width', 'height', 'rx', 'ry', 'points', 'transform', 'style', 'class', 'id', 'ref', 'marker-end', 'marker-start', 'marker-mid', 'orient', 'refX', 'refY', 'pathLength']
    });
    // Tables: wrap in scrollable container
    html = html.replace(/<table>/g, '<div class="table-wrap"><table>').replace(/<\/table>/g, '</table></div>');
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

// ── Message DOM Construction ──

function createMessageBubble(role, content, opts = {}) {
    const {
        isStreaming = false,
        metrics = null,
        timestamp = null,
        index = null,
        modelName = null,
    } = opts;

    _msgCounter++;
    const mid = 'msg-' + _msgCounter;
    const bubble = document.createElement('div');
    bubble.className = 'msg-bubble ' + role;
    bubble.id = mid;
    if (index !== null) bubble.dataset.msgIndex = index;
    bubble.dataset.fullContent = content || '';

    // Content
    const contentDiv = document.createElement('div');
    contentDiv.className = 'msg-content';
    bubble.appendChild(contentDiv);

    // Actions toolbar
    const actions = document.createElement('div');
    actions.className = 'msg-actions';
    if (role === 'user') {
        actions.innerHTML = `
            <button class="msg-action-btn" data-action="edit" title="Edit">✏️</button>
            <button class="msg-action-btn" data-action="delete" title="Delete">🗑️</button>
        `;
    } else {
        const modelBadge = modelName ? `<span class="msg-model-badge">${escHtml(modelName)}</span>` : '';
        actions.innerHTML = `
            <button class="msg-action-btn" data-action="copy" title="Copy">📋</button>
            <button class="msg-action-btn" data-action="regenerate" title="Regenerate">🔄</button>
            <button class="msg-action-btn" data-action="delete" title="Delete">🗑️</button>
            ${modelBadge}
        `;
    }
    actions.addEventListener('click', (e) => {
        const btn = e.target.closest('.msg-action-btn');
        if (!btn) return;
        handleMessageAction(btn.dataset.action, bubble);
    });
    bubble.appendChild(actions);

    // Meta (timestamp + metrics)
    const meta = document.createElement('div');
    meta.className = 'msg-meta';
    let timeStr;
    if (timestamp) {
        const d = new Date(timestamp);
        timeStr = !isNaN(d.getTime()) ? d.toLocaleTimeString() : new Date().toLocaleTimeString();
    } else {
        timeStr = new Date().toLocaleTimeString();
    }
    if (role === 'assistant' && metrics && metrics.ttft_ms != null) {
        const tokStr = (metrics.tok_per_sec != null)
            ? `<span class="msg-metrics">TTFT ${metrics.ttft_ms}ms · ${metrics.tok_per_sec} tok/s · ${metrics.tokens || '?'} tok</span>`
            : `<span class="msg-metrics">TTFT ${metrics.ttft_ms}ms</span>`;
        meta.innerHTML = `${timeStr} · ${tokStr}`;
    } else {
        meta.textContent = timeStr;
    }
    bubble.appendChild(meta);

    // Render content
    if (isStreaming) {
        bubble.classList.add('streaming');
        // Content rendered by _renderStreamingContent()
    } else {
        contentDiv.innerHTML = renderMarkdown(content);
        applyCodeCopy(bubble);
        runMermaid(bubble);
    }

    return bubble;
}

function appendMessage(role, content, opts = {}) {
    const container = document.getElementById('chat-messages');
    const emptyState = document.getElementById('chat-empty-state');
    emptyState.style.display = 'none';

    // Remove typing indicator
    const typingEl = container.querySelector('.typing-indicator');
    if (typingEl) typingEl.remove();

    const bubble = createMessageBubble(role, content, opts);
    container.appendChild(bubble);
    scrollChat();
    return bubble;
}

// ── Streaming State Machine ──

function _ensureStreamingBubble() {
    if (_streamEnded) return null;
    if (_streamBubble && _streamBubble.parentNode) {
        return _streamBubble;
    }

    const container = document.getElementById('chat-messages');
    const emptyState = document.getElementById('chat-empty-state');
    emptyState.style.display = 'none';

    const typingEl = container.querySelector('.typing-indicator');
    if (typingEl) typingEl.remove();

    _streamFullContent = '';
    _streamBubble = createMessageBubble('assistant', '', { isStreaming: true });
    container.appendChild(_streamBubble);
    scrollChat();
    return _streamBubble;
}

function _renderStreamingContent() {
    if (!_streamBubble) return;
    const contentDiv = _streamBubble.querySelector('.msg-content');
    if (!contentDiv) return;

    contentDiv.innerHTML = renderMarkdown(_streamFullContent);
    applyCodeCopy(_streamBubble);
    scrollChat();
}

function updateStreamingMessage(content) {
    if (_streamEnded) return;
    _streamFullContent += content;
    const bubble = _ensureStreamingBubble();
    if (!bubble) return; // ended
    bubble.dataset.fullContent = _streamFullContent;
    _renderStreamingContent();
}

function finishStreamingMessage(fullText, ttftMs, tokPerSec, tokens, durationMs, reasoning) {
    _streamEnded = true;

    const bubble = _streamBubble;
    _streamBubble = null;

    if (!bubble || !bubble.parentNode) {
        // No streaming bubble exists — create static message if we have text
        if (fullText) {
            appendMessage('assistant', fullText);
        }
        _streamFullContent = '';
        return;
    }

    // Determine final display text
    const displayText = fullText || _streamFullContent || '';
    bubble.dataset.fullContent = displayText;
    bubble.classList.remove('streaming');

    const contentDiv = bubble.querySelector('.msg-content');
    if (contentDiv) {
        let displayHtml = '';

        // Reasoning box (markdown-rendered, non escHtml)
        if (reasoning) {
            displayHtml += '<details class="msg-reasoning">' +
                '<summary>🧠 Pensiero (Ragionamento)</summary>' +
                '<div class="msg-reasoning-content">' + renderMarkdown(reasoning) + '</div>' +
                '</details>';
        }

        // Main content
        displayHtml += renderMarkdown(displayText);
        contentDiv.innerHTML = displayHtml;

        if (reasoning) {
            bubble.dataset.reasoning = reasoning;
        }
    }

    // Update metrics
    const meta = bubble.querySelector('.msg-meta');
    if (meta && ttftMs != null) {
        const timeStr = new Date().toLocaleTimeString();
        const metricsStr = (tokPerSec != null)
            ? `<span class="msg-metrics">TTFT ${ttftMs}ms · ${tokPerSec} tok/s · ${tokens} tok</span>`
            : `<span class="msg-metrics">TTFT ${ttftMs}ms</span>`;
        meta.innerHTML = `${timeStr} · ${metricsStr}`;
    }

    // Post-render hooks
    applyCodeCopy(bubble);
    runMermaid(bubble);
    reindexMessages();
    scrollChat();

    _streamFullContent = '';
}

// ── Typing indicator ──

function addTypingIndicator() {
    const container = document.getElementById('chat-messages');
    if (container.querySelector('.typing-indicator')) return;
    const div = document.createElement('div');
    div.className = 'typing-indicator';
    div.innerHTML = '<span></span><span></span><span></span>';
    container.appendChild(div);
    scrollChat();
}

function removeTypingIndicator() {
    const container = document.getElementById('chat-messages');
    const el = container.querySelector('.typing-indicator');
    if (el) el.remove();
}

// ── Message Actions ──

async function handleMessageAction(action, bubble) {
    switch (action) {
        case 'copy':
            const fullContent = bubble.dataset.fullContent || bubble.querySelector('.msg-content')?.textContent || '';
            try {
                await navigator.clipboard.writeText(fullContent);
                showToast('📋 Copied to clipboard');
            } catch {
                showToast('Failed to copy', 'error');
            }
            break;
        case 'edit':
            startEditMessage(bubble);
            break;
        case 'delete':
            await deleteMessage(bubble);
            break;
        case 'regenerate':
            await regenerateResponse(bubble);
            break;
    }
}

async function deleteMessage(bubble) {
    const index = bubble.dataset.msgIndex;
    if (index !== undefined) {
        try {
            await fetchWithTimeout(
                `/api/dashboard/sessions/${encodeURIComponent(currentSessionId)}/messages/${index}`,
                { method: 'DELETE' },
                5000
            );
        } catch (e) {
            console.warn('Backend delete failed, removing from DOM only', e);
        }
    }
    bubble.remove();
    reindexMessages();
}

function reindexMessages() {
    const container = document.getElementById('chat-messages');
    const bubbles = container.querySelectorAll('.msg-bubble:not(.streaming)');
    bubbles.forEach((b, i) => {
        b.dataset.msgIndex = i;
    });
}

function startEditMessage(bubble) {
    if (bubble.classList.contains('streaming')) return;
    const contentDiv = bubble.querySelector('.msg-content');
    const currentText = bubble.dataset.fullContent || contentDiv?.textContent || '';
    const actions = bubble.querySelector('.msg-actions');

    contentDiv.style.display = 'none';
    if (actions) actions.style.display = 'none';

    const editor = document.createElement('div');
    editor.className = 'msg-edit-container';
    editor.innerHTML = `
        <textarea class="msg-edit-textarea">${escHtml(currentText)}</textarea>
        <div class="msg-edit-actions">
            <button class="btn btn-xs msg-edit-save">Save & Resend</button>
            <button class="btn btn-xs btn-outline msg-edit-cancel">Cancel</button>
        </div>
    `;
    bubble.appendChild(editor);

    const textarea = editor.querySelector('.msg-edit-textarea');
    textarea.focus();
    textarea.setSelectionRange(textarea.value.length, textarea.value.length);
    textarea.style.height = 'auto';
    textarea.style.height = Math.min(textarea.scrollHeight, 200) + 'px';

    editor.querySelector('.msg-edit-save').onclick = async () => {
        const newText = textarea.value.trim();
        if (!newText) return;

        const index = parseInt(bubble.dataset.msgIndex);
        if (!isNaN(index)) {
            try {
                await fetchWithTimeout(
                    `/api/dashboard/sessions/${encodeURIComponent(currentSessionId)}/messages/${index}/edit`,
                    {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ content: newText }),
                    },
                    5000
                );
            } catch (e) {
                console.warn('Backend edit failed', e);
            }
        }

        // Remove all messages after this one
        let next = bubble.nextElementSibling;
        while (next) {
            const toRemove = next;
            next = next.nextElementSibling;
            if (toRemove.classList.contains('msg-bubble') || toRemove.classList.contains('typing-indicator')) {
                toRemove.remove();
            }
        }

        // Update this bubble
        bubble.dataset.fullContent = newText;
        contentDiv.innerHTML = renderMarkdown(newText);
        contentDiv.style.display = '';
        editor.remove();
        if (actions) actions.style.display = '';
        applyCodeCopy(bubble);
        runMermaid(bubble);
        reindexMessages();
        _lastUserMessageText = newText; // ← FIX: update last user text for regenerate
        await sendRawMessage(newText);
    };

    editor.querySelector('.msg-edit-cancel').onclick = () => {
        contentDiv.style.display = '';
        editor.remove();
        if (actions) actions.style.display = '';
    };
}

async function regenerateResponse(bubble) {
    if (isChatStreaming) return;
    if (!_lastUserMessageText) {
        let prev = bubble.previousElementSibling;
        while (prev) {
            if (prev.classList.contains('msg-bubble') && prev.classList.contains('user')) {
                _lastUserMessageText = prev.dataset.fullContent || prev.querySelector('.msg-content')?.textContent || '';
                break;
            }
            prev = prev.previousElementSibling;
        }
    }
    if (!_lastUserMessageText) return;

    // Remove this bubble and all after it
    let next = bubble.nextElementSibling;
    while (next) {
        const toRemove = next;
        next = next.nextElementSibling;
        if (toRemove.classList.contains('msg-bubble') || toRemove.classList.contains('typing-indicator')) {
            toRemove.remove();
        }
    }
    bubble.remove();

    // Truncate session at this point
    const index = parseInt(bubble.dataset.msgIndex);
    if (!isNaN(index)) {
        try {
            await fetchWithTimeout(
                `/api/dashboard/sessions/${encodeURIComponent(currentSessionId)}/truncate`,
                {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ from_index: index }),
                },
                5000
            );
        } catch (e) {
            console.warn('Backend truncate failed', e);
        }
    }

    reindexMessages();
    await sendRawMessage(_lastUserMessageText);
}

// ── Send message ──

async function sendChatMessage() {
    const input = document.getElementById('chat-input');
    const text = input.value.trim();
    if (!text && chatImages.length === 0) return;
    if (isChatStreaming) return;

    _lastUserMessageText = text;
    appendMessage('user', text, {});

    input.value = '';
    input.style.height = 'auto';

    const imagesToSend = [...chatImages];
    chatImages = [];
    updateImagePreviews();

    await sendRawMessage(text, imagesToSend);
}

async function sendRawMessage(text, images = []) {
    addTypingIndicator();

    // Reset streaming state
    _streamBubble = null;
    _streamFullContent = '';
    _streamEnded = false;

    isChatStreaming = true;
    document.getElementById('chat-send-btn').style.display = 'none';
    document.getElementById('chat-stop-btn').style.display = 'flex';
    abortController = new AbortController();

    let streamError = false;

    try {
        const resp = await fetch('/api/dashboard/chat/stream', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                message: text,
                conversation_id: chatConvId,
                images: images.length > 0 ? images : undefined
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
            buffer = lines.pop() || ''; // keep incomplete line in buffer

            for (const line of lines) {
                if (!line.startsWith('data: ')) continue;
                try {
                    const data = JSON.parse(line.slice(6));
                    if (data.error) {
                        console.error('Chat error:', data.error);
                        streamError = true;
                        finishStreamingMessage('');
                        appendMessage('assistant', '⚠️ Error: ' + data.error, {});
                        break;
                    }
                    if (data.content) {
                        updateStreamingMessage(data.content);
                    }
                    if (data.done) {
                        finishStreamingMessage(
                            data.full_text || '',
                            data.ttft_ms,
                            data.tok_per_sec,
                            data.tokens,
                            data.duration_ms,
                            data.reasoning
                        );
                    }
                } catch (e) {
                    console.warn('Parse error in stream chunk', e);
                }
            }
        }
    } catch (e) {
        if (e.name !== 'AbortError') {
            console.error('Chat stream failed:', e);
            streamError = true;
            finishStreamingMessage('');
            if (!_streamBubble) {
                appendMessage('assistant', '⚠️ Connection error: ' + e.message, {});
            }
        }
    } finally {
        isChatStreaming = false;
        document.getElementById('chat-send-btn').style.display = 'flex';
        document.getElementById('chat-stop-btn').style.display = 'none';
        abortController = null;
        removeTypingIndicator();
        reindexMessages();
        scrollChat();
    }
}

function stopGeneration() {
    if (abortController) {
        abortController.abort();
        abortController = null;
    }
    finishStreamingMessage(''); // finalize with accumulated stream content
    isChatStreaming = false;
    document.getElementById('chat-send-btn').style.display = 'flex';
    document.getElementById('chat-stop-btn').style.display = 'none';
    removeTypingIndicator();
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

// ── Init ──

document.addEventListener('DOMContentLoaded', () => {
    mermaid.initialize({ startOnLoad: false, theme: 'dark', themeVariables: { primaryColor: '#00ffcc', primaryTextColor: '#f8fafc', primaryBorderColor: '#00ffcc', lineColor: '#00b8ff', secondaryColor: '#7b2cbf', tertiaryColor: '#05070a' } });

    // Chat scroll
    const chatMessages = document.getElementById('chat-messages');
    if (chatMessages) {
        chatMessages.addEventListener('scroll', onChatScroll);
    }

    document.getElementById('scroll-down-btn')?.addEventListener('click', scrollToBottom);

    // Paste images
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

    // Drag-drop
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

    // Focus input on /
    document.addEventListener('keydown', (e) => {
        if (e.key === '/' && !['INPUT', 'TEXTAREA'].includes(e.target.tagName)) {
            if (document.getElementById('view-chat').classList.contains('active')) {
                e.preventDefault();
                document.getElementById('chat-input').focus();
            }
        }
    });
});
