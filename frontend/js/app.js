/* ===== 考研学习平台 - 前端主逻辑 ===== */

const state = {
    subject: 'math',
    mode: 'A',
    currentVideoId: null,
    currentVideoTitle: '',
    conversationId: '',
    messages: [],
    subtitles: [],
    typingId: null,
    isStreaming: false,
    currentImage: null,
    currentImageFile: null,
    autoCapture: false,  // 自动截图当前画面
    uploadController: null,
    currentFolderId: 0,  // 当前选中的文件夹
    convModel: '',       // 当前会话的模型（空 = 全局主模型）
    globalModel: '',     // 全局主模型名（下拉兜底显示）
};

const API_BASE = localStorage.getItem('kaoyan_api_url') || '';
function getApiUrl(p) { return API_BASE + p; }

// ===== API 封装：自动附带访问令牌（⚙️设置 里配置，服务端 .env 需配 PLATFORM_TOKEN）=====
function getApiToken() { try { return localStorage.getItem('kaoyan_token') || ''; } catch (e) { return ''; } }
async function apiFetch(path, opts = {}) {
    const token = getApiToken();
    if (token) {
        const h = new Headers(opts.headers || {});
        h.set('Authorization', 'Bearer ' + token);
        opts.headers = h;
    }
    const resp = await fetch(getApiUrl(path), opts);
    if (resp.status === 401) addSystemMessage('⚠️ 无访问权限：请在 ⚙️ 设置 填写访问令牌（需在服务器 .env 配好 PLATFORM_TOKEN）');
    return resp;
}

// ===== 初始化 =====
// 复位视口滚动：防止历史 scrollIntoView 冒泡把文档滚动位置留在非顶部，导致顶部导航被顶出屏幕、界面看起来"被拉起"
function resetViewportScroll() {
    try {
        window.scrollTo(0, 0);
        document.documentElement.scrollTop = 0;
        document.body.scrollTop = 0;
    } catch (e) {}
}
document.addEventListener('DOMContentLoaded', () => { resetViewportScroll(); switchMode('A'); setupSplitters(); initConvResizer(); restoreConvSidebarState(); loadChatModelList(); loadTree().then(() => restoreActiveConversation()); });

// 终极兜底：任何时刻 document 被滚动（滚动锚定/浏览器内部行为），立即拉回顶部。
// 用 capture 阶段监听，抢在浏览器完成滚动渲染前复位，防止顶部导航被顶出屏幕、视频滚出视口后黑屏。
window.addEventListener('scroll', () => {
    try {
        if (window.scrollY !== 0 || document.documentElement.scrollTop !== 0 || document.body.scrollTop !== 0) {
            window.scrollTo(0, 0);
            document.documentElement.scrollTop = 0;
            document.body.scrollTop = 0;
        }
    } catch (e) {}
}, true);

// ===== 可拖拽分隔条（分隔条在哪，宽度变化就发生在哪） =====
function setupSplitters() {
    const sidebar = document.querySelector('.video-sidebar');
    const chat = document.querySelector('.chat-panel');
    const sideSplit = document.getElementById('splitterSidebar');
    const chatSplit = document.getElementById('splitterChat');
    if (!sidebar || !chat || !sideSplit || !chatSplit) return;

    let isDragging = false;
    let currentSplitter = null;
    let startX = 0;
    let startWidth = 0;
    let target = null;  // 本次拖动要改宽度的面板：sidebar 或 chat

    function onMouseDown(e, splitter, panel) {
        isDragging = true;
        currentSplitter = splitter;
        target = panel;
        startX = e.clientX;
        startWidth = panel.getBoundingClientRect().width;
        splitter.classList.add('dragging');
        document.body.style.cursor = 'col-resize';
        document.body.style.userSelect = 'none';
    }

    sideSplit.addEventListener('mousedown', e => onMouseDown(e, sideSplit, sidebar));
    chatSplit.addEventListener('mousedown', e => onMouseDown(e, chatSplit, chat));

    document.addEventListener('mousemove', e => {
        if (!isDragging || !currentSplitter) return;
        const dx = e.clientX - startX;
        const cw = document.querySelector('.main-content').clientWidth;
        const VIDEO_MIN = 160;  // 视频区最小宽度：两侧面板可以一直扩大把视频区挤到这么小
        if (target === sidebar) {
            // 往右拖 → 侧边栏变宽（其右缘就是分隔条，原地变化）；上限保证视频区不被挤到 < VIDEO_MIN
            const maxW = cw - chat.getBoundingClientRect().width - VIDEO_MIN;
            const newWidth = Math.max(80, Math.min(maxW, startWidth + dx));
            sidebar.style.width = newWidth + 'px';
        } else {
            // 往左拖 → 聊天区变宽（其左缘就是分隔条，原地变化）；上限保证视频区不被挤到 < VIDEO_MIN
            const maxW = cw - sidebar.getBoundingClientRect().width - VIDEO_MIN;
            const newWidth = Math.max(120, Math.min(maxW, startWidth - dx));
            chat.style.width = newWidth + 'px';
        }
    });

    document.addEventListener('mouseup', () => {
        if (isDragging && currentSplitter) {
            currentSplitter.classList.remove('dragging');
        }
        isDragging = false;
        currentSplitter = null;
        document.body.style.cursor = '';
        document.body.style.userSelect = '';
    });
}

// ===== 科目切换 =====
function switchSubject(subject) {
    state.subject = subject;
    state.conversationId = '';
    state.convModel = '';
    state.messages = [];
    state.subtitles = [];
    state.currentFolderId = 0;
    document.querySelectorAll('.subject-tab').forEach(el => el.classList.toggle('active', el.dataset.subject === subject));
    document.getElementById('chatSubjectLabel').textContent = document.querySelector(`.subject-tab[data-subject="${subject}"]`).textContent;
    resetPlayer(); clearChat(); loadTree();
    addSystemMessage(`📚 ${document.querySelector(`.subject-tab[data-subject="${subject}"]`).textContent}`);
    restoreActiveConversation();
}

// ===== Agent 切换 =====
function switchMode(mode) {
    state.mode = mode;
    state.conversationId = '';
    state.convModel = '';
    state.messages = [];
    document.querySelectorAll('.mode-tab').forEach(el => el.classList.toggle('active', el.dataset.mode === mode));
    const names = { A: '💬 即时问答', B: '🎯 引导输出', C: '📝 课后问答' };
    const msgs = { A: '👋 看视频随时提问！', B: '🎯 我来出题引导你！', C: '📝 自由提问吧！' };
    document.getElementById('chatModeLabel').textContent = names[mode] || mode;
    // 模式B：显示侧边栏视频勾选框
    document.querySelectorAll('.b-video-cb').forEach(cb => {
        cb.style.display = (mode === 'B') ? 'inline-block' : 'none';
    });
    clearChat(); addSystemMessage(msgs[mode] || '');
    document.getElementById('subtitlePanel').style.display = (mode === 'A' && state.currentVideoId) ? 'flex' : 'none';
    restoreActiveConversation();
}

// ===== 文件夹树 =====
let treeExpanded = {};  // {folderId: true/false}

// JS 加载成功就立即清除 HTML 中的占位文本
try { const el = document.getElementById('pageLoadCheck'); if (el) el.textContent = '✅ JS 已加载'; } catch(e) {}

// 树数据缓存：供搜索过滤时快速重渲染（不用重新请求后端）
let treeCache = null;

async function loadTree() {
    const container = document.getElementById('videoTreeBody');
    if (!container) { console.error('videoTreeBody not found'); return; }

    container.innerHTML = '<div style="padding:20px;color:#333">🔍 正在请求数据...</div>';

    try {
        const resp = await apiFetch(`/api/folders/tree?subject=${state.subject}`);
        if (!resp.ok) throw new Error('网络错误');
        const data = await resp.json();
        treeCache = data;
        renderTree(data, getTreeFilter());
    } catch (err) {
        container.innerHTML = `<div class="empty-state"><div class="icon">⚠️</div><div>${err.message}</div></div>`;
    }
}

function getTreeFilter() {
    const el = document.getElementById('videoSearch');
    return (el && el.value ? el.value.trim().toLowerCase() : '');
}

function treeVideoMatches(v, q) {
    if (!q) return true;
    const hay = ((v.title || '') + ' ' + (v.filename || '')).toLowerCase();
    return hay.includes(q);
}

function renderTree(data, q) {
    const container = document.getElementById('videoTreeBody');
    if (!container) return;
    if (!data.folders.length && !data.uncategorized.length) {
        container.innerHTML = `<div class="empty-state"><div class="icon">📂</div><div>暂无视频</div></div>`;
        return;
    }
    container.innerHTML = '';
    let shown = 0;
    // 渲染文件夹（搜索时无匹配的整棵隐藏）
    for (const f of data.folders) {
        const node = renderFolderNode(f, 0, q);
        if (node) { container.appendChild(node); shown++; }
    }
    // 未分类视频（搜索时同样过滤）
    const uc = (data.uncategorized || []).filter(v => treeVideoMatches(v, q));
    if (uc.length) {
        const ucDiv = document.createElement('div');
        ucDiv.className = 'tree-folder';
        ucDiv.innerHTML = `<div class="tree-folder-label" onclick="toggleFolder(this)">
            <span class="tree-arrow">▶</span><span class="tree-icon">📁</span><span>未分类</span><span class="tree-count">${uc.length}</span>
        </div><div class="tree-children" style="display:none">`;
        for (const v of uc) ucDiv.querySelector('.tree-children').appendChild(makeVideoNode(v));
        container.appendChild(ucDiv);
        shown++;
    }
    if (!shown) {
        container.innerHTML = `<div class="empty-state"><div class="icon">🔍</div><div>没有匹配「${q}」的视频</div></div>`;
        return;
    }
    // 若存在生成中的课程总结，20 秒后自动刷新一次树状态（后台任务完成时能跟上）
    if (container.querySelector('.status-summary-processing')) {
        clearTimeout(window._summaryPollTimer);
        window._summaryPollTimer = setTimeout(() => loadTree(), 20000);
    }
}

function onVideoSearch(v) {
    if (!treeCache) return;
    renderTree(treeCache, (v || '').trim().toLowerCase());
}

function renderFolderNode(f, depth, q) {
    const isExpanded = treeExpanded[f.id] === true;
    const expand = isExpanded || !!q;   // 搜索时强制展开
    // 先收集匹配内容：无匹配的文件夹整体隐藏（搜索态）
    const childNodes = [];
    let match = 0;
    for (const c of (f.children || [])) {
        const node = renderFolderNode(c, depth + 1, q);
        if (node) { childNodes.push(node); match++; }
    }
    const videos = (f.videos || []).filter(v => treeVideoMatches(v, q));
    match += videos.length;
    if (q && !match) return null;

    const div = document.createElement('div');
    div.className = 'tree-folder';
    const hasAny = (f.children && f.children.length) || (f.videos && f.videos.length);
    const arrow = hasAny ? (expand ? '▼' : '▶') : '';

    div.innerHTML = `<div class="tree-folder-label" style="padding-left:${depth*16+4}px" onclick="toggleFolder(this)" data-fid="${f.id}">
        <span class="tree-arrow">${arrow}</span>
        <span class="tree-icon">📂</span>
        <span class="tree-fname">${escHtml(f.name)}</span>
        <span class="tree-count">${hasAny ? videos.length : ''}</span>
        <span class="tree-ctx" onclick="event.stopPropagation();renameFolder(${f.id})" title="重命名">✏️</span>
        <span class="tree-ctx" onclick="event.stopPropagation();deleteFolder(${f.id})" title="删除">🗑️</span>
    </div>`;
    const childrenDiv = document.createElement('div');
    childrenDiv.className = 'tree-children';
    childrenDiv.style.display = expand ? 'block' : 'none';
    // 子文件夹
    for (const c of childNodes) childrenDiv.appendChild(c);
    // 视频
    for (const v of videos) childrenDiv.appendChild(makeVideoNode(v, depth + 1));
    div.appendChild(childrenDiv);
    return div;
}

function makeVideoNode(v, depth = 0) {
    const item = document.createElement('div');
    item.className = `tree-video${v.id === state.currentVideoId ? ' active' : ''}`;
    item.dataset.videoId = v.id;
    const labels = { pending: '待处理', processing: '提取中', done: '已识别', failed: '失败' };
    // 课程总结状态：📄 点击查看 / 生成中 / 失败可重试 / 无总结（字幕就绪）可手动生成
    const sLabels = { done: '📄', processing: '⏳', failed: '📄重试', none: '📄生成', pending: '📄生成' };
    const sTitle = {
        done: '查看课程总结', processing: '总结生成中', failed: '重新生成总结',
        none: '生成课程总结（字幕已就绪）', pending: '生成课程总结（字幕已就绪）',
    };
    const sAction = v.summary_status === 'done' ? `viewSummary(${v.id})`
        : v.summary_status === 'failed' ? `generateSummary(${v.id})`
        : (v.summary_status === 'none' || v.summary_status === 'pending')
            && v.subtitle_status === 'done' ? `generateSummary(${v.id})` : '';
    const checked = (state.mode === 'B' && sessionStorage.getItem('b_video_'+v.id) === '1') ? 'checked' : '';
    item.innerHTML = `<div class="tree-video-label" style="padding-left:${depth*16+4}px">
        <input type="checkbox" class="b-video-cb" data-video="${v.id}" ${checked} style="display:none" onchange="onBCheckChange(${v.id}, this)">
        <span class="tree-icon" onclick="playVideo(${v.id})">🎬</span>
        <span class="tree-vname" onclick="playVideo(${v.id})">${escHtml(v.title || v.filename)}</span>
        <span class="tree-vmeta">${formatSize(v.file_size)}</span>
        <span class="tree-vstatus status-${v.subtitle_status}">${labels[v.subtitle_status] || ''}</span>
        <span class="tree-sum status-summary-${v.summary_status}" ${sAction ? `onclick="event.stopPropagation();${sAction}"` : ''}
              title="${sTitle[v.summary_status] || ''}">${sLabels[v.summary_status] || ''}</span>
        <span class="tree-ctx" onclick="event.stopPropagation();moveVideoDialog(${v.id})" title="移动到...">📂</span>
        <span class="tree-ctx" onclick="event.stopPropagation();extractSubtitle(${v.id})" title="重新提取字幕">🔄</span>
        <span class="tree-ctx" onclick="event.stopPropagation();deleteVideo(${v.id})" title="删除">🗑️</span>
    </div>`;
    return item;
}

function toggleFolder(el) {
    const children = el.parentElement.querySelector('.tree-children');
    if (!children) return;
    const showing = children.style.display !== 'none';
    children.style.display = showing ? 'none' : 'block';
    const arrow = el.querySelector('.tree-arrow');
    if (arrow) arrow.textContent = showing ? '▶' : '▼';
    const fid = el.dataset.fid;
    if (fid) treeExpanded[fid] = !showing;
}

// ===== 文件夹 CRUD =====
function showNewFolderDialog() {
    openDialog({
        title: '📂 新建文件夹',
        bodyHTML: '<label>文件夹名称</label><input type="text" id="dlgFolderName" maxlength="255" placeholder="如：第6讲 中值定理">',
        okText: '创建',
        onOk: () => {
            const name = document.getElementById('dlgFolderName').value.trim();
            if (!name) { addSystemMessage('⚠️ 名称不能为空'); return; }
            apiFetch('/api/folders/create', {
                method: 'POST', headers: {'Content-Type':'application/json'},
                body: JSON.stringify({subject: state.subject, name: name}),
            }).then(r => {
                closeDialog();
                if (r.ok) loadTree();
                else r.json().then(d => addSystemMessage(`⚠️ ${d.detail || '创建失败'}`));
            });
        },
    });
}

function renameFolder(id) {
    openDialog({
        title: '✏️ 重命名文件夹',
        bodyHTML: '<label>新名称</label><input type="text" id="dlgFolderName" maxlength="255" placeholder="输入新名称">',
        okText: '保存',
        onOk: () => {
            const name = document.getElementById('dlgFolderName').value.trim();
            if (!name) { addSystemMessage('⚠️ 名称不能为空'); return; }
            apiFetch(`/api/folders/${id}/rename`, {
                method: 'PUT', headers: {'Content-Type':'application/json'},
                body: JSON.stringify({name: name}),
            }).then(r => {
                closeDialog();
                if (r.ok) loadTree();
                else r.json().then(d => addSystemMessage(`⚠️ ${d.detail || '重命名失败'}`));
            });
        },
    });
}

function deleteFolder(id) {
    openDialog({
        title: '🗑️ 删除文件夹',
        bodyHTML: '<div class="dialog-warn">删除文件夹后，其中的视频会移回「未分类」，<b>视频文件不会被删除</b>。确定继续吗？</div>',
        okText: '删除',
        onOk: () => {
            apiFetch(`/api/folders/${id}`, {method:'DELETE'})
                .then(r => { closeDialog(); if (r.ok) loadTree(); });
        },
    });
}

async function moveVideoDialog(videoId) {
    // 获取文件夹列表供选择（页内下拉，不再手输 ID）
    try {
        const resp = await apiFetch(`/api/folders/tree?subject=${state.subject}`);
        if (!resp.ok) throw new Error('加载文件夹失败');
        const data = await resp.json();
        let html = '<label>移动到：</label><select id="dlgMoveTarget">';
        html += '<option value="0">（未分类）</option>';
        (function walk(fs, d) {
            for (const f of fs) {
                html += `<option value="${f.id}">${'　'.repeat(d)}${f.name}</option>`;
                walk(f.children || [], d + 1);
            }
        })(data.folders || [], 1);
        html += '</select>';
        openDialog({
            title: '📂 移动视频',
            bodyHTML: html,
            okText: '移动',
            onOk: () => {
                const fid = parseInt(document.getElementById('dlgMoveTarget').value || '0', 10);
                apiFetch(`/api/folders/${videoId}/move?folder_id=${fid}`, {method:'PUT'})
                    .then(r => { closeDialog(); if (r.ok) loadTree(); });
            },
        });
    } catch (e) {
        addSystemMessage(`⚠️ ${e.message}`);
    }
}

// ===== 播放视频 =====
async function playVideo(videoId) {
    state.currentVideoId = videoId;
    document.querySelectorAll('.tree-video').forEach(el => el.classList.toggle('active', parseInt(el.dataset.videoId) === videoId));

    const video = document.getElementById('videoPlayer');
    const placeholder = document.getElementById('videoPlaceholder');

    // <video> 标签无法携带 Authorization 请求头，token 走查询参数（服务端两种都认）
    const tk = getApiToken();
    video.src = getApiUrl(`/api/videos/stream/${videoId}`) + (tk ? `?token=${encodeURIComponent(tk)}` : '');

    // 读取上次播放进度（续播：进度是每 5 秒落一次 localStorage）
    let savedPos = 0;
    try { savedPos = parseFloat(localStorage.getItem('kaoyan_progress_' + videoId) || '0') || 0; } catch (e) {}

    // 等待视频元数据加载成功后再切换显示状态（防鉴权失败/文件不存在导致黑屏）
    video.onloadedmetadata = () => {
        placeholder.style.display = 'none';
        video.style.display = 'block';
        if (savedPos > 10 && isFinite(video.duration) && savedPos < video.duration - 10) {
            video.currentTime = savedPos;
            addSystemMessage(`⏩ 已从上次位置 ${fmtTime(savedPos)} 继续播放`);
        } else if (savedPos > 2) {
            video.currentTime = savedPos;
        }
        video.play();
        video.onerror = null; // 成功加载后移除错误处理器
    };

    // 加载失败时保持占位符可见并提示
    video.onerror = () => {
        video.style.display = 'none';
        placeholder.style.display = 'flex';
        addSystemMessage(`⚠️ 视频加载失败，请检查网络或访问令牌`);
    };

    await loadSubtitles(videoId);

    if (state.mode === 'A') document.getElementById('subtitlePanel').style.display = 'flex';
    addSystemMessage(`🎬 播放中`);
}

// 播放完清掉进度记忆，下次重新从开头播
document.getElementById('videoPlayer').addEventListener('ended', () => {
    try {
        if (state.currentVideoId) localStorage.removeItem('kaoyan_progress_' + state.currentVideoId);
    } catch (e) {}
});

function resetPlayer() {
    const video = document.getElementById('videoPlayer');
    const placeholder = document.getElementById('videoPlaceholder');
    video.pause(); video.src = ''; video.style.display = 'none';
    placeholder.style.display = 'flex';
    state.currentVideoId = null; state.subtitles = [];
    document.getElementById('subtitlePanel').style.display = 'none';
}

// ===== 字幕（可点击跳转） =====
async function loadSubtitles(videoId) {
    try {
        const resp = await apiFetch(`/api/videos/${videoId}/subtitles`);
        if (!resp.ok) return;
        const data = await resp.json();
        state.subtitles = data.subtitles || [];
        renderSubtitles();
    } catch (e) { console.error(e); }
}

function renderSubtitles() {
    const list = document.getElementById('subtitleList');
    const count = document.getElementById('subtitleCount');
    if (!state.subtitles.length) { list.innerHTML = '<div class="empty-state">无字幕</div>'; return; }
    count.textContent = `${state.subtitles.length} 段`;
    list.innerHTML = '';
    state.subtitles.forEach((s, i) => {
        const div = document.createElement('div');
        div.className = 'subtitle-item';
        div.dataset.seq = i;
        div.innerHTML = `<span class="sub-time" onclick="seekTo(${s.start})">${fmtTime(s.start)}</span>
            <span class="sub-text">${escHtml(s.text)}</span>`;
        div.onclick = () => seekTo(s.start);
        list.appendChild(div);
    });
}

// ===== 容器全屏（视频+字幕一起全屏）=====
// ===== 自动截图开关 =====
function toggleAutoCapture() {
    state.autoCapture = !state.autoCapture;
    const btn = document.getElementById('captureToggleBtn');
    if (state.autoCapture) {
        btn.style.background = '#1a73e8';
        btn.style.borderColor = '#1a73e8';
        addSystemMessage('🎥 已开启：每次提问自动截取当前画面');
    } else {
        btn.style.background = '';
        btn.style.borderColor = '';
        addSystemMessage('🎥 已关闭自动截图');
    }
}

async function togglePip() {
    const wrapper = document.getElementById('videoWrapper');
    if (!wrapper) return;
    try {
        if (document.fullscreenElement || document.webkitFullscreenElement) {
            if (document.exitFullscreen) document.exitFullscreen();
            else if (document.webkitExitFullscreen) document.webkitExitFullscreen();
        } else {
            if (wrapper.requestFullscreen) wrapper.requestFullscreen();
            else if (wrapper.webkitRequestFullscreen) wrapper.webkitRequestFullscreen();
        }
    } catch (e) {
        addSystemMessage(`⚠️ 全屏不支持: ${e.message}`);
    }
}

// ===== 字幕覆盖层开关 =====
let subtitleOverlayVisible = true;
function toggleSubtitleOverlay() {
    const overlay = document.getElementById('videoSubtitleOverlay');
    if (!overlay) return;
    subtitleOverlayVisible = !subtitleOverlayVisible;
    overlay.style.display = subtitleOverlayVisible ? 'block' : 'none';
}

// ===== 倍速控制 =====
let currentSpeed = 1;
function setSpeed(speed) {
    const video = document.getElementById('videoPlayer');
    if (video) {
        video.playbackRate = speed;
        currentSpeed = speed;
        // 高亮当前速度按钮
        document.querySelectorAll('.speed-btn').forEach(b => b.classList.toggle('active', parseFloat(b.textContent) === speed));
    }
}

function seekTo(seconds) {
    const video = document.getElementById('videoPlayer');
    if (!video) return;
    if (video.readyState >= 1) {
        video.currentTime = seconds;
        video.play();
    } else {
        // 等视频加载好再跳
        video.addEventListener('loadedmetadata', function onReady() {
            video.currentTime = seconds;
            video.play();
            video.removeEventListener('loadedmetadata', onReady);
        });
        video.load();
    }
}

function fmtTime(s) {
    const m = Math.floor(s / 60);
    const sec = Math.floor(s % 60);
    return `${m}:${String(sec).padStart(2,'0')}`;
}

// 高亮当前字幕 + 视频悬浮字幕
let subtitleHighlightTimer = null;

// 全屏状态检测
function isFullscreen() {
    return !!(document.fullscreenElement || document.webkitFullscreenElement);
}

document.addEventListener('DOMContentLoaded', () => {
    // 全屏变化时更新字幕显示
    document.addEventListener('fullscreenchange', updateSubtitleOverlayVisibility);
    document.addEventListener('webkitfullscreenchange', updateSubtitleOverlayVisibility);

    document.getElementById('videoPlayer').addEventListener('timeupdate', () => {
        if (state.mode !== 'A' || !state.subtitles.length) return;
        if (subtitleHighlightTimer) return;
        subtitleHighlightTimer = setTimeout(() => {
            subtitleHighlightTimer = null;
            const t = document.getElementById('videoPlayer').currentTime;
            let activeIdx = -1;
            state.subtitles.forEach((s, i) => { if (t >= s.start && t <= s.end) activeIdx = i; });
            
            // 更新字幕列表高亮
            document.querySelectorAll('.subtitle-item').forEach(el => el.classList.remove('active'));
            if (activeIdx >= 0) {
                const active = document.querySelector(`.subtitle-item[data-seq="${activeIdx}"]`);
                if (active) {
                    active.classList.add('active');
                    // 只滚动字幕列表容器本身：scrollIntoView 会冒泡滚动 document 视口（CSS overflow:hidden 挡不住编程滚动），
                    // 把顶部导航顶出屏幕，出现"界面被拉起只剩一半"。改用容器内 scrollTop 计算
                    const list = document.getElementById('subtitleList');
                    if (list) {
                        const t = active.offsetTop - list.clientHeight / 2 + active.clientHeight / 2;
                        list.scrollTop = t > 0 ? t : 0;
                    }
                    resetViewportScroll();
                }
            }
            
            // 更新视频悬浮字幕
            const overlay = document.getElementById('videoSubtitleOverlay');
            const subtitlePanel = document.getElementById('subtitlePanel');
            if (overlay && state.subtitles[activeIdx] && subtitleOverlayVisible) {
                overlay.textContent = state.subtitles[activeIdx].text;
                overlay.style.display = 'block';
            } else if (overlay) {
                overlay.style.display = 'none';
            }
        }, 300);
    });
});

function updateSubtitleOverlayVisibility() {
    const overlay = document.getElementById('videoSubtitleOverlay');
    if (!overlay) return;
    if (isFullscreen() && subtitleOverlayVisible) {
        overlay.classList.add('fullscreen');
        overlay.style.display = 'block';
    } else {
        overlay.classList.remove('fullscreen');
    }
}

let subtitlePanelVisible = true;
function toggleSubtitlePanel() {
    const list = document.getElementById('subtitleList');
    const btn = document.getElementById('subtitleToggleBtn');
    subtitlePanelVisible = !subtitlePanelVisible;
    list.style.display = subtitlePanelVisible ? 'block' : 'none';
    btn.textContent = subtitlePanelVisible ? '收起' : '展开';
}

// ===== 视频上传 =====
async function uploadVideo(input) {
    const file = input.files[0];
    if (!file) return;
    if (!file.type.startsWith('video/')) { alert('请选择视频'); return; }
    if (file.size > 4e9) { alert('最大 4GB'); return; }
    const fd = new FormData();
    fd.append('file', file); fd.append('subject', state.subject);
    fd.append('title', file.name.replace(/\.[^.]+$/, ''));
    try {
        const resp = await apiFetch('/api/videos/upload', { method: 'POST', body: fd });
        if (!resp.ok) throw new Error((await resp.json().catch(()=>({}))).detail||'失败');
        input.value = ''; loadTree();
        const data = await resp.json();
        if (data.subtitle_status === 'pending') setTimeout(() => extractSubtitle(data.id), 1000);
    } catch (err) { addSystemMessage(`⚠️ ${err.message}`); input.value = ''; }
}

async function uploadFolder(input) {
    const files = input.files;
    if (!files || !files.length) { addSystemMessage('⚠️ 未选择任何文件'); input.value=''; return; }

    const exts = ['.mp4','.avi','.mkv','.mov','.wmv','.flv','.webm'];
    const videos = [];
    for (const f of files) {
        const ext = '.' + f.name.split('.').pop().toLowerCase();
        if (exts.includes(ext)) videos.push(f);
    }
    if (!videos.length) { addSystemMessage('⚠️ 文件夹中没有视频文件'); input.value=''; return; }

    const batch = videos.slice(0, 50);
    addSystemMessage(`📁 找到 ${videos.length} 个视频，上传 ${batch.length} 个...`);

    // 逐个上传 + 提取字幕
    let success = 0, failed = 0;
    const uploadedIds = [];
    for (const file of batch) {
        try {
            const fd = new FormData();
            fd.append('file', file);
            fd.append('subject', state.subject);
            fd.append('title', file.name.replace(/\.[^.]+$/, ''));
            const resp = await apiFetch('/api/videos/upload', { method: 'POST', body: fd });
            if (resp.ok) {
                const data = await resp.json();
                uploadedIds.push(data.id);
                success++;
            } else {
                failed++;
            }
        } catch (e) {
            failed++;
        }
    }

    addSystemMessage(`✅ 上传完成：成功 ${success} 个${failed ? `，失败 ${failed} 个` : ''}`);
    input.value = '';
    loadTree();

    // 逐个提取字幕
    if (uploadedIds.length > 0) {
        addSystemMessage('🔄 开始提取字幕...');
        for (let i = 0; i < uploadedIds.length; i++) {
            await new Promise(r => setTimeout(r, i * 500));  // 间隔 0.5 秒
            try {
                const resp = await apiFetch(`/api/videos/${uploadedIds[i]}/extract-subtitle`, { method: 'POST' });
                if (resp.ok) {
                    const d = await resp.json();
                    addSystemMessage(`✅ 第 ${i+1}/${uploadedIds.length} 个字幕提取完成`);
                }
            } catch (e) {
                addSystemMessage(`⚠️ 第 ${i+1} 个字幕提取失败`);
            }
        }
        addSystemMessage('🎉 全部字幕提取完成！');
        loadTree();
    }
}

// ===== 字幕提取串行队列：一次只识别一个视频（避免并发触发云 ASR 限流），并显示进度 1/N =====
const subtitleQueue = [];
let subtitleBusy = false;
let subtitleDoneCount = 0;

async function processSubtitleQueue() {
    if (subtitleBusy) return;
    subtitleBusy = true;
    while (subtitleQueue.length > 0) {
        const videoId = subtitleQueue.shift();
        const idx = ++subtitleDoneCount;
        const total = subtitleDoneCount + subtitleQueue.length;
        try {
            addSystemMessage(`🔄 提取字幕中（${idx}/${total}）...`);
            const resp = await apiFetch(`/api/videos/${videoId}/extract-subtitle`, {method:'POST'});
            if (!resp.ok) {
                let detail = '提取失败';
                try { const d = await resp.json(); detail = d.detail || d.message || detail; } catch (e) {}
                throw new Error(detail);
            }
            const data = await resp.json();
            addSystemMessage(`✅ 字幕提取完成（${idx}/${total}）`); loadTree();
            // 只在当前播放的视频才刷新字幕
            if (videoId === state.currentVideoId) {
                await loadSubtitles(videoId);
            }
        } catch (err) {
            addSystemMessage(`⚠️ 字幕提取失败（${idx}/${total}）: ${err.message}`); loadTree();
        }
    }
    subtitleBusy = false;
    subtitleDoneCount = 0;
}

function extractSubtitle(videoId) {
    subtitleQueue.push(videoId);
    processSubtitleQueue();
}

async function deleteVideo(videoId) {
    openDialog({
        title: '🗑️ 删除视频',
        bodyHTML: '<div class="dialog-warn">将删除该视频文件及其字幕/总结记录，<b>不可恢复</b>。确定继续吗？</div>',
        okText: '删除',
        onOk: () => {
            apiFetch(`/api/videos/${videoId}`, {method:'DELETE'}).then(r => {
                closeDialog();
                if (state.currentVideoId === videoId) resetPlayer();
                loadTree();
            });
        },
    });
}

// ===== 重新扫描本地目录（找回手动放入 storage/videos 的视频）=====
async function rescanVideos() {
    addSystemMessage('🔎 正在扫描本地视频目录...');
    try {
        const resp = await apiFetch('/api/videos/rescan', { method: 'POST' });
        const d = await resp.json().catch(() => ({}));
        addSystemMessage(d.message || (resp.ok ? '✅ 扫描完成' : `⚠️ 扫描失败（${resp.status}）`));
        loadTree();
    } catch (err) { addSystemMessage(`⚠️ 扫描失败：${err.message}`); }
}

// ===== 一键补生成课程总结（存量的字幕已完成但无总结的视频）=====
async function batchSummaries() {
    try {
        const resp = await apiFetch('/api/videos/batch-summary', { method: 'POST' });
        const d = await resp.json().catch(() => ({}));
        if (!resp.ok) { addSystemMessage(`⚠️ ${d.detail || '批量生成失败'}`); return; }
        addSystemMessage(d.message || `✅ 已开始批量生成（${d.queued} 个视频）`);
        loadTree();
        // 后台正在生成时轻量轮询树状态（有 ⏳ 徽标即每 15 秒刷新，结束自动停）
        const poll = () => {
            if (!document.querySelector('.status-summary-processing')) return;
            loadTree();
            setTimeout(poll, 15000);
        };
        setTimeout(poll, 10000);
    } catch (err) { addSystemMessage(`⚠️ ${err.message}`); }
}

// ===== 课程总结（查看 / 手动生成重试）=====
async function viewSummary(videoId) {
    try {
        const resp = await apiFetch(`/api/videos/${videoId}/summary`);
        const d = await resp.json().catch(() => ({}));
        if (!resp.ok || d.status !== 'done' || !d.content) {
            addSystemMessage('ℹ️ 总结尚未生成（字幕完成后自动生成）');
            return;
        }
        document.getElementById('summaryBody').textContent = d.content;
        const dl = document.getElementById('summaryDownload');
        dl.onclick = () => {
            const blob = new Blob([d.content], { type: 'text/markdown;charset=utf-8' });
            const a = document.createElement('a');
            a.href = URL.createObjectURL(blob);
            const nameEl = document.querySelector(`.tree-video[data-video-id="${videoId}"] .tree-vname`);
            a.download = `${(nameEl ? nameEl.textContent.trim() : `video_${videoId}`)}-课程总结.md`;
            a.click();
            setTimeout(() => URL.revokeObjectURL(a.href), 1000);
        };
        document.getElementById('summaryModal').style.display = 'flex';
    } catch (err) { addSystemMessage(`⚠️ ${err.message}`); }
}

function closeSummary() {
    const m = document.getElementById('summaryModal');
    if (m) m.style.display = 'none';
}

async function generateSummary(videoId) {
    addSystemMessage('📄 正在生成课程总结（分段提取，约需 10~60 秒）...');
    try {
        const resp = await apiFetch(`/api/videos/${videoId}/generate-summary`, { method: 'POST' });
        const d = await resp.json().catch(() => ({}));
        if (!resp.ok) {
            addSystemMessage(`⚠️ 总结生成失败：${d.detail || resp.status}`);
            loadTree();
            return;
        }
        addSystemMessage('✅ 课程总结生成完成，点 📄 查看');
        loadTree();
    } catch (err) { addSystemMessage(`⚠️ ${err.message}`); loadTree(); }
}

// ===== 聊天 =====
async function sendMessage() {
    const input = document.getElementById('chatInput');
    const text = input.value.trim();
    if ((!text && !state.currentImage) || state.isStreaming) return;
    const displayText = text || '[查看图片]';
    input.value = '';
    addUserMessage(displayText, state.currentImage, quoteState ? quoteState.text : '');
    document.getElementById('btnSend').disabled = true;
    state.isStreaming = true;
    let typingId = showTyping();  // 思考期间显示"打字中"
    let streamingEl = null;       // 流式正文字泡（SSE 到达后接管）
    let streamingContent = null;
    let lastRender = 0;
    // ---- 思考静默心跳：提升到 try 外，异常路径也能安全清理 ----
    let thinkingTimer = null;     // 500ms 心跳：更新"已用时"计时 + 判定静默
    const stopThinkingTimer = () => {
        if (thinkingTimer) { clearInterval(thinkingTimer); thinkingTimer = null; }
    };
    try {
        // A模式 + 视频播放中 + 自动截图开 → 捕获当前帧
        let captureBlob = null;
        if (state.autoCapture && state.mode === 'A' && state.currentVideoId) {
            const video = document.getElementById('videoPlayer');
            if (video && video.videoWidth > 0) {
                const c = document.createElement('canvas');
                c.width = video.videoWidth;
                c.height = video.videoHeight;
                c.getContext('2d').drawImage(video, 0, 0);
                captureBlob = await new Promise(resolve => c.toBlob(resolve, 'image/jpeg', 0.7));
            }
        }

        // 统一走 SSE 流式端点（文本/图片/自动截图/模式B 勾选都走 FormData）
        const fd = new FormData();
        fd.append('query', text || '分析图片');
        fd.append('subject', state.subject);
        fd.append('mode', state.mode);
        fd.append('subtitle_context', state.mode === 'A' ? await getModeASubtitleContext() : '');
        fd.append('conversation_id', state.conversationId);
        if (quoteState && quoteState.text) fd.append('quote', JSON.stringify({ text: quoteState.text }));
        if (state.mode === 'B') fd.append('watched_video_ids', JSON.stringify(getSelectedBVideos()));
        if (state.currentImage) {
            fd.append('image', state.currentImageFile);
            removeImage();
        } else if (captureBlob) {
            fd.append('image', captureBlob, 'frame.jpg');
        }

        const resp = await apiFetch('/api/chat/send-stream', { method: 'POST', body: fd });
        if (!resp.ok) {
            let detail = `${resp.status}`;
            try { detail = (await resp.json().catch(() => ({}))).detail || detail; } catch (e) {}
            throw new Error(detail);
        }

        // ---- 解析 SSE 流，打字机式渲染 + 思维链"深度思考中"展示 ----
        removeTyping(typingId); typingId = 0;
        streamingEl = document.createElement('div');
        streamingEl.className = 'message assistant';
        streamingEl.innerHTML = `
            <div class="reasoning-block" style="display:none">
                <div class="reasoning-header" onclick="toggleReasoning(this)"><span class="reasoning-arrow">▶</span><span class="reasoning-title">🧠 深度思考中...</span></div>
                <div class="reasoning-idle" style="display:none"><span class="spinner"></span><span class="idle-text">💭 正在深入思考…</span></div>
                <div class="reasoning-body"></div>
            </div>
            <div class="msg-content"></div><div class="msg-time"></div>`;
        streamingContent = streamingEl.querySelector('.msg-content');
        const reasoningBlock = streamingEl.querySelector('.reasoning-block');
        const reasoningBody = streamingEl.querySelector('.reasoning-body');
        const reasoningTitle = streamingEl.querySelector('.reasoning-title');
        const reasoningIdleEl = streamingEl.querySelector('.reasoning-idle');
        document.getElementById('chatMessages').appendChild(streamingEl);

        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        let raw = '';
        let reasoningText = '';
        let reasoningStarted = false;
        let contentStarted = false;
        let reasoningLastRender = 0;
        // ---- 思考反馈：上游 API 首 token 前常静默很久，用计时/动画让用户知道一直在干活 ----
        const IDLE_THRESHOLD_MS = 2500;   // 思考期静默阈值（有 reasoning 后）
        const WAIT_THRESHOLD_MS = 2500;   // 等待首个 token 阈值（发送后）
        let reasoningStartedAt = 0;       // 思考开始时间（用于标题"已用时"）
        let lastReasoningAt = 0;          // 最近一次收到 reasoning token 的时间
        let reasoningIdle = false;        // 思考期是否处于静默提示态
        let streamStartTime = 0;          // 进入流式（创建 streamingEl）时刻
        let reasoningBlockShown = false;  // 等待期是否已把思考块显示出来
        const idleTextEl = reasoningBlock.querySelector('.idle-text');
        const startThinkingTimer = () => {
            if (thinkingTimer) return;
            thinkingTimer = setInterval(() => {
                const now = Date.now();
                const setLabel = (t) => { if (reasoningTitle.textContent !== t) reasoningTitle.textContent = t; };
                const setIdleText = (t) => { if (idleTextEl.textContent !== t) idleTextEl.textContent = t; };
                // 阶段1：还没收到任何 token —— 等待模型首个输出（最常见的长静默在这里）
                if (!reasoningStarted && !contentStarted) {
                    const waited = now - streamStartTime;
                    if (waited > WAIT_THRESHOLD_MS) {
                        if (!reasoningBlockShown) {
                            reasoningBlockShown = true;
                            reasoningBlock.style.display = '';
                            reasoningIdleEl.style.display = '';
                            setLabel('🧠 深度思考中…');
                        }
                        setIdleText(`⏳ 正在等待模型响应…（已等待 ${Math.round(waited / 1000)}s）`);
                    }
                    return;
                }
                if (contentStarted) return; // 正文开始后由收尾/collapseReasoning 清理
                // 阶段2：思考中 —— 静默判定 + 已用时计时
                const idle = now - lastReasoningAt > IDLE_THRESHOLD_MS;
                if (idle !== reasoningIdle) {
                    reasoningIdle = idle;
                    reasoningIdleEl.style.display = idle ? '' : 'none';
                }
                if (reasoningIdle) setIdleText('💭 正在深入思考…');
                const secs = Math.round((now - reasoningStartedAt) / 1000);
                setLabel(`🧠 深度思考中…（已用时 ${secs}s）`);
            }, 500);
        };
        let sseError = '';
        let done = false;
        // 进入流式即启动心跳：覆盖"等待首个 token"的静默期
        streamStartTime = Date.now();
        startThinkingTimer();

        const renderReasoningThrottled = () => {
            const now = Date.now();
            if (now - reasoningLastRender > 50) {
                reasoningBody.textContent = reasoningText;
                reasoningBody.scrollTop = reasoningBody.scrollHeight;
                reasoningLastRender = now;
                scrollChat();
            }
        };
        const renderThrottled = () => {
            // 节流：高频 delta 不全量触发 KaTeX 重渲染，50ms 一次足够流畅
            const now = Date.now();
            if (now - lastRender > 50) {
                streamingContent.innerHTML = renderMath(escHtml(raw));
                lastRender = now;
                scrollChat();
            }
        };
        // 正文开始后：思考块收起为一行，可点击展开
        const collapseReasoning = () => {
            if (!reasoningStarted) return;
            stopThinkingTimer();
            reasoningIdleEl.style.display = 'none';
            reasoningBlock.classList.add('collapsed');
            reasoningTitle.textContent = '🧠 已深度思考（点击展开）';
            streamingEl.querySelector('.reasoning-arrow').textContent = '▶';
        };

        while (true) {
            const { value, done: readerDone } = await reader.read();
            if (readerDone) break;
            buffer += decoder.decode(value, { stream: true });
            let sep;
            while ((sep = buffer.indexOf('\n\n')) !== -1) {
                const event = buffer.slice(0, sep);
                buffer = buffer.slice(sep + 2);
                for (const line of event.split('\n')) {
                    if (!line.startsWith('data:')) continue;
                    const data = line.slice(5).trim();
                    if (!data) continue;
                    let obj;
                    try { obj = JSON.parse(data); } catch (e) { continue; }
                    if (obj.type === 'reasoning' && obj.text) {
                        // 思维链实时展示（独立于正文，不混入回答）
                        if (!reasoningStarted) {
                            reasoningStarted = true;
                            reasoningStartedAt = Date.now();
                            lastReasoningAt = reasoningStartedAt;
                            reasoningBlock.style.display = '';
                            // 撤掉"等待响应"提示：有思考内容了，转文字滚动
                            reasoningIdleEl.style.display = 'none';
                        }
                        reasoningText += obj.text;
                        lastReasoningAt = Date.now();
                        // 恢复输出：立即撤掉静默提示（不等下一次心跳）
                        if (reasoningIdle) {
                            reasoningIdle = false;
                            reasoningIdleEl.style.display = 'none';
                        }
                        renderReasoningThrottled();
                    } else if (obj.type === 'delta' && obj.text) {
                        if (!contentStarted) {
                            contentStarted = true;
                            stopThinkingTimer();  // 正文开始，停心跳（无论是否走过思考）
                            collapseReasoning();
                        }
                        raw += obj.text;
                        renderThrottled();
                    } else if (obj.type === 'done') {
                        state.conversationId = obj.conversation_id;
                        saveActiveConv();
                        clearQuote();  // 发送成功，撤销引用条
                        done = true;
                    } else if (obj.type === 'error') {
                        sseError = obj.detail || 'AI 调用失败';
                    }
                }
            }
        }

        // 收尾：停止思考心跳，强制渲染最终稿 + 时间戳
        stopThinkingTimer();
        if (streamingContent) {
            streamingContent.innerHTML = renderMath(escHtml(raw));
            streamingEl.querySelector('.msg-time').textContent = time();
            scrollChat();
        }
        if (reasoningStarted && !sseError) {
            // 最终落定思考块状态（正文未出现时也收起为"已思考"）
            reasoningTitle.textContent = '🧠 已深度思考（点击展开）';
            streamingEl.querySelector('.reasoning-arrow').textContent = '▶';
            reasoningBlock.classList.add('collapsed');
        }
        if (sseError) {
            streamingEl.remove();
            addSystemMessage(`⚠️ ${sseError}`);
        } else if (!done) {
            streamingEl.remove();
            addSystemMessage('⚠️ 连接中断，请重试');
        }
        if (state.conversationId) loadConversations();
    } catch (err) {
        stopThinkingTimer();
        if (typingId) removeTyping(typingId);
        if (streamingEl) streamingEl.remove();
        addSystemMessage(`⚠️ ${err.message}`);
    }
    finally {
        document.getElementById('btnSend').disabled = false;
        state.isStreaming = false;
        input.focus({ preventScroll: true });
        resetViewportScroll();
    }
}

function onInputKeydown(e) {
    if (e.key==='Enter'&&!e.shiftKey) { e.preventDefault(); sendMessage(); }
}

// Ctrl+V 粘贴图片
// Ctrl+V 粘贴图片（同时监听 document 级别 + HTML onpaste）
function onPaste(e) {
    const items = e.clipboardData?.items;
    if (!items) { addSystemMessage('⚠️ 无法读取剪贴板'); return; }
    let found = false;
    for (const item of items) {
        if (item.type.startsWith('image/')) {
            e.preventDefault();
            const file = item.getAsFile();
            if (!file) { addSystemMessage('⚠️ 剪贴板图片无法读取'); continue; }
            if (file.size > 10*1024*1024) { addSystemMessage('⚠️ 图片最大 10MB'); return; }
            state.currentImageFile = file;
            const reader = new FileReader();
            reader.onload = ev => {
                state.currentImage = ev.target.result;
                const preview = document.getElementById('imagePreview');
                document.getElementById('previewImg').src = ev.target.result;
                preview.style.display = 'inline-flex';
                addSystemMessage('📋 已粘贴图片');
            };
            reader.readAsDataURL(file);
            found = true;
            break;
        }
    }
    if (!found && items.length > 0) {
        addSystemMessage('ℹ️ 剪贴板中没有图片');
    }
}

// document 级别兜底（防止 textarea focus 丢失）
document.addEventListener('paste', function(e) {
    const target = e.target;
    // 只处理在聊天输入区的粘贴
    if (target && target.id === 'chatInput') return; // onpaste 已处理
    const items = e.clipboardData?.items;
    if (!items) return;
    for (const item of items) {
        if (item.type.startsWith('image/')) {
            e.preventDefault();
            // 聚焦到聊天框
            const input = document.getElementById('chatInput');
            if (input) input.focus({preventScroll: true});
            // 重新触发
            onPaste(e);
            break;
        }
    }
});

async function getModeASubtitleContext() {
    // 模式A上下文 = 整讲字幕全文 + 当前播放位置。
    // 一节视频的字幕仅 1~1.5 万 token（中文 ~1.2 token/字），对 DeepSeek
    // 的大上下文毫无压力；数学讲解前后依赖强（前 30 分钟定理后 30 分钟引用），
    // 全量保留才能让 agent 完整理解整讲脉络，再配合播放位置时间戳，
    // agent 可以精确知道用户学到第几分钟。
    if (!state.subtitles.length) return '';
    const video = document.getElementById('videoPlayer');
    const t = (video && typeof video.currentTime === 'number') ? video.currentTime : 0;
    const d = (video && typeof video.duration === 'number' && isFinite(video.duration)) ? video.duration : 0;
    const pos = `${fmtTime(t)} / ${fmtTime(d)}`;
    // ⚡ 精简模式（设置页勾选）：课程总结 + 当前位置前后 ±10 分钟字幕，回答更快更省
    if (isCompactASubjectCtx() && state.currentVideoId) {
        const WIN = 600;   // ±10 分钟
        let summaryText = '';
        try {
            const resp = await apiFetch(`/api/videos/${state.currentVideoId}/summary`);
            const sd = await resp.json().catch(() => ({}));
            if (resp.ok && sd.status === 'done' && sd.content) summaryText = sd.content.trim();
        } catch (e) {}
        const excerpt = state.subtitles
            .filter(s => s.start >= t - WIN && s.start <= t + WIN)
            .map(s => (s.text || '')).filter(Boolean).join(' ');
        const summaryPart = summaryText ? `\n【课程总结】\n${summaryText}` : '';
        const nearPart = excerpt ? `\n【当前位置附近字幕】\n${excerpt}` : '';
        return `【当前播放位置：${pos}】${summaryPart}${nearPart}`;
    }
    const full = state.subtitles.map(s => (s && s.text) || '').filter(Boolean).join(' ');
    return `【当前播放位置：${pos}】 ${full}`;
}

// ===== 图片上传 =====
function onImagePick(input) {
    const file = input.files[0]; if (!file) return;
    if (!file.type.startsWith('image/')) { alert('请选图片'); return; }
    if (file.size>10*1024*1024) { alert('最大 10MB'); return; }
    state.currentImageFile = file;
    const r = new FileReader();
    r.onload = e => { state.currentImage = e.target.result;
        document.getElementById('previewImg').src = e.target.result;
        document.getElementById('imagePreview').style.display = 'inline-flex'; };
    r.readAsDataURL(file); input.value = '';
}
function removeImage() { state.currentImage=null; state.currentImageFile=null;
    document.getElementById('imagePreview').style.display='none'; }

// ===== 语音输入（浏览器录音 → 后端 Whisper/千问转文字 → 填入输入框）=====
let voiceRecorder = null;   // MediaRecorder
let voiceChunks = [];
let voiceStream = null;     // getUserMedia 流（停止时逐个 track.stop）
let voiceTimer = null;
let voiceSec = 0;
const VOICE_MAX_SEC = 120;  // 最长录音 2 分钟（防忘关）

function voiceBtnEl() { return document.getElementById('voiceInputBtn'); }

function setVoiceBtnUI(recording, sec) {
    const btn = voiceBtnEl();
    if (!btn) return;
    btn.classList.toggle('recording', recording);
    btn.textContent = recording ? `⏺${sec || ''}` : '🎤';
    btn.title = recording ? '点击停止并识别' : '🎤 语音输入（录音后自动填入输入框）';
}

async function toggleVoiceInput() {
    if (voiceRecorder && voiceRecorder.state === 'recording') { stopVoiceInput(); return; }
    if (!navigator.mediaDevices || !window.MediaRecorder) {
        addSystemMessage('⚠️ 当前浏览器不支持录音（需 Chrome/Edge/Firefox 等）');
        return;
    }
    try {
        voiceStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (e) {
        addSystemMessage('⚠️ 无法访问麦克风：请点浏览器地址栏的麦克风图标允许权限后重试');
        return;
    }
    voiceChunks = [];
    voiceSec = 0;
    const mr = new MediaRecorder(voiceStream);
    voiceRecorder = mr;
    mr.ondataavailable = (e) => { if (e.data && e.data.size) voiceChunks.push(e.data); };
    mr.onstop = () => { voiceRecorder = null; handleVoiceChunks(); };
    mr.onerror = () => { voiceRecorder = null; cleanupVoiceStream(); addSystemMessage('⚠️ 录音出错，请重试'); };
    mr.start(250);  // 每 250ms 收集一次数据，防止过长录音丢数据
    setVoiceBtnUI(true, '');
    voiceTimer = setInterval(() => {
        voiceSec++;
        setVoiceBtnUI(true, `${voiceSec}s`);
        if (voiceSec >= VOICE_MAX_SEC) stopVoiceInput();
    }, 1000);
    addSystemMessage('🎤 录音中…说完再点一次按钮停止（最长 2 分钟）');
}

function stopVoiceInput() {
    if (!voiceRecorder || voiceRecorder.state !== 'recording') return;
    try { voiceRecorder.stop(); } catch (e) {}
    if (voiceTimer) { clearInterval(voiceTimer); voiceTimer = null; }
    setVoiceBtnUI(false, '');
}

function cleanupVoiceStream() {
    if (voiceStream) {
        voiceStream.getTracks().forEach(t => { try { t.stop(); } catch (e) {} });
        voiceStream = null;
    }
}

async function handleVoiceChunks() {
    cleanupVoiceStream();
    if (!voiceChunks.length) { addSystemMessage('⚠️ 没有录到声音'); return; }
    addSystemMessage('🎤 正在识别语音…');
    const fd = new FormData();
    fd.append('file', new Blob(voiceChunks, { type: 'audio/webm' }), 'voice.webm');
    try {
        const resp = await apiFetch('/api/voice/transcribe', { method: 'POST', body: fd });
        const d = await resp.json().catch(() => ({}));
        if (!resp.ok) { addSystemMessage(`⚠️ 语音识别失败：${d.detail || resp.status}`); return; }
        const text = (d.text || '').trim();
        if (!text) { addSystemMessage('⚠️ 未识别到内容，请靠近麦克风重试'); return; }
        const ta = document.getElementById('chatInput');
        const cur = ta.value.trim();
        ta.value = cur ? cur + ' ' + text : text;
        ta.focus({ preventScroll: true });
        addSystemMessage(`🎤 已填入输入框（${text.length} 字），可修改后回车发送`);
    } catch (err) {
        addSystemMessage(`⚠️ 语音识别失败：${err.message}`);
    }
}

// ===== 消息渲染 =====
function addUserMessage(text, img, quote) {
    let html = `<div class="message user">`;
    if (quote && quote.trim()) html += `<div class="quote-bubble">📎 引用：${escHtml(quote)}</div>`;
    if (img) html += `<div class="msg-content"><img src="${img}" class="chat-img" onload="scrollChat()"><br>${escHtml(text)}</div>`;
    else html += `<div class="msg-content">${escHtml(text)}</div>`;
    html += `<div class="msg-time">${time()}</div></div>`;
    document.getElementById('chatMessages').innerHTML += html; scrollChat();
}
function addAssistantMessage(text) {
    const safe = escHtml(text);
    const withMath = renderMath(safe);
    document.getElementById('chatMessages').innerHTML +=
        `<div class="message assistant"><div class="msg-content">${withMath}</div><div class="msg-time">${time()}</div></div>`;
    scrollChat();
}
function addSystemMessage(text) {
    document.getElementById('chatMessages').innerHTML +=
        `<div class="message system"><div class="msg-content">${escHtml(text)}</div></div>`;
    scrollChat();
}
// 思维链块：点击标题行展开/折叠（流式与新历史消息共用）
function toggleReasoning(headerEl) {
    const block = headerEl.closest('.reasoning-block');
    if (!block) return;
    const collapsed = block.classList.toggle('collapsed');
    headerEl.querySelector('.reasoning-arrow').textContent = collapsed ? '▶' : '▼';
    if (!collapsed) {
        const body = block.querySelector('.reasoning-body');
        if (body) body.scrollTop = body.scrollHeight;
    }
}
let typingCount=0;
function showTyping() { const id=++typingCount;
    document.getElementById('chatMessages').innerHTML += `<div class="message assistant" id="typing-${id}"><div class="msg-content"><div class="typing-indicator"><span></span><span></span><span></span></div></div></div>`;
    scrollChat(); return id; }
function removeTyping(id) { const e=document.getElementById(`typing-${id}`); if(e) e.remove(); }
function clearChat() { document.getElementById('chatMessages').innerHTML=''; }
function scrollChat() {
    const box = document.getElementById('chatMessages');
    if (!box || !box.scrollHeight) return;
    requestAnimationFrame(() => {
        requestAnimationFrame(() => {
            requestAnimationFrame(() => {
                box.scrollTop = box.scrollHeight;
            });
        });
    });
    // 兜底：100ms 后再滚一次（防 KaTeX / 图片异步加载导致布局延迟）
    setTimeout(() => { if (box) box.scrollTop = box.scrollHeight; }, 100);
}

// ===== 会话管理（新建/删除/重命名/选择/按科目分组） =====
function convLsKey() { return `kaoyan_conv_${state.subject}_${state.mode}`; }
function saveActiveConv() { try { localStorage.setItem(convLsKey(), state.conversationId || ''); } catch(e){} }
function loadActiveConv() { try { return localStorage.getItem(convLsKey()) || ''; } catch(e){ return ''; } }

async function loadConversations() {
    try {
        const resp = await apiFetch(`/api/chat/conversations?subject=${state.subject}`);
        if (!resp.ok) return;
        const groups = await resp.json();
        const box = document.getElementById('conversationList');
        const modeNames = {A:'💬 即时问答', B:'🎯 引导输出', C:'📝 课后问答'};
        let html = '';
        for (const m of ['A','B','C']) {
            const list = groups[m] || [];
            if (!list.length) continue;
            html += `<div class="conv-group"><div class="conv-group-title">${modeNames[m]}</div>`;
            for (const c of list) {
                const active = c.conversation_id === state.conversationId ? ' active' : '';
                html += `<div class="conv-item${active}" onclick="selectConversation('${c.conversation_id}')" title="${escHtml(c.name)}">`
                      + `<span class="conv-name">${escHtml(c.name)}</span>`
                      + `${c.model ? `<span class="conv-model-tag" title="会话模型：${escHtml(c.model)}">${escHtml(c.model)}</span>` : ''}`
                      + `<span class="conv-count">${c.message_count}</span>`
                      + `<span class="conv-ops" onclick="event.stopPropagation()">`
                      + `<button class="conv-op" onclick="renameConversation('${c.conversation_id}')" title="重命名">✏️</button>`
                      + `<button class="conv-op" onclick="deleteConversation('${c.conversation_id}')" title="删除">🗑️</button>`
                      + `</span></div>`;
            }
            html += '</div>';
        }
        if (!html) html = '<div class="conv-empty">（当前科目还没有对话，点上方「＋新对话」开始）</div>';
        box.innerHTML = html;
    } catch(e) {}
}

const convSbKey = 'kaoyan_conv_sidebar';       // '1' 显示 / '0' 隐藏
const convSbWKey = 'kaoyan_conv_sidebar_w';
function setConvSidebarVisible(visible) {
    document.getElementById('conversationSidebar').style.display = visible ? '' : 'none';
    try { localStorage.setItem(convSbKey, visible ? '1' : '0'); } catch(e){}
}
function toggleConversationList() {
    const sb = document.getElementById('conversationSidebar');
    const visible = sb.style.display !== 'none';
    setConvSidebarVisible(!visible);
    if (!visible) loadConversations();
}
function collapseConversationSidebar() { setConvSidebarVisible(false); }
function initConvResizer() {
    const r = document.getElementById('convSidebarResizer');
    const sb = document.getElementById('conversationSidebar');
    if (!r || !sb) return;
    let dragging = false, startX = 0, startW = 0;
    r.addEventListener('mousedown', e => {
        dragging = true; startX = e.clientX; startW = sb.offsetWidth;
        document.body.style.cursor = 'col-resize'; document.body.style.userSelect = 'none';
        r.classList.add('dragging'); e.preventDefault();
    });
    document.addEventListener('mousemove', e => {
        if (!dragging) return;
        let w = startW + (e.clientX - startX);
        w = Math.max(140, Math.min(380, w));
        sb.style.width = w + 'px';
        try { localStorage.setItem(convSbWKey, w); } catch(x){}
    });
    document.addEventListener('mouseup', () => {
        if (dragging) { dragging = false; document.body.style.cursor = ''; document.body.style.userSelect = ''; r.classList.remove('dragging'); }
    });
}
function restoreConvSidebarState() {
    try {
        if (localStorage.getItem(convSbKey) === '0') setConvSidebarVisible(false);
        const w = localStorage.getItem(convSbWKey);
        if (w) document.getElementById('conversationSidebar').style.width = w + 'px';
    } catch(e){}
}

async function newConversation() {
    try {
        const resp = await apiFetch(`/api/chat/conversations?subject=${state.subject}&mode=${state.mode}`, {method:'POST'});
        if (!resp.ok) throw new Error('创建失败');
        const d = await resp.json();
        state.conversationId = d.conversation_id;
        state.convModel = '';
        syncChatModelSelect();
        saveActiveConv();
        clearChat();
        document.querySelectorAll('.b-video-cb').forEach(cb => cb.checked=false);
        addSystemMessage('🆕 已开新对话');
        loadConversations();
    } catch(e){ addSystemMessage('⚠️ 新对话失败: '+e.message); }
}

async function selectConversation(convId) {
    state.conversationId = convId;
    saveActiveConv();
    clearChat();
    try {
        const resp = await apiFetch(`/api/chat/${convId}/history`);
        if (!resp.ok) throw new Error('加载失败');
        const d = await resp.json();
        state.convModel = d.model || '';   // 会话级模型（历史会话恢复时下拉对齐）
        syncChatModelSelect();
        // 一次性拼出全部 HTML 再写入 DOM，避免逐步 append 触发滚动锚定
        const box = document.getElementById('chatMessages');
        let html = '';
        for (const msg of d.messages) {
            const qBlock = (msg.quote && msg.quote.trim())
                ? `<div class="quote-bubble">📎 引用：${escHtml(msg.quote)}</div>` : '';
            if (msg.role === 'user') {
                html += `<div class="message user">${qBlock}<div class="msg-content">${escHtml(msg.content)}</div><div class="msg-time">${time()}</div></div>`;
            } else if (msg.role === 'assistant') {
                // 历史消息带思维链时显示折叠的思考块（点击可展开）
                const rBlock = (msg.reasoning && msg.reasoning.trim())
                    ? `<div class="reasoning-block collapsed"><div class="reasoning-header" onclick="toggleReasoning(this)"><span class="reasoning-arrow">▶</span><span class="reasoning-title">🧠 已深度思考（点击展开）</span></div><div class="reasoning-body">${escHtml(msg.reasoning)}</div></div>`
                    : '';
                html += `<div class="message assistant">${rBlock}<div class="msg-content">${renderMath(escHtml(msg.content))}</div><div class="msg-time">${time()}</div></div>`;
            }
        }
        if (!d.messages.length) html += '<div class="message system"><div class="msg-content">（空对话）</div></div>';
        box.innerHTML = html;
        // 布局完成后再滚到底部（scrollChat 内部有三层 rAF + 100ms 兜底）
        scrollChat();
        loadConversations();
    } catch(e){ addSystemMessage('️ '+e.message); }
}

async function renameConversation(convId) {
    openDialog({
        title: '✏️ 重命名对话',
        bodyHTML: '<label>输入新名称</label><input type="text" id="dlgConvName" maxlength="255" placeholder="给这个对话起个名字">',
        okText: '保存',
        onOk: () => {
            const name = document.getElementById('dlgConvName').value.trim();
            if (!name) { addSystemMessage('⚠️ 名称不能为空'); return; }
            apiFetch(`/api/chat/conversations/${convId}?name=${encodeURIComponent(name)}`, {method:'PATCH'})
                .then(r => {
                    closeDialog();
                    if (r.ok) loadConversations();
                    else r.json().then(d => addSystemMessage(`⚠️ ${d.detail || '重命名失败'}`));
                });
        },
    });
}

async function deleteConversation(convId) {
    openDialog({
        title: '🗑️ 删除对话',
        bodyHTML: '<div class="dialog-warn">将删除这个对话及其全部消息，<b>不可恢复</b>。确定继续吗？</div>',
        okText: '删除',
        onOk: () => {
            apiFetch(`/api/chat/conversations/${convId}`, {method:'DELETE'}).then(r => {
                closeDialog();
                if (r.ok) {
                    if (state.conversationId === convId) newConversation();
                    else loadConversations();
                }
            });
        },
    });
}

async function restoreActiveConversation() {
    const convId = loadActiveConv();
    if (convId) await selectConversation(convId);
}

// ===== 引用（选中 AI 回答某段 → 悬浮按钮 → 引用条 → 发送特别说明） =====
let quoteState = null;   // {text: '...'}
let quoteFloater = null; // 悬浮"引用"按钮元素

function quoteFloaterEl() {
    if (quoteFloater) return quoteFloater;
    quoteFloater = document.createElement('div');
    quoteFloater.id = 'quoteFloater';
    quoteFloater.className = 'quote-floater';
    quoteFloater.style.display = 'none';
    quoteFloater.innerHTML = '<button type="button" onclick="applyQuote()">💬 引用</button>';
    document.body.appendChild(quoteFloater);
    return quoteFloater;
}

// 鼠标抬起：若在 AI 回答块内选中了文字，显示悬浮"引用"按钮
document.addEventListener('mouseup', (e) => {
    const sel = window.getSelection();
    if (!sel || sel.isCollapsed) { hideQuoteFloater(); return; }
    const text = (sel.toString() || '').trim();
    if (!text) { hideQuoteFloater(); return; }
    // 选区锚点需落在 AI 回答内容里（reasoning 思考块不参与）
    let node = sel.anchorNode;
    const inAssistant = node && node.nodeType === 3 ? node.parentElement : node;
    const msgEl = inAssistant ? inAssistant.closest('.message.assistant') : null;
    if (!msgEl || !msgEl.querySelector('.msg-content') || !msgEl.querySelector('.msg-content').contains(inAssistant)) {
        hideQuoteFloater(); return;
    }
    try {
        const rect = sel.getRangeAt(0).getBoundingClientRect();
        if (!rect || !rect.width && !rect.height) { hideQuoteFloater(); return; }
        const f = quoteFloaterEl();
        f.style.display = '';
        let left = rect.right + 8;
        let top = rect.bottom + 8;
        if (left + 90 > window.innerWidth) left = rect.left - 100;
        if (top + 34 > window.innerHeight) top = rect.top - 40;
        f.style.left = left + 'px';
        f.style.top = top + 'px';
        f.dataset.text = text;
    } catch (err) { hideQuoteFloater(); }
});
document.addEventListener('mousedown', (e) => {
    if (quoteFloater && !quoteFloater.contains(e.target)) hideQuoteFloater();
});

function hideQuoteFloater() {
    if (quoteFloater) quoteFloater.style.display = 'none';
}

function applyQuote() {
    const text = quoteFloater ? (quoteFloater.dataset.text || '') : '';
    if (!text) return;
    quoteState = { text: text.slice(0, 500) };
    document.getElementById('quoteBar').style.display = '';
    document.getElementById('quoteBarText').textContent =
        `引用了回答：「${quoteState.text.length > 80 ? quoteState.text.slice(0, 80) + '…' : quoteState.text}」`;
    hideQuoteFloater();
    // 清理选区，聚焦输入框等待补充说明
    try { window.getSelection().removeAllRanges(); } catch (e) {}
    document.getElementById('chatInput').focus();
}

function clearQuote() {
    quoteState = null;
    const bar = document.getElementById('quoteBar');
    if (bar) bar.style.display = 'none';
}

// ===== 会话级模型一键切换（当前 API Key 下可用模型列表，仅本会话生效） =====
let chatModels = [];        // 可用模型列表缓存
let chatModelLoaded = false;
function chatModelSel() { return document.getElementById('chatModelSelect'); }

async function loadChatModelList() {
    try {
        const resp = await apiFetch('/api/config/chat-models', { method: 'POST' });
        const d = await resp.json().catch(() => ({}));
        chatModels = d.models || [];
        state.globalModel = d.current || '';
        const sel = chatModelSel();
        if (!sel) return;
        sel.innerHTML = '';
        if (chatModels.length) {
            chatModels.forEach(m => { const o = document.createElement('option'); o.value = m; o.textContent = m; sel.appendChild(o); });
        } else {
            const o = document.createElement('option');
            o.value = state.globalModel;
            o.textContent = state.globalModel || '（无法获取模型列表）';
            sel.appendChild(o);
            o.disabled = !state.globalModel;
        }
        syncChatModelSelect();
    } catch (e) {}
}

// 把下拉值对齐到当前会话的模型（convModel 优先，其次全局）
function syncChatModelSelect() {
    const sel = chatModelSel();
    if (!sel) return;
    const active = state.convModel || state.globalModel || '';
    const options = [...sel.options];
    const hit = options.find(o => o.value === active);
    if (hit) sel.value = hit.value;
    else if (active) {
        const o = document.createElement('option');
        o.value = active; o.textContent = active;
        sel.appendChild(o); sel.value = active;
    } else if (options.length) {
        sel.selectedIndex = 0;
    }
}

async function onChatModelChange(sel) {
    const model = (sel.value || '').trim();
    if (!model || model === state.convModel) return;
    // 还没有会话时先自动建一个（当前科目/模式），保证模型绑定到会话
    if (!state.conversationId) {
        try {
            const resp = await apiFetch(`/api/chat/conversations?subject=${state.subject}&mode=${state.mode}`, { method: 'POST' });
            if (!resp.ok) throw new Error('创建会话失败');
            const d = await resp.json();
            state.conversationId = d.conversation_id;
            saveActiveConv();
            loadConversations();
        } catch (e) {
            addSystemMessage(`⚠️ ${e.message}`);
            syncChatModelSelect();
            return;
        }
    }
    try {
        const resp = await apiFetch(`/api/chat/conversations/${state.conversationId}/model?model=${encodeURIComponent(model)}`, { method: 'POST' });
        if (!resp.ok) throw new Error('保存失败');
        state.convModel = model;
        addSystemMessage(`✅ 本会话已切换模型：${model}`);
        loadConversations();
    } catch (e) {
        addSystemMessage(`⚠️ 模型切换失败：${e.message}`);
        syncChatModelSelect();
    }
}

// ===== 通用操作弹层（替换原生 prompt/confirm，输入框回车确认、Esc 取消）=====
let dialogOkHandler = null;

function openDialog(opts) {
    const modal = document.getElementById('dialogModal');
    if (!modal) return;
    document.getElementById('dialogTitle').textContent = opts.title || '操作';
    document.getElementById('dialogBody').innerHTML = opts.bodyHTML || '';
    document.getElementById('dialogOkBtn').textContent = opts.okText || '确定';
    dialogOkHandler = opts.onOk || null;
    modal.style.display = 'flex';
    const first = modal.querySelector('input, select, textarea');
    if (first) { setTimeout(() => { try { first.focus(); } catch (e) {} }, 30); }
}

function closeDialog(e) {
    if (e && e.target !== e.currentTarget) return;  // 点背景才触发；按钮走 onclick
    const modal = document.getElementById('dialogModal');
    if (modal) modal.style.display = 'none';
    dialogOkHandler = null;
}

function dialogOk() {
    if (dialogOkHandler) dialogOkHandler();
}

document.getElementById('dialogOkBtn').addEventListener('click', dialogOk);
document.getElementById('dialogModal').addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { e.preventDefault(); closeDialog(); }
    else if (e.key === 'Enter' && e.target && e.target.tagName !== 'TEXTAREA' && e.target.id !== 'dialogOkBtn') {
        e.preventDefault();
        dialogOk();
    }
});

// ===== 设置页一键填充常用服务 =====
const API_PRESETS = {
    main:   { base_url: 'https://api.deepseek.com/v1', model: 'deepseek-v4-flash', multimodal: false },
    vision: { base_url: 'https://open.bigmodel.cn/api/paas/v4', model: 'glm-4v-flash' },
    asr:    { base_url: 'https://dashscope.aliyuncs.com', model: 'qwen3-asr-flash' },
};

function applyPreset(section) {
    const p = API_PRESETS[section];
    const ids = {
        main:   { base: 'setMainBaseUrl', model: 'setMainModel', mm: 'setMainMultimodal' },
        vision: { base: 'setVisionBaseUrl', model: 'setVisionModel' },
        asr:    { base: 'setAsrBaseUrl', model: 'setAsrModel' },
    };
    const m = ids[section];
    if (!p || !m) return;
    const elBase = document.getElementById(m.base);
    if (elBase) elBase.value = p.base_url;
    const elModel = document.getElementById(m.model);
    if (elModel) elModel.value = p.model;
    if (m.mm) {
        const mm = document.getElementById(m.mm);
        if (mm) mm.checked = !!p.multimodal;
    }
    if (elBase) {
        elBase.style.borderColor = 'var(--color-primary)';
        setTimeout(() => { elBase.style.borderColor = ''; }, 1000);
    }
}

// ⚡ 模式A 精简上下文开关（设置页勾选，本地记忆）
function isCompactASubjectCtx() {
    try { return localStorage.getItem('kaoyan_compact_ctx') === '1'; } catch (e) { return false; }
}

// ===== 设置（模型 API 配置，保存即生效）=====
function showSettings() {
    document.getElementById('settingsModal').style.display = 'flex';
    const tokenEl = document.getElementById('setToken');
    if (tokenEl) tokenEl.value = getApiToken();
    // 清空 Key 输入框（留空 = 保留已有 Key），加载当前配置填充
    ['setMainKey', 'setVisionKey', 'setAsrKey'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.value = '';
    });
    loadModelConfig();
}
function closeSettings(e) {
    if (e && e.target !== e.currentTarget) return;
    document.getElementById('settingsModal').style.display = 'none';
}
async function loadModelConfig() {
    try {
        const resp = await apiFetch('/api/config/model');
        if (!resp.ok) return;
        const c = await resp.json();
        const fill = (id, v) => {
            const el = document.getElementById(id);
            if (el && v != null) el.value = v;
        };
        const ph = (id, hasKey) => {
            const el = document.getElementById(id);
            if (el && !hasKey) el.placeholder = '尚未配置 Key';
        };
        fill('setMainBaseUrl', c.main && c.main.base_url);
        fill('setMainModel', c.main && c.main.model);
        const mm = document.getElementById('setMainMultimodal');
        if (mm) mm.checked = !!(c.main && c.main.multimodal);
        const compactA = document.getElementById('setCompactA');
        if (compactA) compactA.checked = isCompactASubjectCtx();
        ph('setMainKey', c.main && c.main.has_key);
        if (c.main && !c.main.has_key) document.getElementById('setMainKey').placeholder = '尚未配置 Key';

        fill('setVisionBaseUrl', c.vision && c.vision.base_url);
        fill('setVisionModel', c.vision && c.vision.model);
        const ve = document.getElementById('setVisionEnabled');
        if (ve) ve.checked = !!(c.vision && c.vision.enabled);
        if (c.vision && !c.vision.has_key) document.getElementById('setVisionKey').placeholder = '尚未配置 Key';

        fill('setAsrBaseUrl', c.asr && c.asr.base_url);
        fill('setAsrModel', c.asr && c.asr.model);
        const ap = document.getElementById('setAsrProvider');
        if (ap) ap.value = (c.asr && c.asr.provider) || 'local';
        const asz = document.getElementById('setAsrSize');
        if (asz) asz.value = (c.asr && c.asr.size) || 'small';
        if (c.asr && !c.asr.has_key) document.getElementById('setAsrKey').placeholder = '尚未配置 Key';
    } catch (e) {}
}
// 从 OpenAI 兼容端点拉取可用模型列表，填充对应 datalist（不落库，用表单当前值）
async function fetchModels(section) {
    const ids = section === 'main'
        ? { base: 'setMainBaseUrl', key: 'setMainKey', model: 'setMainModel', list: 'mainModelList' }
        : section === 'vision'
        ? { base: 'setVisionBaseUrl', key: 'setVisionKey', model: 'setVisionModel', list: 'visionModelList' }
        : { base: 'setAsrBaseUrl', key: 'setAsrKey', model: 'setAsrModel', list: 'asrModelList' };
    const val = id => { const el = document.getElementById(id); return el ? el.value.trim() : ''; };
    const baseUrl = val(ids.base);
    if (!baseUrl) { alert('请先填写 API 地址'); return; }
    const btn = document.querySelector(`#settingsModal .form-group:has(#${ids.model}) .btn-sm`);
    if (btn) btn.textContent = '⏳ 获取中...';
    try {
        const resp = await apiFetch('/api/config/list-models', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ base_url: baseUrl, api_key: val(ids.key) }),
        });
        const d = await resp.json().catch(() => ({}));
        const dl = document.getElementById(ids.list);
        if (dl) dl.innerHTML = '';
        const models = d.models || [];
        if (models.length) {
            models.forEach(m => {
                const o = document.createElement('option');
                o.value = m;
                if (dl) dl.appendChild(o);
            });
            alert(`✅ 获取到 ${models.length} 个模型，点击模型输入框可从列表选择（也可直接手输）`);
        } else {
            alert(`⚠️ 未能获取模型列表${d.error ? `：${d.error}` : ''}\n端点可能不支持 /models 接口，可手动输入模型名`);
        }
    } catch (err) {
        alert(`❌ 获取失败：${err.message}`);
    } finally {
        if (btn) btn.textContent = '🔍 获取模型';
    }
}

async function saveSettings() {
    // 访问令牌（本地）
    const token = document.getElementById('setToken').value.trim();
    try {
        if (token) localStorage.setItem('kaoyan_token', token);
        else localStorage.removeItem('kaoyan_token');
    } catch (e) {}

    try {
        if (document.getElementById('setCompactA').checked) localStorage.setItem('kaoyan_compact_ctx', '1');
        else localStorage.removeItem('kaoyan_compact_ctx');
    } catch (e) {}

    // 模型配置（服务器，留空 Key = 保留已有）
    const payload = {
        main: {
            base_url: document.getElementById('setMainBaseUrl').value.trim(),
            api_key: document.getElementById('setMainKey').value.trim(),
            model: document.getElementById('setMainModel').value.trim(),
            multimodal: document.getElementById('setMainMultimodal').checked,
        },
        vision: {
            enabled: document.getElementById('setVisionEnabled').checked,
            base_url: document.getElementById('setVisionBaseUrl').value.trim(),
            api_key: document.getElementById('setVisionKey').value.trim(),
            model: document.getElementById('setVisionModel').value.trim(),
        },
        asr: {
            base_url: document.getElementById('setAsrBaseUrl').value.trim(),
            api_key: document.getElementById('setAsrKey').value.trim(),
            model: document.getElementById('setAsrModel').value.trim(),
            provider: document.getElementById('setAsrProvider').value,
            size: document.getElementById('setAsrSize').value,
        },
    };
    try {
        const resp = await apiFetch('/api/config/model', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        if (!resp.ok) {
            const d = await resp.json().catch(() => ({}));
            throw new Error(d.detail || resp.status);
        }
        alert('✅ 模型配置已保存，立即生效');
        // 全局模型可能变了：重新拉对话框模型下拉列表
        chatModelLoaded = false;
        loadChatModelList();
    } catch (err) {
        alert(`❌ 保存失败：${err.message}`);
    }
    closeSettings();
}

// ===== KaTeX 公式渲染 =====
function renderMath(text) {
    if (typeof katex === 'undefined') {
        // 本地 KaTeX 未加载完成：不静默裸露，给出提示（text 已 escHtml 转义）
        return text + '<div class="katex-pending">⚠️ 公式渲染组件加载中，请刷新页面后查看</div>';
    }
    // escHtml 会把公式内部的 < > & 等转成 &lt; &gt; &amp;，而 KaTeX 不认 HTML 实体：
    // 普通公式里出现裸 & 会直接解析失败（& 仅在对齐/矩阵环境内合法），导致整段公式乱码。
    // 因此在交给 KaTeX 前，先把公式内容还原回原始 LaTeX 字符（DOM 解码单次完成，&amp; 不会二次解码）。
    const unescapeMath = s => {
        const d = document.createElement('div');
        d.innerHTML = s;
        return d.textContent;
    };
    const render = (formula, display) => {
        try {
            return katex.renderToString(unescapeMath(formula).trim(), { displayMode: display, throwOnError: false });
        } catch (e) {
            // 渲染失败：原样返回公式（保留可读性），不吞不伪装
            return display ? `$$${escHtml(formula)}$$` : `$${escHtml(formula)}$`;
        }
    };

    // 0) 归一化模型偶尔违规输出的形态：\[...\] → $$...$$、\(...\) → $...$
    //    提示词里是软约束，这里在渲染层做硬兜底，任何形态都能渲染
    text = text.replace(/\\\[([\s\S]*?)\\\]/g, (_, f) => `$$${f}$$`);
    text = text.replace(/\\\(([\s\S]*?)\\\)/g, (_, f) => `$${f}$`);

    // 1) $$...$$ 块级公式（允许跨行）
    text = text.replace(/\$\$([\s\S]*?)\$\$/g, (_, formula) => render(formula, true));

    // 2) $...$ 行内公式（不跨行、非贪婪，且要求 $ 前不是另一公式的 $）
    text = text.replace(/(^|[^\$])\$([^\$\n]+?)\$(?!\$)/g, (m, prefix, formula) => prefix + render(formula, false));
    return text;
}

// ===== 播放进度记忆 =====
let progressSaveTimer = null;
document.addEventListener('DOMContentLoaded', () => {
    const video = document.getElementById('videoPlayer');
    video.addEventListener('timeupdate', () => {
        if (!state.currentVideoId) return;
        if (progressSaveTimer) return;
        progressSaveTimer = setTimeout(() => {
            progressSaveTimer = null;
            try {
                localStorage.setItem('kaoyan_progress_' + state.currentVideoId, String(video.currentTime));
                localStorage.setItem('kaoyan_last_video', String(state.currentVideoId));
                // 记录今日观看
                const today = new Date().toISOString().slice(0, 10);
                let watched = JSON.parse(localStorage.getItem('kaoyan_watched') || '{}');
                if (!watched[today]) watched[today] = [];
                if (!watched[today].includes(state.currentVideoId)) {
                    watched[today].push(state.currentVideoId);
                    localStorage.setItem('kaoyan_watched', JSON.stringify(watched));
                }
            } catch(e) {}
        }, 5000);  // 每5秒存一次
    });
    
    // 恢复上次播放
    try {
        const lastId = localStorage.getItem('kaoyan_last_video');
        if (lastId) {
            setTimeout(() => {
                const items = document.querySelectorAll('.tree-video');
                for (const item of items) {
                    if (item.dataset.videoId === lastId) {
                        item.click();
                        break;
                    }
                }
            }, 500);
        }
    } catch(e) {}
});

// ===== 工具 =====
// ===== 模式B 视频选择器 =====
const B_MAX_VIDEOS = 3;  // 模式B 一次最多选几个视频
function onBCheckChange(id, cb) {
    if (cb.checked) {
        const selected = document.querySelectorAll('.b-video-cb:checked');
        if (selected.length > B_MAX_VIDEOS) {
            cb.checked = false;
            addSystemMessage(`⚠️ 模式B最多选 ${B_MAX_VIDEOS} 个视频；要继续引导下一组，请点【🆕 新对话】再选下一组`);
            return;
        }
    }
    sessionStorage.setItem('b_video_'+id, cb.checked ? '1' : '0');
}
function getSelectedBVideos() {
    const cbs = document.querySelectorAll('.b-video-cb:checked');
    return Array.from(cbs).map(c => parseInt(c.dataset.video));
}

function escHtml(t) { const d=document.createElement('div'); d.textContent=t; return d.innerHTML; }
function formatSize(b) { if(!b) return ''; const u=['B','KB','MB','GB']; let s=b,i=0; while(s>=1024&&i<u.length-1){s/=1024;i++} return `${s.toFixed(1)}${u[i]}`; }
function time() { const n=new Date(); return `${String(n.getHours()).padStart(2,'0')}:${String(n.getMinutes()).padStart(2,'0')}`; }
