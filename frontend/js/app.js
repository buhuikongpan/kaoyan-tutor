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
document.addEventListener('DOMContentLoaded', () => { resetViewportScroll(); loadTree(); switchMode('A'); setupSplitters(); initConvResizer(); restoreConvSidebarState(); restoreActiveConversation(); });

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

async function loadTree() {
    const container = document.getElementById('videoTreeBody');
    if (!container) { console.error('videoTreeBody not found'); return; }

    container.innerHTML = '<div style="padding:20px;color:#333">🔍 正在请求数据...</div>';

    try {
        const resp = await apiFetch(`/api/folders/tree?subject=${state.subject}`);
        if (!resp.ok) throw new Error('网络错误');
        const data = await resp.json();
        if (!data.folders.length && !data.uncategorized.length) {
            container.innerHTML = `<div class="empty-state"><div class="icon">📂</div><div>暂无视频</div></div>`;
            return;
        }
        container.innerHTML = '';
        // 渲染文件夹
        for (const f of data.folders) {
            container.appendChild(renderFolderNode(f, 0));
        }
        // 未分类视频
        if (data.uncategorized.length) {
            const ucDiv = document.createElement('div');
            ucDiv.className = 'tree-folder';
            ucDiv.innerHTML = `<div class="tree-folder-label" onclick="toggleFolder(this)">
                <span class="tree-arrow">▶</span><span class="tree-icon">📁</span><span>未分类</span><span class="tree-count">${data.uncategorized.length}</span>
            </div><div class="tree-children" style="display:none">`;
            for (const v of data.uncategorized) {
                ucDiv.querySelector('.tree-children').appendChild(makeVideoNode(v));
            }
            container.appendChild(ucDiv);
        }
        // 若存在生成中的课程总结，20 秒后自动刷新一次树状态（后台任务完成时能跟上）
        if (container.querySelector('.status-summary-processing')) {
            clearTimeout(window._summaryPollTimer);
            window._summaryPollTimer = setTimeout(() => loadTree(), 20000);
        }
    } catch (err) {
        container.innerHTML = `<div class="empty-state"><div class="icon">⚠️</div><div>${err.message}</div></div>`;
    }
}

function renderFolderNode(f, depth) {
    const div = document.createElement('div');
    div.className = 'tree-folder';
    const isExpanded = treeExpanded[f.id] === true;
    const hasChildren = f.children && f.children.length;
    const hasVideos = f.videos && f.videos.length;
    const arrow = (hasChildren || hasVideos) ? (isExpanded ? '▼' : '▶') : '';

    div.innerHTML = `<div class="tree-folder-label" style="padding-left:${depth*16+4}px" onclick="toggleFolder(this)" data-fid="${f.id}">
        <span class="tree-arrow">${arrow}</span>
        <span class="tree-icon">📂</span>
        <span class="tree-fname">${escHtml(f.name)}</span>
        <span class="tree-count">${hasVideos ? f.videos.length : ''}</span>
        <span class="tree-ctx" onclick="event.stopPropagation();renameFolder(${f.id})" title="重命名">✏️</span>
        <span class="tree-ctx" onclick="event.stopPropagation();deleteFolder(${f.id})" title="删除">🗑️</span>
    </div>`;
    const childrenDiv = document.createElement('div');
    childrenDiv.className = 'tree-children';
    childrenDiv.style.display = isExpanded ? 'block' : 'none';
    // 子文件夹
    if (hasChildren) for (const c of f.children) childrenDiv.appendChild(renderFolderNode(c, depth + 1));
    // 视频
    if (hasVideos) for (const v of f.videos) childrenDiv.appendChild(makeVideoNode(v, depth + 1));
    div.appendChild(childrenDiv);
    return div;
}

function makeVideoNode(v, depth = 0) {
    const item = document.createElement('div');
    item.className = `tree-video${v.id === state.currentVideoId ? ' active' : ''}`;
    item.dataset.videoId = v.id;
    const labels = { pending: '待处理', processing: '提取中', done: '已识别', failed: '失败' };
    // 课程总结状态：📄 点击查看 / 生成中 / 失败可重试（无总结不显示）
    const sLabels = { done: '📄', processing: '⏳总结', failed: '📄重试', none: '', pending: '' };
    const sTitle = {
        done: '查看课程总结', processing: '总结生成中', failed: '重新生成总结', none: '', pending: '',
    };
    const sAction = v.summary_status === 'done' ? `viewSummary(${v.id})`
        : v.summary_status === 'failed' ? `generateSummary(${v.id})` : '';
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
    const name = prompt('新建文件夹名称：');
    if (!name) return;
    apiFetch('/api/folders/create', {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({subject: state.subject, name: name.trim()}),
    }).then(r => { if(r.ok) loadTree(); else r.json().then(d => alert(d.detail)); });
}

function renameFolder(id) {
    const name = prompt('新名称：');
    if (!name) return;
    apiFetch(`/api/folders/${id}/rename`, {
        method: 'PUT', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({name: name.trim()}),
    }).then(r => { if(r.ok) loadTree(); });
}

function deleteFolder(id) {
    if (!confirm('删除文件夹？视频会移回未分类。')) return;
    apiFetch(`/api/folders/${id}`, {method:'DELETE'})
        .then(r => { if(r.ok) loadTree(); });
}

function moveVideoDialog(videoId) {
    // 获取文件夹列表供选择
    apiFetch(`/api/folders/tree?subject=${state.subject}`)
        .then(r => r.json()).then(data => {
            const names = ['未分类'];
            const ids = [0];
            function walk(fs) {
                for (const f of fs) { names.push(f.name); ids.push(f.id); walk(f.children || []); }
            }
            walk(data.folders);
            const choice = prompt(`移动到哪个文件夹？\n0: 未分类\n${ids.map((id,i) => i>0 ? `${id}: ${names[i]}` : '').filter(Boolean).join('\n')}\n\n输入文件夹ID：`);
            if (choice === null) return;
            const fid = parseInt(choice);
            if (isNaN(fid)) return;
            apiFetch(`/api/folders/${videoId}/move?folder_id=${fid}`, {method:'PUT'})
                .then(r => { if(r.ok) loadTree(); });
        });
}

// ===== 播放视频 =====
async function playVideo(videoId) {
    state.currentVideoId = videoId;
    document.querySelectorAll('.tree-video').forEach(el => el.classList.toggle('active', parseInt(el.dataset.videoId) === videoId));

    const video = document.getElementById('videoPlayer');
    const placeholder = document.getElementById('videoPlaceholder');
    placeholder.style.display = 'none';
    video.style.display = 'block';
    // <video> 标签无法携带 Authorization 请求头，token 走查询参数（服务端两种都认）
    const tk = getApiToken();
    video.src = getApiUrl(`/api/videos/stream/${videoId}`) + (tk ? `?token=${encodeURIComponent(tk)}` : '');
    video.play();

    await loadSubtitles(videoId);

    if (state.mode === 'A') document.getElementById('subtitlePanel').style.display = 'flex';
    addSystemMessage(`🎬 播放中`);
}

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
    if (!confirm('确定删除？')) return;
    try {
        await apiFetch(`/api/videos/${videoId}`, {method:'DELETE'});
        if (state.currentVideoId === videoId) resetPlayer();
        loadTree();
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
    addUserMessage(displayText, state.currentImage);
    document.getElementById('btnSend').disabled = true;
    state.isStreaming = true;
    let typingId = showTyping();  // 思考期间显示"打字中"
    let streamingEl = null;       // 流式正文字泡（SSE 到达后接管）
    let streamingContent = null;
    let lastRender = 0;
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
        fd.append('subtitle_context', state.mode === 'A' ? getModeASubtitleContext() : '');
        fd.append('conversation_id', state.conversationId);
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

        // ---- 解析 SSE 流，打字机式渲染 ----
        removeTyping(typingId); typingId = 0;
        streamingEl = document.createElement('div');
        streamingEl.className = 'message assistant';
        streamingEl.innerHTML = `<div class="msg-content"></div><div class="msg-time"></div>`;
        streamingContent = streamingEl.querySelector('.msg-content');
        document.getElementById('chatMessages').appendChild(streamingEl);

        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        let raw = '';
        let sseError = '';
        let done = false;

        const renderThrottled = () => {
            // 节流：高频 delta 不全量触发 KaTeX 重渲染，50ms 一次足够流畅
            const now = Date.now();
            if (now - lastRender > 50) {
                streamingContent.innerHTML = renderMath(escHtml(raw));
                lastRender = now;
                scrollChat();
            }
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
                    if (obj.type === 'delta' && obj.text) {
                        raw += obj.text;
                        renderThrottled();
                    } else if (obj.type === 'done') {
                        state.conversationId = obj.conversation_id;
                        saveActiveConv();
                        done = true;
                    } else if (obj.type === 'error') {
                        sseError = obj.detail || 'AI 调用失败';
                    }
                }
            }
        }

        // 收尾：强制渲染最终稿 + 时间戳
        if (streamingContent) {
            streamingContent.innerHTML = renderMath(escHtml(raw));
            streamingEl.querySelector('.msg-time').textContent = time();
            scrollChat();
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

function getModeASubtitleContext() {
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

// ===== 消息渲染 =====
function addUserMessage(text, img) {
    let html = `<div class="message user">`;
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
let typingCount=0;
function showTyping() { const id=++typingCount;
    document.getElementById('chatMessages').innerHTML += `<div class="message assistant" id="typing-${id}"><div class="msg-content"><div class="typing-indicator"><span></span><span></span><span></span></div></div></div>`;
    scrollChat(); return id; }
function removeTyping(id) { const e=document.getElementById(`typing-${id}`); if(e) e.remove(); }
function clearChat() { document.getElementById('chatMessages').innerHTML=''; }
function scrollChat() { document.getElementById('chatMessages').scrollTop = document.getElementById('chatMessages').scrollHeight; }

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
        for (const msg of d.messages) {
            if (msg.role === 'user') addUserMessage(msg.content, undefined);
            else if (msg.role === 'assistant') addAssistantMessage(msg.content);
        }
        if (!d.messages.length) addSystemMessage('（空对话）');
        loadConversations();
    } catch(e){ addSystemMessage('⚠️ '+e.message); }
}

async function renameConversation(convId) {
    const name = prompt('输入新名称：');
    if (name === null || !name.trim()) return;
    try {
        const resp = await apiFetch(`/api/chat/conversations/${convId}?name=${encodeURIComponent(name.trim())}`, {method:'PATCH'});
        if (!resp.ok) throw new Error('重命名失败');
        loadConversations();
    } catch(e){ alert(e.message); }
}

async function deleteConversation(convId) {
    if (!confirm('确定删除这个对话及其全部消息？')) return;
    try {
        const resp = await apiFetch(`/api/chat/conversations/${convId}`, {method:'DELETE'});
        if (!resp.ok) throw new Error('删除失败');
        if (state.conversationId === convId) newConversation();
        else loadConversations();
    } catch(e){ alert(e.message); }
}

async function restoreActiveConversation() {
    const convId = loadActiveConv();
    if (convId) await selectConversation(convId);
}

// ===== 设置 =====
function showSettings() {
    document.getElementById('settingsModal').style.display = 'flex';
    const tokenEl = document.getElementById('setToken');
    if (tokenEl) tokenEl.value = getApiToken();
    loadConfigStatus();
}
function closeSettings(e) {
    if (e && e.target !== e.currentTarget) return;
    document.getElementById('settingsModal').style.display = 'none';
}
async function loadConfigStatus() {
    try {
        const resp = await apiFetch('/api/config/status');
        if (!resp.ok) return;
        const s = await resp.json();
        const set = (id, ok, txt) => {
            const el = document.getElementById(id);
            if (el) el.textContent = ok ? '✅ ' + txt : '❌ ' + txt;
        };
        set('stDeepseek', s.deepseek, 'DeepSeek 已配置（聊天）');
        set('stZhipu', s.zhipu, '智谱已配置（看图）');
        set('stQwen', s.qwen, '千问已配置（字幕 ASR）');
        set('stAuth', s.auth_enabled, '鉴权已启用（请在下栏填写令牌）');
        const stAuth = document.getElementById('stAuth');
        if (stAuth && !s.auth_enabled) stAuth.textContent = '未启用（局域网内任何设备可访问）';
    } catch (e) {}
}
function saveSettings() {
    const token = document.getElementById('setToken').value.trim();
    try {
        if (token) localStorage.setItem('kaoyan_token', token);
        else localStorage.removeItem('kaoyan_token');
    } catch (e) {}
    alert(token ? '✅ 令牌已保存，立即生效' : '✅ 已清除令牌（若服务器 .env 仍配置了 PLATFORM_TOKEN，接口将无法访问）');
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
    // 替换 $$...$$ 块级公式
    text = text.replace(/\$\$([\s\S]*?)\$\$/g, (_, formula) => {
        try {
            return katex.renderToString(unescapeMath(formula).trim(), {displayMode: true, throwOnError: false});
        } catch(e) {
            return `<code>$$${escHtml(formula)}$$</code>`;
        }
    });
    // 替换 $...$ 行内公式
    text = text.replace(/\$([^\$\n]+?)\$/g, (_, formula) => {
        try {
            return katex.renderToString(unescapeMath(formula).trim(), {displayMode: false, throwOnError: false});
        } catch(e) {
            return `$${escHtml(formula)}$`;
        }
    });
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
