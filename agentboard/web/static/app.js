// AgentBoard SPA —— 纯前端，通过 fetch 调用 REST API（前后端分离）。
const API = window.AGENTBOARD_API || "http://127.0.0.1:8000";
let META = { types: ["task", "bug"], statuses: ["backlog", "todo", "in_progress", "in_review", "verifying", "done"], priorities: ["highest", "high", "medium", "low", "lowest"] };
let PROJECTS = []; // 缓存项目列表供侧栏使用
let GLOBAL_SEARCH = ""; // A-05 全局搜索框当前查询词
let kbdSel = -1; // A-15 键盘快捷键：当前选中项索引（每次 render 重置）
const API_PLURAL = { project: "projects", epic: "epics", story: "stories", task: "tasks" }; // A-19 实体→REST 复数（story 不规则）

// Epic 7 鉴权：token 生命周期与用户态（后端端点已存在，仅前端集成）
function getToken() { return localStorage.getItem("agentboard_token"); }
function setToken(t, username) { localStorage.setItem("agentboard_token", t); if (username) localStorage.setItem("agentboard_user", username); }
function clearToken() { localStorage.removeItem("agentboard_token"); localStorage.removeItem("agentboard_user"); }
let CURRENT_USER = null;      // 当前登录用户（含 id/username）
let _AUTH_VISIBLE = false;    // 登录界面是否正在显示（render 守卫，避免覆盖登录屏）
let _AUTH_MODE = "login";     // "login" | "register"

// ---------- HTTP ----------
async function api(path, method = "GET", body) {
  const opt = { method, headers: {} };
  const token = getToken();
  if (token) opt.headers["Authorization"] = "Bearer " + token;
  if (body !== undefined) { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
  const r = await fetch(API + path, opt);
  if (r.status === 401) {
    // 令牌失效或后端要求鉴权：跳回登录（auth 端点自身不触发，避免递归）
    if (!path.startsWith("/api/auth/") && !_AUTH_VISIBLE) showAuthScreen();
    throw new Error("unauthorized");
  }
  if (!r.ok) {
    let msg = r.status + "";
    try { const j = await r.json(); msg = j.detail || msg; } catch (e) {}
    throw new Error(msg);
  }
  if (r.status === 204) return null;
  return r.json();
}

// ---------- utils ----------
const $ = (id) => document.getElementById(id);
const esc = (s) => (s || "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
// A-12 Toast 堆叠：每条提示一个 .toast-item，进场滑入淡入、2.5s 后淡出移除（可选 type: error|success）
function toast(msg, type) {
  const c = $("toast");
  const el = document.createElement("div");
  el.className = "toast-item" + (type ? " toast-" + type : "");
  el.textContent = msg;
  c.appendChild(el);
  requestAnimationFrame(() => el.classList.add("toast-in"));
  setTimeout(() => { el.classList.remove("toast-in"); el.classList.add("toast-out"); setTimeout(() => el.remove(), 280); }, 2500);
}
function route(hash) { location.hash = hash; }

// A-16 复制深链：复制当前项的可分享深链接（如 #/task/123 = 完整 URL + hash 锚点）。
// 优先 navigator.clipboard，不可用时回退 execCommand，复制成功以 toast 反馈。
function copyLink(href) {
  const url = location.origin + location.pathname + href;
  const done = () => toast("已复制链接");
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(url).then(done).catch(() => fallbackCopy(url, done));
  } else {
    fallbackCopy(url, done);
  }
}
function fallbackCopy(text, cb) {
  const ta = document.createElement("textarea");
  ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
  document.body.appendChild(ta); ta.focus(); ta.select();
  try { document.execCommand("copy"); cb(); } catch (e) { toast("复制失败"); }
  ta.remove();
}

// A-10 深色模式：切换 <html data-theme> 并持久化到 localStorage（默认浅色）
const THEME_KEY = "agentboard_theme";
// A-20 前端偏好本地存储：Story 页任务区视图（列表/看板）持久化键
const VIEW_KEY = "agentboard_story_view";
// B-06 列表分组：Story 任务列表分组方式（none/status/type）持久化键
const GROUP_KEY = "agentboard_story_group";
function applyTheme(theme) {
  const t = theme || localStorage.getItem(THEME_KEY) || "light";
  document.documentElement.setAttribute("data-theme", t);
  const btn = $("theme-toggle");
  if (btn) btn.innerHTML = t === "dark"
    ? '<svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="3.5"/><path d="M10 2v2M10 16v2M2 10h2M16 10h2M4.3 4.3l1.4 1.4M14.3 14.3l1.4 1.4M15.7 4.3l-1.4 1.4M5.7 14.3l-1.4 1.4"/></svg>'
    : '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M16.5 12.4A7 7 0 0 1 7.6 3.5 7 7 0 1 0 16.5 12.4Z"/></svg>';
}
function toggleTheme() {
  const cur = document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
  const next = cur === "dark" ? "light" : "dark";
  localStorage.setItem(THEME_KEY, next);
  applyTheme(next);
}

// minimal markdown renderer
function md(src) {
  if (!src) return '<span class="muted">（空）</span>';
  let h = esc(src);
  h = h.replace(/```([\s\S]*?)```/g, (m, c) => `<pre>${c.replace(/^\n/, "")}</pre>`);
  h = h.replace(/`([^`]+)`/g, "<code>$1</code>");
  h = h.replace(/^###### (.*)$/gm, "<h6>$1</h6>").replace(/^##### (.*)$/gm, "<h5>$1</h5>")
       .replace(/^#### (.*)$/gm, "<h4>$1</h4>").replace(/^### (.*)$/gm, "<h3>$1</h3>")
       .replace(/^## (.*)$/gm, "<h2>$2</h2>").replace(/^# (.*)$/gm, "<h1>$1</h1>");
  h = h.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  h = h.replace(/^\s*[-*] (.*)$/gm, "<li>$1</li>");
  h = h.replace(/(<li>[\s\S]*?<\/li>)/g, m => "<ul>" + m + "</ul>");
  h = h.replace(/\n{2,}/g, "<br><br>").replace(/\n/g, "<br>");
  return h;
}

function crumbs(items) {
  const bar = $("crumb-bar");
  if (!bar) return;
  bar.innerHTML = items.map((it, i) =>
    i < items.length - 1
      ? `<a href="${it.href}">${esc(it.label)}</a><span class="crumb-sep">›</span>`
      : `<span class="crumb-current" aria-current="page">${esc(it.label)}</span>`
  ).join("");
  bar.style.display = items.length > 1 ? "" : "none";
}

// 状态 → 中文映射 & 颜色
const STATUS_LABEL = {
  backlog: "待规划", todo: "待处理", in_progress: "进行中",
  in_review: "评审中", verifying: "验证中", done: "已完成"
};
const STATUS_COLOR = {
  backlog: "#6b7280", todo: "#3b82f6", in_progress: "#f59e0b",
  in_review: "#8b5cf6", verifying: "#06b6d4", done: "#10b981"
};
function statusBadge(s, extra = "") {
  const label = STATUS_LABEL[s] || s;
  return `<span class="badge status status--${s}"${extra}><span class="badge-dot" aria-hidden="true"></span>${label}</span>`;
}
function statusDot(s) {
  const color = STATUS_COLOR[s] || "#6b7280";
  return `<span class="status-dot" style="background:${color}" title="${STATUS_LABEL[s]||s}"></span>`;
}
const PRIORITY_LABEL = { highest: "最高", high: "高", medium: "中", low: "低", lowest: "最低" };
function priorityBadge(p) {
  p = p || "medium";
  const paths = {
    highest: '<path d="M5 10l3-4 3 4M5 15l3-4 3 4"/>',
    high: '<path d="M4 11l4-5 4 5M8 6v8"/>',
    medium: '<path d="M8 4l4 4-4 4-4-4 4-4Z"/>',
    low: '<path d="M4 5l4 5 4-5M8 2v8"/>',
    lowest: '<path d="M5 4l3 4 3-4M5 9l3 4 3-4"/>'
  };
  if (!paths[p]) p = "medium";
  return `<span class="badge priority priority--${p}" title="优先级：${PRIORITY_LABEL[p] || p}"><svg viewBox="0 0 16 16" aria-hidden="true">${paths[p]}</svg>${PRIORITY_LABEL[p] || p}</span>`;
}

function avatar(name, extra = "") {
  const value = (name || "?").trim();
  const initials = value.split(/[\s_-]+/).filter(Boolean).slice(0, 2).map(x => x[0]).join("").toUpperCase() || "?";
  const isAgent = /agent|buddy|codex|qoder/i.test(value);
  return `<span class="avatar${isAgent ? " avatar-agent" : ""}${extra ? " " + extra : ""}" title="${esc(value)}">${esc(initials)}</span>`;
}

// P-15 Agent 活动面板：聚合评论为"近期动态"时间线，复用 avatar() 呈现作者（Agent 自动带标记）。
function timeAgo(iso) {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  const s = Math.floor((Date.now() - d.getTime()) / 1000);
  if (s < 60) return "刚刚";
  if (s < 3600) return Math.floor(s / 60) + " 分钟前";
  if (s < 86400) return Math.floor(s / 3600) + " 小时前";
  if (s < 2592000) return Math.floor(s / 86400) + " 天前";
  return d.toLocaleDateString();
}
function activityPanel(items) {
  const body = items.length
    ? `<ul class="activity-list">${items.map(c => `
      <li class="activity-item">
        ${avatar(c.author, "activity-avatar")}
        <div class="activity-body">
          <div class="activity-line"><strong>${esc(c.author)}</strong> 评论了 <a class="activity-task" href="#/task/${c.taskId}">${esc(c.taskTitle)}</a></div>
          <p class="activity-text">${esc((c.content || "").slice(0, 90))}</p>
          <time class="activity-time">${timeAgo(c.created_at)}</time>
        </div>
      </li>`).join("")}</ul>`
    : '<div class="empty-inline">暂无动态。在任务详情发表评论后，Agent 与成员的最新进展会显示在这里。</div>';
  return `<aside class="activity-panel">
    <div class="activity-head"><h3>近期动态</h3><span class="activity-tag">Agent 活动</span></div>
    ${body}
  </aside>`;
}

function statIcon(kind) {
  const icons = {
    projects: '<rect x="3" y="4" width="14" height="12" rx="2"/><path d="M3 8h14M8 8v8"/>',
    epics: '<path d="M5 4h10v12H5zM8 2v4M12 2v4M8 9h4M8 12h4"/>',
    stories: '<path d="M4 3h12v14H4zM7 7h6M7 10h6M7 13h4"/>',
    tasks: '<circle cx="10" cy="10" r="7"/><path d="m7 10 2 2 4-5"/>',
    rate: '<path d="M3 15V9M8 15V5M13 15V2M2 15h15"/>'
  };
  return `<svg viewBox="0 0 20 20" aria-hidden="true">${icons[kind]}</svg>`;
}
// A-09 进度条：按子项（任务）status 计算完成度（done 占比），在 Epic/Story 卡片底部显示细进度条。
function progressBar(done, total) {
  if (!total) return "";
  const pct = Math.round(done / total * 100);
  const color = pct >= 100 ? "var(--success)" : pct >= 50 ? "var(--primary)" : "#94a3b8";
  return `<div class="entity-progress" title="${done}/${total} 完成">
    <div class="progress-track"><div class="progress-fill" style="width:${pct}%;background:${color}"></div></div>
    <span class="progress-pct">${pct}%</span>
  </div>`;
}
// A-06 状态流转按钮组：Jira 式工作流按钮（仅展示合法迁移），点击即 PUT /api/tasks/{id}/status。
// 与后端 service.TRANSITIONS 保持一致；后端仍为权威校验，非法迁移会被 400 拒绝（防御性）。
const STATUS_TRANSITIONS = {
  backlog: ["todo"],
  todo: ["in_progress", "backlog"],
  in_progress: ["in_review", "verifying", "todo"],
  in_review: ["done", "in_progress"],
  verifying: ["done", "in_progress"],
  done: ["in_progress"],
};
function statusFlow(t) {
  const cur = t.status;
  const nexts = STATUS_TRANSITIONS[cur] || [];
  const curPill = `<span class="sf-current status--${cur}">${STATUS_LABEL[cur] || cur}</span>`;
  if (!nexts.length) return `<div class="status-flow">${curPill}<span class="sf-done-hint">✔ 终态</span></div>`;
  const btns = nexts.map(n =>
    `<button class="sf-btn status--${n}" data-next="${n}">${STATUS_LABEL[n] || n}</button>`
  ).join('<span class="sf-arrow">→</span>');
  return `<div class="status-flow" id="status-flow">${curPill}<span class="sf-arrow">→</span>${btns}</div>`;
}
// 任务类型图标（内联 SVG，不引入图标库）：task=勾选圆环，bug=瓢虫
function typeIcon(type) {
  if (type === "bug") {
    return `<svg class="ti-svg" viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" aria-hidden="true">
      <ellipse cx="8" cy="9" rx="5" ry="5.2"/><line x1="8" y1="4" x2="8" y2="14"/>
      <circle cx="8" cy="3.3" r="1.6" fill="currentColor" stroke="none"/>
      <circle cx="5.4" cy="8.6" r="1" fill="currentColor" stroke="none"/>
      <circle cx="10.6" cy="8.6" r="1" fill="currentColor" stroke="none"/>
      <circle cx="6" cy="12" r="1" fill="currentColor" stroke="none"/>
      <circle cx="10" cy="12" r="1" fill="currentColor" stroke="none"/></svg>`;
  }
  return `<svg class="ti-svg" viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
    <circle cx="8" cy="8" r="6.4"/><path d="M5.4 8l1.9 1.9L10.8 6"/></svg>`;
}

// 只读看板：按 status 分列展示 task 卡片（复用已加载的 tasks 与 statusBadge）
function renderKanban(tasks) {
  const cols = META.statuses.map(s => {
    const items = tasks.filter(t => t.status === s);
    const cards = items.length
      ? items.map(t => `<a data-task-id="${t.id}" class="kanban-card">
          <span class="type-icon ${t.type}">${typeIcon(t.type)}</span>
          <span class="kanban-card-title">${esc(t.title)}</span>
          ${priorityBadge(t.priority)}
        </a>`).join("")
      : '<div class="kanban-empty">—</div>';
    return `<div class="kanban-col">
      <div class="kanban-col-head">${statusBadge(s)}<span class="kanban-count">${items.length}</span></div>
      ${cards}
    </div>`;
  }).join("");
  return `<div class="kanban">${cols}</div>`;
}

// B-06 列表分组（纯前端）：将 Story 任务渲染为「不分组 / 按状态 / 按类型」的列表。
// 分组维度取自后端已返回的 status/type 字段，无需新增 API。
function storyTaskItemHTML(t) {
  return `<a data-task-id="${t.id}" class="entity-item">
    <div class="entity-item-main">
      <span class="type-icon ${t.type}">${typeIcon(t.type)}</span>
      <span class="entity-item-title">${esc(t.title)}</span>
    </div>
    <div class="entity-item-badges">
      ${t.type === "bug" ? `<span class="badge bug">${typeIcon("bug")} Bug</span>` : ""}
      ${priorityBadge(t.priority)}
      ${statusBadge(t.status)}
    </div>
    ${entityActions("task", t.id)}
  </a>`;
}
function storyTaskListHTML(tasks, groupBy) {
  if (!tasks.length) return emptyState("🔧", "暂无任务", "添加 Task 或 Bug 开始推进工作", { id: "s-new-task", label: "＋ 新建任务" });
  if (groupBy === "none") return `<div class="entity-list">${tasks.map(storyTaskItemHTML).join("")}</div>`;
  const keys = groupBy === "status" ? META.statuses : META.types;
  const labelOf = k => groupBy === "status" ? (STATUS_LABEL[k] || k) : (k === "bug" ? "Bug" : "Task");
  const iconOf = k => groupBy === "status" ? statusBadge(k) : `<span class="type-icon ${k}">${typeIcon(k)}</span>`;
  return `<div class="group-wrap">${keys.map(k => {
    const items = tasks.filter(t => (groupBy === "status" ? t.status : t.type) === k);
    if (!items.length) return "";
    return `<div class="group-head">${iconOf(k)}<span class="group-label">${labelOf(k)}</span><span class="group-count">${items.length}</span></div>
      <div class="entity-list">${items.map(storyTaskItemHTML).join("")}</div>`;
  }).join("")}</div>`;
}

// ---------- 侧栏 ----------
let sidebarOpen = true;
let storyViewMode = localStorage.getItem(VIEW_KEY) || "list"; // "list" | "board"（Story 页任务区视图切换，A-20 记住上次选择）
let storyGroupBy = localStorage.getItem(GROUP_KEY) || "none"; // "none" | "status" | "type"（B-06 任务列表分组，纯前端）

function toggleSidebar() {
  sidebarOpen = !sidebarOpen;
  document.body.classList.toggle("sidebar-collapsed", !sidebarOpen);
}

async function renderSidebar() {
  const tree = $("sidebar-tree");
  if (!tree) return;
  try {
    PROJECTS = await api("/api/projects");
    tree.innerHTML = PROJECTS.length
      ? PROJECTS.map(p => `
          <div class="sidebar-item">
            <a href="#/project/${p.id}" class="sidebar-link${(location.hash||"#/")===`#/project/${p.id}`?" active":""}">
              <span class="sidebar-icon"><svg viewBox="0 0 18 18" aria-hidden="true"><path d="M2.5 5.5h5l1.5 2h6.5v7H2.5zM2.5 5.5v-2h4l1.5 2"/></svg></span>
              <span class="sidebar-label">${esc(p.name)}</span>
              ${p.key ? `<span class="sidebar-key">${esc(p.key)}</span>` : ""}
            </a>
          </div>`).join("")
      : '<div class="sidebar-empty">暂无项目<br><small>点击 ＋ 创建</small></div>';
  } catch (e) {
    tree.innerHTML = '<div class="muted">加载失败</div>';
  }
}

// ---------- 导航高亮 ----------
function highlightNav(hash) {
  document.querySelectorAll(".topbar-nav a").forEach(a => a.classList.remove("active"));
  const target = document.querySelector(`.topbar-nav a[data-nav="${hash === "#/" || hash === "" ? "home" : hash === "#/projects" ? "projects" : ""}"]`);
  if (target) target.classList.add("active");
}

// A-07 加载骨架屏：用与真实布局近似的占位块 + shimmer 动画替代单纯 spinner，避免内容载入时的布局跳动。
function skeleton() {
  const card = (w) => `<div class="sk-card">
    <div class="sk-line sk-title" style="width:${w}%"></div>
    <div class="sk-line" style="width:92%"></div>
    <div class="sk-line" style="width:68%"></div>
  </div>`;
  const cards = Array.from({ length: 6 }, (_, i) => card(50 + ((i * 7) % 35))).join("");
  return `<div class="skeleton">
    <div class="sk-header"><div class="sk-line sk-h"></div></div>
    <div class="sk-grid">${cards}</div>
  </div>`;
}

// A-08 空状态优化：统一的友好空状态（图标 + 文案 + 可选动作按钮），
// 替代原本灰色「暂无 xxx」纯文字，向 Jira 的空状态引导靠拢。
// cta 可选 { id, label }：点击触发同页已有的「＋ 新建」按钮（onclick 由其绑定处理）。
function emptyState(icon, title, desc, cta) {
  const btn = cta
    ? `<button class="btn-primary-sm" onclick="document.getElementById('${cta.id}').click()">${esc(cta.label)}</button>`
    : "";
  const art = `<svg class="empty-art" viewBox="0 0 120 96" fill="none" aria-hidden="true">
    <rect x="20" y="22" width="80" height="58" rx="10" stroke="currentColor" stroke-width="3"/>
    <path d="M20 40h80M37 31h18" stroke="currentColor" stroke-width="3" stroke-linecap="round"/>
    <rect x="34" y="51" width="52" height="10" rx="5" fill="currentColor" opacity=".12"/>
    <circle cx="60" cy="73" r="12" fill="var(--brand-soft)"/>
    <path d="m54 73 4 4 8-9" stroke="var(--brand-600)" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>
  </svg>`;
  return `<div class="empty-state empty-compact">
    <div class="empty-icon">${art}</div>
    <h3>${esc(title)}</h3>
    ${desc ? `<p class="muted">${esc(desc)}</p>` : ""}
    ${btn}
  </div>`;
}

// ---------- Router ----------
async function render() {
  if (_AUTH_VISIBLE) return; // Epic 7：已跳登录界面时不渲染应用主体
  const h = location.hash || "#/";
  const app = $("app");
  kbdSel = -1; // A-15 每次视图切换重置键盘选中态
  app.innerHTML = skeleton();
  highlightNav(h);
  renderSidebar();

  try {
    const m = h.match(/^#\/(project|epic|story|task)\/(\d+)$/);
    if (h === "#/" || h === "") await viewHome(app);
    else if (h === "#/projects") await viewProjects(app);
    else if (m && m[1] === "project") await viewProject(app, +m[2]);
    else if (m && m[1] === "epic") await viewEpic(app, +m[2]);
    else if (m && m[1] === "story") await viewStory(app, +m[2]);
    else if (m && m[1] === "task") await viewTask(app, +m[2]);
    else app.innerHTML = '<div class="card empty-state">🔍 页面不存在</div>';
  } catch (e) {
    app.innerHTML = `<div class="card error-state">
      <h3>⚠ 加载失败</h3>
      <p>${esc(e.message)}</p>
      <p class="muted">请确认后端服务已启动：${API}</p>
    </div>`;
  }
  applySearch();
  // A-17 路由过渡：每次视图切换后重新触发主内容区淡入/上滑动画（复用 fadeIn keyframe）
  app.classList.remove("route-in");
  void app.offsetWidth; // 强制回流以重启 CSS 动画
  app.classList.add("route-in");
}

// A-05 全局搜索框：按标题实时过滤当前页列表（纯前端过滤，不改 API）。
// 遍历当前页所有可搜索列表容器，按条目标题文本匹配 q；空结果时给出提示。
function applySearch() {
  const q = (GLOBAL_SEARCH || "").trim().toLowerCase();
  const scopes = document.querySelectorAll(".project-grid, .entity-list, .table-wrap, #story-board-view");
  scopes.forEach(scope => {
    const rows = scope.classList.contains("table-wrap")
      ? scope.querySelectorAll("tbody tr")
      : scope.querySelectorAll(".project-card, .entity-item, .kanban-card");
    let visible = 0;
    rows.forEach(r => {
      const titleEl = r.querySelector(".project-card-name, .entity-item-title, .kanban-card-title");
      const text = titleEl ? titleEl.textContent : r.textContent;
      const show = !q || text.toLowerCase().includes(q);
      r.style.display = show ? "" : "none";
      if (show) visible++;
    });
    let hint = scope.querySelector(":scope > .search-empty-hint");
    if (!visible && q) {
      if (!hint) {
        hint = document.createElement("div");
        hint.className = "search-empty-hint empty-inline";
        hint.textContent = `未找到匹配「${GLOBAL_SEARCH.trim()}」`;
        scope.appendChild(hint);
      }
    } else if (hint) hint.remove();
  });
  // B-06 分组视图：全局搜索过滤后隐藏空分组标题
  if (q) document.querySelectorAll(".group-wrap").forEach(w => {
    w.querySelectorAll(".group-head").forEach(h => {
      const list = h.nextElementSibling;
      if (list && list.classList.contains("entity-list")) {
        const anyVisible = [...list.querySelectorAll(".entity-item")].some(el => el.style.display !== "none");
        h.style.display = anyVisible ? "" : "none";
      }
    });
  });
}

// ========== Views ==========

// ----- 首页仪表盘 -----
async function viewHome(app) {
  crumbs([{ label: "仪表盘" }]);
  const ps = PROJECTS.length ? PROJECTS : await api("/api/projects");
  PROJECTS = ps;

  // 汇总统计
  let totalEpics = 0, totalStories = 0, totalTasks = 0, doneTasks = 0;
  const projectStats = [];
  const allTasks = [];
  for (const p of ps) {
    try {
      const eps = await api(`/api/projects/${p.id}/epics`);
      totalEpics += eps.length;
      let pStories = 0, pTasks = 0, pDone = 0;
      for (const e of eps) {
        const sts = await api(`/api/epics/${e.id}/stories`);
        pStories += sts.length;
        for (const s of sts) {
          const ts = await api(`/api/stories/${s.id}/tasks`);
          pTasks += ts.length;
          pDone += ts.filter(t => t.status === "done").length;
          ts.forEach(t => allTasks.push({ id: t.id, title: t.title, status: t.status }));
        }
      }
      totalStories += pStories; totalTasks += pTasks; doneTasks += pDone;
      projectStats.push({ ...p, epics: eps.length, stories: pStories, tasks: pTasks, done: pDone });
    } catch (e) {
      projectStats.push({ ...p, epics: "?", stories: "?", tasks: "?", done: "?" });
    }
  }

  // P-15 Agent 活动面板：复用仪表盘已枚举的 task 列表，并行拉取各自评论（沿用 /api/tasks/{id}/comments），
  // 汇总成"近期动态"时间线。不新增后端端点、不改变 API 契约；任一请求失败则降级为空面板。
  let activity = [];
  if (allTasks.length) {
    try {
      const groups = await Promise.all(allTasks.map(t => api(`/api/tasks/${t.id}/comments`).catch(() => [])));
      activity = [];
      allTasks.forEach((t, i) => (groups[i] || []).forEach(c =>
        activity.push({ id: c.id, author: c.author, content: c.content, created_at: c.created_at, taskId: t.id, taskTitle: t.title })));
      activity.sort((a, b) => (b.created_at || "").localeCompare(a.created_at || ""));
      activity = activity.slice(0, 12);
    } catch (e) { activity = []; }
  }

  app.innerHTML = `
    <div class="dashboard">
      <section class="hero">
        <div><span class="hero-kicker">AGENTBOARD WORKSPACE</span><h1>项目协作一目了然</h1><p>${ps.length} 个项目空间 · ${totalTasks} 项任务正在持续推进</p></div>
        <span class="hero-pill"><span></span> 实时进度已同步</span>
      </section>
      <!-- 统计卡片 -->
      <div class="stats-row">
        <div class="stat-card stat-brand"><div class="stat-icon">${statIcon("projects")}</div><div class="stat-content">
          <div class="stat-number">${ps.length}</div>
          <div class="stat-label">项目</div><div class="stat-trend">协作空间总数</div></div></div>
        <div class="stat-card stat-violet"><div class="stat-icon">${statIcon("epics")}</div><div class="stat-content">
          <div class="stat-number">${totalEpics}</div>
          <div class="stat-label">Epic</div><div class="stat-trend">目标持续拆解</div></div></div>
        <div class="stat-card stat-warning"><div class="stat-icon">${statIcon("stories")}</div><div class="stat-content">
          <div class="stat-number">${totalStories}</div>
          <div class="stat-label">Story</div><div class="stat-trend">需求条目总数</div></div></div>
        <div class="stat-card stat-success"><div class="stat-icon">${statIcon("tasks")}</div><div class="stat-content">
          <div class="stat-number">${totalTasks}</div>
          <div class="stat-label">任务</div><div class="stat-trend"><strong>${doneTasks}</strong> 项已完成</div></div></div>
        <div class="stat-card stat-rate highlight"><div class="stat-icon">${statIcon("rate")}</div><div class="stat-content">
          <div class="stat-number">${totalTasks ? Math.round(doneTasks/totalTasks*100) : 0}%</div>
          <div class="stat-label">完成率</div><div class="stat-trend">整体交付进度</div></div></div>
      </div>

      <!-- 项目列表 -->
      <div class="section-header">
        <h2>项目总览</h2>
        <button id="home-new-project" class="btn-primary-sm">＋ 新建项目</button>
      </div>
      ${ps.length ? `
        <div class="project-grid">
          ${projectStats.map((p, i) => {
            const pct = typeof p.done === "number" && typeof p.tasks === "number" && p.tasks ? Math.round(p.done / p.tasks * 100) : 0;
            return `
            <a href="#/project/${p.id}" class="project-card project-hue-${i % 4}" style="--project-progress:${pct}%">
              <div class="project-card-header">
                <span class="project-card-name">${esc(p.name)}</span>
                ${p.key ? `<span class="project-key">${esc(p.key)}</span>` : ""}
              </div>
              ${p.description ? `<div class="project-card-desc">${esc(p.description.substring(0, 100))}${p.description.length > 100 ? "…" : ""}</div>` : ""}
              <div class="project-card-stats">
                <span>${p.epics} Epic</span>
                <span>${p.stories} Story</span>
                <span>${p.tasks} 任务</span>
                ${typeof p.done === "number" ? `<span class="text-success">${p.done} 完成</span>` : ""}
              </div>
              <div class="project-progress"><span style="width:${pct}%"></span></div><div class="project-progress-label"><span>完成进度</span><strong>${pct}%</strong></div>
            </a>
          `}).join("")}
        </div>
      ` : `
        ${emptyState("projects", "还没有项目", "创建你的第一个项目管理空间", { id: "home-new-project", label: "创建项目" })}
      `}

      <!-- 快捷操作 -->
      <div class="section-header" style="margin-top:32px">
        <h2>快捷操作</h2>
      </div>
      <div class="quick-actions">
        <button onclick="route('#/projects')" class="action-card">所有项目 <span>→</span></button>
        <button onclick="showNewProjectModal()" class="action-card">新建项目 <span>＋</span></button>
      </div>
      ${activityPanel(activity)}
    </div>`;

  // 绑定新建项目
  const btn = $("home-new-project");
  if (btn) btn.onclick = () => showNewProjectModal();
}

// ----- 项目列表页（完整视图）-----
async function viewProjects(app) {
  crumbs([{ label: "项目" }]);
  const ps = PROJECTS.length ? PROJECTS : await api("/api/projects");
  PROJECTS = ps;

  app.innerHTML = `
    <div class="page-header">
      <h2>所有项目</h2>
      <button id="proj-new-btn" class="btn-primary">＋ 新建项目</button>
    </div>
    ${ps.length ? `
      <div class="table-wrap">
        <table class="data-table">
          <thead><tr>
            <th>项目名称</th><th>短码</th><th>描述</th><th>操作</th>
          </tr></thead>
          <tbody>
            ${ps.map(p => `
              <tr>
                <td><a href="#/project/${p.id}" class="link-bold">${esc(p.name)}</a></td>
                <td>${p.key ? `<span class="badge">${esc(p.key)}</span>` : '<span class="muted">—</span>'}</td>
                <td class="cell-truncate">${esc(p.description || "").substring(0, 80) || '<span class="muted">—</span>'}</td>
                <td><a href="#/project/${p.id}" class="link">打开</a></td>
              </tr>
            `).join("")}
          </tbody>
        </table>
      </div>
    ` : `
      ${emptyState("projects", "暂无项目", "创建第一个项目管理空间", { id: "proj-new-btn", label: "创建第一个项目" })}
    `}`;

  const btn = $("proj-new-btn");
  if (btn) btn.onclick = () => showNewProjectModal();
}

// ----- 项目详情 -----
async function viewProject(app, id) {
  const p = await api(`/api/projects/${id}`);
  const eps = await api(`/api/projects/${id}/epics`);
  // A-09：聚合每个 Epic 下所有 Story 的任务完成度（done 占比）
  const epicProgress = {};
  for (const e of eps) {
    try {
      const stories = await api(`/api/epics/${e.id}/stories`);
      let done = 0, total = 0;
      for (const s of stories) {
        const tasks = await api(`/api/stories/${s.id}/tasks`);
        total += tasks.length;
        done += tasks.filter(t => t.status === "done").length;
      }
      epicProgress[e.id] = { done, total };
    } catch (_) { epicProgress[e.id] = { done: 0, total: 0 }; }
  }
  crumbs([{ label: "仪表盘", href: "#/" }, { label: p.name }]);
  // 高亮侧栏
  document.querySelectorAll(".sidebar-link").forEach(a => a.classList.remove("active"));
  const activeLink = document.querySelector(`.sidebar-link[href="#/project/${id}"]`);
  if (activeLink) activeLink.classList.add("active");

  app.innerHTML = `
    <div class="page-header">
      <div class="page-title-row">
        <h2>${esc(p.name)}</h2>
        ${p.key ? `<span class="badge lg">${esc(p.key)}</span>` : ""}
      </div>
      <div class="page-actions">
        <button id="p-edit-toggle" class="ghost-sm">✏ 编辑</button>
      </div>
    </div>

    ${p.description ? `<div class="card">${md(p.description)}</div>` : ""}

    <!-- Epics 列表 -->
    <div class="section-header">
      <h3>Epics <span class="count">${eps.length}</span></h3>
      <button id="p-new-epic" class="btn-primary-sm">＋ 新建 Epic</button>
    </div>
    ${eps.length ? `
      <div class="entity-list">
        ${eps.map(e => `
          <a href="#/epic/${e.id}" class="entity-item">
            <div class="entity-item-main">
              ${statusDot(e.status)}
              <span class="entity-item-title">${esc(e.title)}</span>
            </div>
            ${statusBadge(e.status)}
            ${progressBar((epicProgress[e.id]||{}).done, (epicProgress[e.id]||{}).total)}
            ${entityActions("epic", e.id)}
          </a>
        `).join("")}
      </div>
    ` : emptyState("🗂", "暂无 Epic", "创建第一个 Epic 来组织你的 Story", { id: "p-new-epic", label: "＋ 新建 Epic" })}

    <!-- 编辑区域 -->
    <div class="card form-section" id="p-edit-area" style="display:none;margin-top:16px">
      <h4>✏ 编辑项目</h4>
      <form id="p-edit-form">
        <label>名称</label><input name="name" value="${esc(p.name)}" required>
        <label>短码</label><input name="key" value="${esc(p.key || "")}">
        <label>描述 (markdown)</label><textarea name="description" rows="4">${esc(p.description)}</textarea>
        <div class="row">
          <button type="submit" class="btn-primary-sm">保存修改</button>
          <button type="button" class="danger" id="p-del-btn">删除项目</button>
          <button type="button" class="ghost-sm" onclick="$('#/p-edit-area').style.display='none'">取消</button>
        </div>
      </form>
    </div>`;

  // 绑定事件
  const newEpicBtn = $("p-new-epic");
  if (newEpicBtn) newEpicBtn.onclick = () => showCreateModal("epic", id);

  const editToggle = $("p-edit-toggle");
  if (editToggle) editToggle.onclick = () => {
    const f = $("p-edit-area"); f.style.display = f.style.display === "none" ? "" : "none";
  };
  bindForm("p-edit-form", async (d) => {
    await api(`/api/projects/${id}`, "PATCH", { name: d.name, key: d.key || null, description: d.description });
    toast("已保存"); render();
  });
  const delBtn = $("p-del-btn");
  if (delBtn) delBtn.onclick = async () => {
    if (!confirm("删除项目及其所有子项？此操作不可撤销！")) return;
    await api(`/api/projects/${id}`, "DELETE"); toast("已删除"); route("#/");
  };

  attachInlineEditList(app);
  attachEntityActions(app);
}

// ----- Epic 详情 -----
async function viewEpic(app, id) {
  const ep = await api(`/api/epics/${id}`);
  const sts = await api(`/api/epics/${id}/stories`);
  // A-09：计算每个 Story 下任务的完成度（done 占比），用于卡片进度条
  const storyProgress = {};
  for (const s of sts) {
    try {
      const tasks = await api(`/api/stories/${s.id}/tasks`);
      storyProgress[s.id] = { done: tasks.filter(t => t.status === "done").length, total: tasks.length };
    } catch (_) { storyProgress[s.id] = { done: 0, total: 0 }; }
  }
  const proj = PROJECTS.find(p => p.id === ep.project_id);
  crumbs([
    { label: "仪表盘", href: "#/" },
    { label: proj ? proj.name : "项目", href: `#/project/${ep.project_id}` },
    { label: ep.title }
  ]);

  app.innerHTML = `
    <div class="page-header">
      <div class="page-title-row">
        <h2>${esc(ep.title)}</h2>
        ${statusBadge(ep.status)}
      </div>
    </div>
    ${ep.description ? `<div class="card">${md(ep.description)}</div>` : ""}

    <div class="section-header">
      <h3>Stories <span class="count">${sts.length}</span></h3>
      <button id="e-new-story" class="btn-primary-sm">＋ 新建 Story</button>
    </div>
    ${sts.length ? `
      <div class="entity-list">
        ${sts.map(s => `
          <a href="#/story/${s.id}" class="entity-item">
            <div class="entity-item-main">
              ${statusDot(s.status)}
              <span class="entity-item-title">${esc(s.title)}</span>
            </div>
            ${statusBadge(s.status)}
            ${progressBar((storyProgress[s.id]||{}).done, (storyProgress[s.id]||{}).total)}
            ${entityActions("story", s.id)}
          </a>
        `).join("")}
      </div>
    ` : emptyState("📝", "暂无 Story", "把需求拆成一个个 Story 逐步推进", { id: "e-new-story", label: "＋ 新建 Story" })}

    <div class="card form-section" style="margin-top:16px">
      <details><summary class="summary-edit">✏ 编辑 Epic</summary>
        <form id="e-edit">
          <label>标题</label><input name="title" value="${esc(ep.title)}" required>
          <label>状态</label>${statusSelect(ep.status)}
          <label>描述 (markdown)</label><textarea name="description" rows="4">${esc(ep.description)}</textarea>
          <div class="row">
            <button type="submit" class="btn-primary-sm">保存</button>
            <button type="button" class="danger" id="e-del">删除 Epic</button>
          </div>
        </form>
      </details>
    </div>`;

  const newBtn = $("e-new-story");
  if (newBtn) newBtn.onclick = () => showCreateModal("story", id);
  bindForm("e-edit", async (d) => {
    await api(`/api/epics/${id}`, "PATCH", { title: d.title, status: d.status, description: d.description });
    toast("已保存"); render();
  });
  const eDel = $("e-del");
  if (eDel) eDel.onclick = async () => {
    if (!confirm("删除 Epic 及其子项？")) return;
    await api(`/api/epics/${id}`, "DELETE"); toast("已删除"); route(`#/project/${ep.project_id}`);
  };

  attachInlineEditList(app);
  attachEntityActions(app);
}

// ----- Story 详情 -----
async function viewStory(app, id) {
  const st = await api(`/api/stories/${id}`);
  const ep = await api(`/api/epics/${st.epic_id}`);
  const proj = PROJECTS.find(p => p.id === ep.project_id);
  const tasks = await api(`/api/stories/${id}/tasks`);
  crumbs([
    { label: "仪表盘", href: "#/" },
    { label: proj ? proj.name : "项目", href: `#/project/${ep.project_id}` },
    { label: "Epic", href: `#/epic/${st.epic_id}` },
    { label: st.title }
  ]);

  app.innerHTML = `
    <div class="page-header">
      <div class="page-title-row">
        <h2>${esc(st.title)}</h2>
        ${statusBadge(st.status)}
      </div>
    </div>
    ${st.description ? `<div class="card">${md(st.description)}</div>` : ""}

    <div class="section-header">
      <h3>Tasks / Bugs <span class="count">${tasks.length}</span></h3>
      <div class="page-actions">
        <div class="seg">
          <button class="seg-btn${storyViewMode === "list" ? " active" : ""}" data-mode="list">列表</button>
          <button class="seg-btn${storyViewMode === "board" ? " active" : ""}" data-mode="board">看板</button>
        </div>
        <select id="s-group-by" class="select-sm" title="任务列表分组方式">
          <option value="none"${storyGroupBy === "none" ? " selected" : ""}>不分组</option>
          <option value="status"${storyGroupBy === "status" ? " selected" : ""}>按状态</option>
          <option value="type"${storyGroupBy === "type" ? " selected" : ""}>按类型</option>
        </select>
        <button id="s-new-task" class="btn-primary-sm">＋ 新建</button>
        <button class="ghost-sm" id="copy-story-link" title="复制此项深链接">🔗 复制链接</button>
      </div>
    </div>
    <div id="story-list-view"${storyViewMode === "board" ? ' style="display:none"' : ""}>
      ${storyTaskListHTML(tasks, storyGroupBy)}
    </div>
    <div id="story-board-view"${storyViewMode === "list" ? ' style="display:none"' : ""}>
      ${renderKanban(tasks)}
    </div>

    <div class="card form-section" style="margin-top:16px">
      <details><summary class="summary-edit">✏ 编辑 Story</summary>
        <form id="s-edit">
          <label>标题</label><input name="title" value="${esc(st.title)}" required>
          <label>状态</label>${statusSelect(st.status)}
          <label>描述 (markdown)</label><textarea name="description" rows="4">${esc(st.description)}</textarea>
          <div class="row">
            <button type="submit" class="btn-primary-sm">保存</button>
            <button type="button" class="danger" id="s-del">删除 Story</button>
          </div>
        </form>
      </details>
    </div>`;

  const newBtn = $("s-new-task");
  if (newBtn) newBtn.onclick = () => showCreateModal("task", id, { projectId: ep.project_id });
  document.querySelectorAll(".seg-btn").forEach(b => b.onclick = () => {
    storyViewMode = b.dataset.mode;
    localStorage.setItem(VIEW_KEY, storyViewMode); // A-20 持久化视图偏好
    $("story-list-view").style.display = storyViewMode === "board" ? "none" : "";
    $("story-board-view").style.display = storyViewMode === "list" ? "none" : "";
    document.querySelectorAll(".seg-btn").forEach(x => x.classList.toggle("active", x.dataset.mode === storyViewMode));
  });
  const gs = $("s-group-by");
  if (gs) gs.onchange = () => {
    storyGroupBy = gs.value;
    localStorage.setItem(GROUP_KEY, storyGroupBy); // B-06 记住分组偏好
    const lv = $("story-list-view");
    if (lv) lv.innerHTML = storyTaskListHTML(tasks, storyGroupBy);
    applySearch();
  };
  const copyBtnS = $("copy-story-link");
  if (copyBtnS) copyBtnS.onclick = () => copyLink(`#/story/${id}`);
  bindForm("s-edit", async (d) => {
    await api(`/api/stories/${id}`, "PATCH", { title: d.title, status: d.status, description: d.description });
    toast("已保存"); render();
  });
  const sDel = $("s-del");
  if (sDel) sDel.onclick = async () => {
    if (!confirm("删除 Story 及其任务？")) return;
    await api(`/api/stories/${id}`, "DELETE"); toast("已删除"); route(`#/epic/${st.epic_id}`);
  };

  attachTaskDrawer(app);
  attachEntityActions(app);
}

// ----- Task 详情 -----
async function viewTask(app, id) {
  const t = await api(`/api/tasks/${id}`);
  const comments = await api(`/api/tasks/${id}/comments`);
  crumbs([
    { label: "仪表盘", href: "#/" },
    { label: "Story", href: `#/story/${t.story_id}` },
    { label: t.title }
  ]);

  app.innerHTML = `
    <div class="page-header">
      <div class="page-title-row">
        <h2 id="task-title">${esc(t.title)}</h2>
        <div class="header-badges">
          ${t.type === "bug" ? `<span class="badge bug">${typeIcon("bug")} Bug</span>` : `<span class="badge task-badge">${typeIcon("task")} Task</span>`}
          ${priorityBadge(t.priority)}
          ${statusBadge(t.status, ' id="stbadge"')}
        </div>
      </div>
      <div class="page-actions">
        <button class="ghost-sm" id="copy-task-link" title="复制此项深链接">🔗 复制链接</button>
        ${statusFlow(t)}
      </div>
    </div>

    <div class="two-col">
      <div class="card"><h3>Description</h3><div class="md">${md(t.description)}</div></div>
      <div class="card"><h3>Spec（OpenSpec）</h3><div class="md">${md(t.spec)}</div></div>
    </div>

    <div class="card form-section">
      <h3>✏ 编辑</h3>
      <form id="t-edit">
        <label>标题</label><input name="title" value="${esc(t.title)}" required>
        <label>类型</label>${typeSelect(t.type)}
        <label>优先级</label>${prioritySelect(t.priority)}
        <label>Description (markdown)</label>${mdToolbar("description")}<textarea name="description" rows="5">${esc(t.description)}</textarea>
        <label>Spec (markdown)</label>${mdToolbar("spec")}<textarea name="spec" rows="12">${esc(t.spec)}</textarea>
        <div class="row" style="flex-wrap:wrap">
          <button type="submit" class="btn-primary-sm">💾 保存</button>
          <button type="button" class="ghost-sm" id="tpl">📝 插入提案模板</button>
          <button type="button" class="ghost-sm" id="gen">⚡ 生成子任务</button>
          <button type="button" class="danger" id="del">🗑 删除</button>
        </div>
      </form>
    </div>

    <div class="card comments-card">
      <div class="section-header"><h3>评论 <span class="count">${comments.length}</span></h3></div>
      <div class="comment-list">
        ${comments.length ? comments.map(c => `<article class="comment-item">
          ${avatar(c.author, "comment-avatar")}
          <div class="comment-body"><div class="comment-meta"><strong>${esc(c.author)}</strong><time>${new Date(c.created_at).toLocaleString()}</time><button class="comment-delete" data-comment-id="${c.id}" title="删除评论">×</button></div><div class="md">${md(c.content)}</div></div>
        </article>`).join("") : '<div class="empty-inline">还没有评论。可在这里记录决策，Agent 也会在此同步进展。</div>'}
      </div>
      <form id="comment-form" class="comment-form">
        <input name="author" value="${esc(localStorage.getItem("agentboard_comment_author") || "我")}" placeholder="作者（人或 Agent）" required>
        <textarea name="content" rows="3" placeholder="添加评论（支持 markdown）" required></textarea>
        <div><button type="submit" class="btn-primary-sm">发表评论</button></div>
      </form>
    </div>`;

  document.querySelectorAll("#status-flow [data-next]").forEach(b => {
    b.onclick = async () => {
      const next = b.dataset.next;
      b.disabled = true;
      try {
        await api(`/api/tasks/${id}/status`, "PUT", { status: next });
        toast("状态已更新"); render();
      } catch (e) { toast("更新失败：" + e.message); b.disabled = false; }
    };
  });
  const copyBtn = $("copy-task-link");
  if (copyBtn) copyBtn.onclick = () => copyLink(`#/task/${id}`);
  bindForm("t-edit", async (d) => {
    await api(`/api/tasks/${id}`, "PATCH", { title: d.title, type: d.type, priority: d.priority, description: d.description, spec: d.spec });
    toast("已保存"); render();
  });
  bindForm("comment-form", async (d) => {
    localStorage.setItem("agentboard_comment_author", d.author);
    await api(`/api/tasks/${id}/comments`, "POST", { author: d.author, content: d.content });
    toast("评论已添加"); render();
  });
  document.querySelectorAll("[data-comment-id]").forEach(b => b.onclick = async () => {
    if (!confirm("删除这条评论？")) return;
    await api(`/api/comments/${b.dataset.commentId}`, "DELETE");
    toast("评论已删除"); render();
  });
  $("tpl").onclick = () => {
    const ta = document.querySelector('textarea[name="spec"]');
    ta.value = `# 变更提案：${t.title}\n\n## 背景\n\n## 目标\n\n## 范围\n\n## 任务清单\n- [ ] \n\n## 验收标准\n- [ ] `;
  };
  $("del").onclick = async () => {
    if (!confirm("删除该任务？")) return;
    await api(`/api/tasks/${id}`, "DELETE"); toast("已删除"); route(`#/story/${t.story_id}`);
  };
  $("gen").onclick = async () => {
    if (!confirm("从 spec 中的清单项生成同级子任务？")) return;
    try {
      const created = await api(`/api/tasks/${id}/generate-subtasks`, "POST");
      toast(`已生成 ${created.length} 个子任务`); render();
    } catch (e) { toast("生成失败：" + e.message); }
  };

  const th = $("task-title");
  if (th) makeInlineEditableDetail(th, { type: "task", id: id, onSaved: (v) => {
    const cur = document.querySelector(".crumb-current"); if (cur) cur.textContent = v;
  } });

  bindMdToolbar(app);
}

// ========== 公共组件 ==========

let modalReturnFocus = null;

const CREATE_META = {
  project: { title: "创建项目", eyebrow: "PROJECT", submit: "创建项目", field: "项目名称", placeholder: "例如：AgentBoard 产品研发" },
  epic: { title: "创建 Epic", eyebrow: "EPIC", submit: "创建 Epic", field: "Epic 标题", placeholder: "描述一个阶段性业务目标" },
  story: { title: "创建 Story", eyebrow: "STORY", submit: "创建 Story", field: "Story 标题", placeholder: "描述一个可交付的用户需求" },
  task: { title: "创建工作项", eyebrow: "WORK ITEM", submit: "创建", field: "标题", placeholder: "需要完成什么？" }
};

function showNewProjectModal() { showCreateModal("project"); }

// Jira 风格统一创建弹窗：项目 / Epic / Story / Task / Bug 共用交互、校验和关闭行为。
function showCreateModal(kind, parentId, context = {}) {
  const meta = CREATE_META[kind];
  if (!meta) return;
  const existing = document.getElementById("create-modal");
  if (existing) existing.remove();
  modalReturnFocus = document.activeElement;
  const overlay = document.createElement("div");
  overlay.id = "create-modal";
  overlay.className = "modal-overlay create-overlay";
  overlay.setAttribute("role", "presentation");
  const projectFields = kind === "project" ? `
    <div class="form-field"><label for="create-key">短码 <span class="field-optional">可选</span></label>
    <input id="create-key" name="key" maxlength="12" placeholder="例如：AB" autocomplete="off"><div class="field-help">用于工作项编号，建议使用 2–6 个大写字母</div></div>` : "";
  const taskFields = kind === "task" ? `
    <div class="form-grid"><div class="form-field"><label for="create-type">工作项类型</label>${typeSelect("task").replace("<select", '<select id="create-type"')}</div>
    <div class="form-field"><label for="create-priority">优先级</label>${prioritySelect("medium").replace("<select", '<select id="create-priority"')}</div></div>` : "";
  overlay.innerHTML = `<section class="modal modal-create" role="dialog" aria-modal="true" aria-labelledby="create-modal-title">
    <header class="modal-header"><div><span class="modal-eyebrow">${meta.eyebrow}</span><h3 id="create-modal-title">${meta.title}</h3></div>
      <button type="button" class="modal-close" data-modal-close aria-label="关闭弹窗">×</button></header>
    <form id="create-form" novalidate>
      <div class="modal-body">
        <div class="form-field"><label for="create-title">${meta.field} <span class="required">*</span></label>
        <input id="create-title" name="title" placeholder="${meta.placeholder}" required maxlength="200" autocomplete="off"><div class="field-error" aria-live="polite"></div></div>
        ${projectFields}${taskFields}
        <div class="form-field"><label for="create-description">描述 <span class="field-optional">可选</span></label>
        <textarea id="create-description" name="description" rows="5" placeholder="添加背景、验收标准或相关说明（支持 Markdown）"></textarea></div>
      </div>
      <footer class="modal-footer"><span class="modal-shortcut">${navigator.platform.includes("Mac") ? "⌘" : "Ctrl"} + Enter 创建</span>
        <div class="modal-actions"><button type="button" class="ghost" data-modal-close>取消</button><button type="submit" class="btn-primary">${meta.submit}</button></div></footer>
    </form></section>`;
  document.body.appendChild(overlay);
  document.body.classList.add("modal-open");
  const form = overlay.querySelector("form"), title = overlay.querySelector("#create-title");
  const close = () => closeModal("create-modal");
  overlay.querySelectorAll("[data-modal-close]").forEach(btn => btn.onclick = close);
  overlay.onclick = e => { if (e.target === overlay) close(); };
  form.onkeydown = e => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); form.requestSubmit(); } };
  form.onsubmit = async e => {
    e.preventDefault();
    const error = overlay.querySelector(".field-error");
    if (!title.value.trim()) { title.setAttribute("aria-invalid", "true"); error.textContent = "请输入标题"; title.focus(); return; }
    title.removeAttribute("aria-invalid"); error.textContent = "";
    const submit = form.querySelector('[type="submit"]'), data = Object.fromEntries(new FormData(form));
    submit.disabled = true; submit.textContent = "创建中…";
    try {
      if (kind === "project") await api("/api/projects", "POST", { name: data.title.trim(), key: data.key.trim().toUpperCase() || null, description: data.description });
      else if (kind === "epic") await api(`/api/projects/${parentId}/epics`, "POST", { title: data.title.trim(), description: data.description });
      else if (kind === "story") await api(`/api/epics/${parentId}/stories`, "POST", { title: data.title.trim(), description: data.description });
      else await api(`/api/stories/${parentId}/tasks`, "POST", { project_id: context.projectId, title: data.title.trim(), type: data.type, priority: data.priority, description: data.description });
      closeModal("create-modal"); toast(`${kind === "task" ? (data.type === "bug" ? "Bug" : "Task") : meta.eyebrow} 已创建`, "success"); render();
    } catch (err) { toast("创建失败：" + err.message, "error"); submit.disabled = false; submit.textContent = meta.submit; }
  };
  requestAnimationFrame(() => { overlay.classList.add("open"); title.focus(); });
}

function closeModal(id = "create-modal", restoreFocus = true) {
  const el = document.getElementById(id);
  if (!el) return;
  el.classList.remove("open"); document.body.classList.remove("modal-open");
  setTimeout(() => el.remove(), 160);
  if (restoreFocus && modalReturnFocus && document.contains(modalReturnFocus)) modalReturnFocus.focus();
}

// 通用表单绑定
function bindForm(formId, handler) {
  const f = $(formId);
  if (!f) return;
  f.onsubmit = async (e) => {
    e.preventDefault();
    const d = Object.fromEntries(new FormData(e.target));
    await handler(d);
  };
}

function statusSelect(cur, id) {
  return `<select name="status"${id ? ` id="${id}"` : ""}>` +
    META.statuses.map(s =>
      `<option class="status--${s}" value="${s}"${s === cur ? " selected" : ""}>${STATUS_LABEL[s] || s}</option>`
    ).join("") + "</select>";
}
function typeSelect(cur) {
  return `<select name="type">` +
    META.types.map(t => `<option value="${t}"${t === cur ? " selected" : ""}>${t === "task" ? "Task" : "Bug"}</option>`).join("") +
    "</select>";
}

function prioritySelect(cur = "medium") {
  return `<select name="priority">` +
    (META.priorities || ["highest", "high", "medium", "low", "lowest"]).map(p =>
      `<option value="${p}"${p === cur ? " selected" : ""}>${PRIORITY_LABEL[p] || p}</option>`
    ).join("") + `</select>`;
}

// A-14 Markdown 编辑工具栏：在 description/spec 文本框上方加「加粗/标题/列表/行内代码」快捷按钮，
// 点击即向对应 textarea 插入 markdown 语法（行内类包裹选区，块级类在行首插入）。纯前端，不改 API。
function mdToolbar(taName) {
  return `<div class="md-toolbar" data-ta="${taName}">
    <button type="button" class="md-tb-btn" data-md="bold" title="加粗">B</button>
    <button type="button" class="md-tb-btn" data-md="h2" title="标题">H</button>
    <button type="button" class="md-tb-btn" data-md="list" title="列表">•</button>
    <button type="button" class="md-tb-btn" data-md="code" title="行内代码">{ }</button>
  </div>`;
}
function insertMd(ta, kind) {
  const s = ta.selectionStart, e = ta.selectionEnd, v = ta.value;
  const sel = v.slice(s, e);
  const wrap = { bold: ["**", "**", "加粗文本"], code: ["`", "`", "代码"] };
  const block = { h2: ["## ", "标题"], list: ["- ", "列表项"] };
  if (wrap[kind]) {
    const [pre, post, ph] = wrap[kind];
    const text = sel || ph, ins = pre + text + post;
    ta.value = v.slice(0, s) + ins + v.slice(e);
    ta.setSelectionRange(s + pre.length, s + pre.length + text.length);
  } else {
    const [pre, ph] = block[kind];
    const lineStart = v.lastIndexOf("\n", s - 1) + 1;
    const text = sel || ph, ins = pre + text;
    ta.value = v.slice(0, lineStart) + ins + v.slice(lineStart);
    ta.setSelectionRange(lineStart + pre.length, lineStart + pre.length + text.length);
  }
  ta.focus();
}
function bindMdToolbar(scope) {
  scope.querySelectorAll(".md-toolbar").forEach(bar => {
    const ta = scope.querySelector(`textarea[name="${bar.dataset.ta}"]`);
    if (!ta) return;
    bar.querySelectorAll(".md-tb-btn").forEach(b => b.onclick = () => insertMd(ta, b.dataset.md));
  });
}

// A-04 行内快速编辑标题：双击进入编辑态，回车/失焦 PATCH 保存，Esc 取消
function inlineEditEnter(elm, opts) {
  if (elm.querySelector("input")) return;
  const original = (elm.dataset.title || elm.textContent).trim();
  elm.dataset.title = original;
  elm.innerHTML = `<input class="inline-edit-input" value="${esc(original)}">`;
  const inp = elm.querySelector("input");
  inp.focus(); inp.select();
  const revert = () => { elm.textContent = elm.dataset.title; };
  const commit = async () => {
    if (!elm.querySelector("input")) return;
    const v = inp.value.trim();
    if (!v || v === original) { revert(); return; }
    try {
      await api(`/api/${API_PLURAL[opts.type] || opts.type + "s"}/${opts.id}`, "PATCH", { title: v });
      elm.dataset.title = v; elm.textContent = v;
      toast("标题已更新");
      if (opts.onSaved) opts.onSaved(v);
    } catch (e) { toast("保存失败：" + e.message); revert(); }
  };
  inp.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") { ev.preventDefault(); commit(); }
    else if (ev.key === "Escape") { ev.preventDefault(); revert(); }
  });
  inp.addEventListener("blur", commit);
}

// 列表项标题位于 <a> 内：单击应导航，双击编辑 → 用计时区分（避免双击先触发跳转销毁元素）
function makeInlineEditable(elm, opts) {
  elm.classList.add("inline-editable");
  elm.title = "双击编辑标题";
  let t = null;
  elm.addEventListener("click", (ev) => {
    if (elm.querySelector("input")) { ev.preventDefault(); return; }
    ev.preventDefault(); ev.stopPropagation();
    if (t) { clearTimeout(t); t = null; inlineEditEnter(elm, opts); }
    else { t = setTimeout(() => { t = null; route(`#/${opts.type}/${opts.id}`); }, 200); }
  });
}

// 详情标题（非链接）：直接双击编辑
function makeInlineEditableDetail(elm, opts) {
  elm.classList.add("inline-editable");
  elm.title = "双击编辑标题";
  elm.addEventListener("dblclick", () => inlineEditEnter(elm, opts));
}

// 为列表视图内所有 entity-item 标题挂载行内编辑（按锚点 href 推断 type/id）
function attachInlineEditList(app) {
  app.querySelectorAll("a.entity-item").forEach(a => {
    const m = (a.getAttribute("href") || "").match(/#\/(epic|story|task)\/(\d+)/);
    if (!m) return;
    const span = a.querySelector(".entity-item-title");
    if (span) makeInlineEditable(span, { type: m[1], id: +m[2] });
  });
}

// A-19 列表项 hover 操作：hover 显示「编辑/删除」快捷图标，减少误触确认。复用行内编辑与既有 DELETE 端点，不改 API 契约。
function entityActions(type, id) {
  return `<div class="entity-item-actions">
      <button class="ei-act" data-act="edit" data-type="${type}" data-id="${id}" title="编辑标题" aria-label="编辑">✏</button>
      <button class="ei-act ei-act-del" data-act="del" data-type="${type}" data-id="${id}" title="删除" aria-label="删除">🗑</button>
    </div>`;
}
function attachEntityActions(app) {
  const CONFIRM = { epic: "删除 Epic 及其所有子项？此操作不可撤销！", story: "删除 Story 及其任务？此操作不可撤销！", task: "删除此任务？此操作不可撤销！" };
  app.querySelectorAll(".entity-item-actions .ei-act").forEach(b => {
    b.addEventListener("click", async (ev) => {
      ev.preventDefault(); ev.stopPropagation();
      const type = b.dataset.type, id = +b.dataset.id, item = b.closest(".entity-item");
      if (b.dataset.act === "edit") {
        const span = item && item.querySelector(".entity-item-title");
        if (span) inlineEditEnter(span, { type, id });
        return;
      }
      if (!confirm(CONFIRM[type] || "确认删除？此操作不可撤销！")) return;
      try {
        await api(`/api/${API_PLURAL[type] || type + "s"}/${id}`, "DELETE");
        toast("已删除"); render();
      } catch (e) { toast("删除失败：" + e.message); }
    });
  });
}

// A-13 任务详情抽屉：点击任务列表/看板项从右侧滑出（含 description/spec/状态），不跳路由；关闭回列表。
// 复用既有的 md()/statusBadge()/statusFlow()/priorityBadge()/typeIcon() 辅助，未改 API 契约。
async function openTaskDrawer(id) {
  const drawer = $("task-drawer"), overlay = $("drawer-overlay");
  if (!drawer || !overlay) return;
  drawer.innerHTML = '<div class="drawer-loading">加载中…</div>';
  overlay.style.display = "";
  drawer.setAttribute("aria-hidden", "false");
  requestAnimationFrame(() => { overlay.classList.add("open"); drawer.classList.add("open"); });
  try {
    const t = await api(`/api/tasks/${id}`);
    drawer.innerHTML = `
      <div class="drawer-head">
        <div class="drawer-head-main">
          <h3 id="drawer-task-title">${esc(t.title)}</h3>
          <div class="header-badges">
            ${t.type === "bug" ? `<span class="badge bug">${typeIcon("bug")} Bug</span>` : `<span class="badge task-badge">${typeIcon("task")} Task</span>`}
            ${priorityBadge(t.priority)}
            ${statusBadge(t.status)}
          </div>
        </div>
        <button class="icon-btn" onclick="closeTaskDrawer()" title="关闭 (Esc)">×</button>
      </div>
      <div class="drawer-body">
        <div class="drawer-status">${statusFlow(t)}</div>
        <div class="card"><h4>Description</h4><div class="md">${md(t.description)}</div></div>
        <div class="card"><h4>Spec（OpenSpec）</h4><div class="md">${md(t.spec)}</div></div>
        <a href="#/task/${t.id}" class="drawer-link" onclick="closeTaskDrawer()">在完整页面打开 ↗</a>
      </div>`;
    drawer.querySelectorAll("#status-flow [data-next]").forEach(b => {
      b.onclick = async () => {
        try { await api(`/api/tasks/${id}/status`, "PUT", { status: b.dataset.next });
          toast("状态已更新"); openTaskDrawer(id);
        } catch (e) { toast("更新失败：" + e.message); }
      };
    });
  } catch (e) {
    drawer.innerHTML = `<div class="drawer-head"><h3>加载失败</h3><button class="icon-btn" onclick="closeTaskDrawer()">×</button></div><div class="drawer-body"><p class="muted">${esc(e.message)}</p></div>`;
  }
}
function closeTaskDrawer() {
  const drawer = $("task-drawer"), overlay = $("drawer-overlay");
  if (!drawer || !overlay) return;
  drawer.classList.remove("open"); overlay.classList.remove("open");
  drawer.setAttribute("aria-hidden", "true");
  setTimeout(() => { overlay.style.display = "none"; render(); }, 250);
}
// 为任务列表项/看板卡挂载「单击开抽屉、双击编辑标题」（200ms 计时区分，避免双击先触发抽屉）
function attachTaskDrawer(app) {
  app.querySelectorAll("a[data-task-id]").forEach(a => {
    const id = +a.dataset.taskId;
    let t = null;
    a.addEventListener("click", (ev) => {
      if (a.querySelector("input")) { ev.preventDefault(); return; }
      ev.preventDefault();
      if (t) { clearTimeout(t); t = null; return; }
      t = setTimeout(() => { t = null; openTaskDrawer(id); }, 200);
    });
    const span = a.querySelector(".entity-item-title");
    if (span) {
      span.classList.add("inline-editable"); span.title = "双击编辑标题";
      span.addEventListener("dblclick", () => {
        if (t) { clearTimeout(t); t = null; }
        if (span.querySelector("input")) return;
        inlineEditEnter(span, { type: "task", id, onSaved: (v) => { const dt = $("drawer-task-title"); if (dt) dt.textContent = v; } });
      });
    }
  });
}

// A-15 键盘快捷键：j/k 上下移动选中项、e 编辑选中项、Esc 关闭弹层（Esc 由既有监听处理）。
// 复用既有行内编辑（inlineEditEnter）与路由（route），不改 API 契约；输入框聚焦时不触发，避免与输入冲突。
function kbdItems() {
  const app = $("app");
  if (!app) return [];
  return Array.from(app.querySelectorAll(".entity-item, .project-card, .kanban-card"))
    .filter(el => el.style.display !== "none");
}
function kbdSet(i) {
  const items = kbdItems();
  if (!items.length) { kbdSel = -1; return; }
  kbdSel = Math.max(0, Math.min(items.length - 1, i));
  items.forEach((el, idx) => el.classList.toggle("kbd-selected", idx === kbdSel));
  items[kbdSel].scrollIntoView({ block: "nearest" });
}
function kbdEdit() {
  const el = kbdItems()[kbdSel];
  if (!el) return;
  const title = el.querySelector(".entity-item-title");
  if (title && title.classList.contains("inline-editable")) {
    let type, id;
    const m = (el.getAttribute("href") || "").match(/#\/(epic|story)\/(\d+)/);
    if (m) { type = m[1]; id = +m[2]; }
    else if (el.dataset.taskId) { type = "task"; id = +el.dataset.taskId; }
    if (type) { inlineEditEnter(title, { type, id }); return; }
  }
  const href = el.getAttribute("href");
  if (href) route(href); // 项目卡等无行内编辑 → 打开
}
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") return; // 弹层关闭由既有监听处理
  const tag = (document.activeElement && document.activeElement.tagName) || "";
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  if (e.key === "j") { e.preventDefault(); kbdSet(kbdSel < 0 ? 0 : kbdSel + 1); }
  else if (e.key === "k") { e.preventDefault(); kbdSet(kbdSel < 0 ? 0 : kbdSel - 1); }
  else if (e.key === "e") { e.preventDefault(); kbdEdit(); }
});

// ---------- Epic 7：登录 / 注册 / 用户态 ----------
function showAuthScreen() {
  _AUTH_VISIBLE = true;
  CURRENT_USER = null;
  updateUserInfo();
  const app = $("app");
  if (app) { app.innerHTML = authScreenHTML(); bindAuthScreen(); }
}

function authScreenHTML() {
  const submit = _AUTH_MODE === "register" ? "注册" : "登录";
  return `
  <div class="auth-wrap">
    <div class="auth-card">
      <div class="auth-brand">
        <span class="logo-mark" aria-hidden="true"><svg viewBox="0 0 32 32" width="34" height="34" xmlns="http://www.w3.org/2000/svg"><defs><linearGradient id="ag" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#6366f1"/><stop offset=".55" stop-color="#8b5cf6"/><stop offset="1" stop-color="#a855f7"/></linearGradient></defs><rect width="32" height="32" rx="8" fill="url(#ag)"/><rect x="6" y="8" width="5" height="16" rx="2" fill="#fff"/><rect x="13.5" y="8" width="5" height="11" rx="2" fill="#fff" opacity=".85"/><rect x="21" y="8" width="5" height="14" rx="2" fill="#fff" opacity=".7"/></svg></span>
        <h1>Agent<b>Board</b></h1>
        <p class="muted">登录以管理项目与 Agent 开发闭环</p>
      </div>
      <div class="auth-tabs">
        <button type="button" class="auth-tab${_AUTH_MODE === "login" ? " active" : ""}" data-mode="login">登录</button>
        <button type="button" class="auth-tab${_AUTH_MODE === "register" ? " active" : ""}" data-mode="register">注册</button>
      </div>
      <form id="auth-form" class="auth-form" autocomplete="off">
        <label>用户名<input name="username" required minlength="2" maxlength="40" autocomplete="username" placeholder="2-40 个字符" /></label>
        <label>密码<input name="password" type="password" required minlength="6" autocomplete="current-password" placeholder="至少 6 位" /></label>
        <button type="submit" class="btn-primary block" id="auth-submit">${submit}</button>
      </form>
      <p class="auth-hint muted" id="auth-hint"></p>
    </div>
  </div>`;
}

function bindAuthScreen() {
  const app = $("app");
  if (!app) return;
  app.querySelectorAll(".auth-tab").forEach((t) => t.addEventListener("click", () => {
    _AUTH_MODE = t.dataset.mode;
    app.querySelectorAll(".auth-tab").forEach((x) => x.classList.toggle("active", x === t));
    const submit = $("auth-submit");
    if (submit) submit.textContent = _AUTH_MODE === "register" ? "注册" : "登录";
    const hint = $("auth-hint");
    if (hint) hint.textContent = "";
  }));
  const form = $("auth-form");
  if (form) form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(form);
    const username = (fd.get("username") || "").toString().trim();
    const password = (fd.get("password") || "").toString();
    if (!username || !password) { toast("请输入用户名和密码", "error"); return; }
    const btn = $("auth-submit");
    if (btn) { btn.disabled = true; btn.textContent = "处理中…"; }
    try {
      const res = await api("/api/auth/" + _AUTH_MODE, "POST", { username, password });
      setToken(res.token, res.username);
      CURRENT_USER = res;
      _AUTH_VISIBLE = false;
      updateUserInfo();
      toast(_AUTH_MODE === "register" ? "注册成功，已登录" : "登录成功");
      startApp();
    } catch (err) {
      if (btn) { btn.disabled = false; btn.textContent = _AUTH_MODE === "register" ? "注册" : "登录"; }
      toast((_AUTH_MODE === "register" ? "注册失败：" : "登录失败：") + err.message, "error");
    }
  });
}

function updateUserInfo() {
  const el = $("user-info");
  if (!el) return;
  if (CURRENT_USER) {
    el.innerHTML = `<span class="user-chip"><span class="user-name">${esc(CURRENT_USER.username)}</span><button id="logout-btn" class="ghost-sm" type="button">登出</button></span>`;
    const lb = $("logout-btn");
    if (lb) lb.addEventListener("click", logout);
  } else {
    el.innerHTML = `<button id="login-btn" class="ghost-sm" type="button">登录</button>`;
    const lb = $("login-btn");
    if (lb) lb.addEventListener("click", showAuthScreen);
  }
}

function logout() {
  clearToken();
  CURRENT_USER = null;
  showAuthScreen();
  toast("已登出");
}

async function startApp() {
  _AUTH_VISIBLE = false;
  updateUserInfo();
  try { META = await api("/api/meta"); } catch (e) {}
  try { PROJECTS = await api("/api/projects"); } catch (e) {}
  render();
}

// ---------- boot ----------
window.addEventListener("hashchange", render);
const sbToggle = document.getElementById("sidebar-toggle");
if (sbToggle) sbToggle.addEventListener("click", toggleSidebar);

// 侧栏新建项目按钮
const sbNewBtn = document.getElementById("sidebar-new-project");
if (sbNewBtn) sbNewBtn.addEventListener("click", showNewProjectModal);

// A-05 全局搜索框：输入即过滤当前页列表（跨路由持久化）
const gsInput = document.getElementById("global-search");
if (gsInput) {
  GLOBAL_SEARCH = gsInput.value;
  gsInput.addEventListener("input", (e) => { GLOBAL_SEARCH = e.target.value; applySearch(); });
}

// A-10 深色模式：启动时应用已保存偏好，并绑定切换按钮
applyTheme();
const themeBtn = document.getElementById("theme-toggle");
if (themeBtn) themeBtn.addEventListener("click", toggleTheme);

// A-13 任务详情抽屉：点击遮罩或按 Esc 关闭
const drawerOverlay = document.getElementById("drawer-overlay");
if (drawerOverlay) drawerOverlay.addEventListener("click", closeTaskDrawer);
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  const modal = $("create-modal");
  if (modal) { e.preventDefault(); closeModal("create-modal"); return; }
  const d = $("task-drawer");
  if (d && d.classList.contains("open")) closeTaskDrawer();
});

(async () => {
  // Epic 7：启动时用已存 token 校验登录态；无 token 或失效则进入应用（后端开放时）或自动跳登录（后端要求鉴权）
  const token = getToken();
  if (token) {
    try { CURRENT_USER = await api("/api/auth/me"); } catch (e) { clearToken(); CURRENT_USER = null; }
  }
  startApp();
})();
