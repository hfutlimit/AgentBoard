// AgentBoard SPA —— 纯前端，通过 fetch 调用 REST API（前后端分离）。
const API = window.AGENTBOARD_API || "http://127.0.0.1:8000";
let META = { types: ["task", "bug"], statuses: ["backlog", "todo", "in_progress", "in_review", "verifying", "done"] };
let PROJECTS = []; // 缓存项目列表供侧栏使用

// ---------- HTTP ----------
async function api(path, method = "GET", body) {
  const opt = { method, headers: {} };
  const token = localStorage.getItem("agentboard_token");
  if (token) opt.headers["Authorization"] = "Bearer " + token;
  if (body !== undefined) { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
  const r = await fetch(API + path, opt);
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
function toast(msg) { const t = $("toast"); t.textContent = msg; t.classList.add("show"); setTimeout(() => t.classList.remove("show"), 2500); }
function route(hash) { location.hash = hash; }

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
      : `<span class="crumb-current">${esc(it.label)}</span>`
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
function statusBadge(s) {
  const label = STATUS_LABEL[s] || s;
  const color = STATUS_COLOR[s] || "#6b7280";
  return `<span class="badge status" style="background:${color}15;color:${color};border:1px solid ${color}30">${label}</span>`;
}
function statusDot(s) {
  const color = STATUS_COLOR[s] || "#6b7280";
  return `<span class="status-dot" style="background:${color}" title="${STATUS_LABEL[s]||s}"></span>`;
}

// ---------- 侧栏 ----------
let sidebarOpen = true;

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
              <span class="sidebar-icon">📁</span>
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

// ---------- Router ----------
async function render() {
  const h = location.hash || "#/";
  const app = $("app");
  app.innerHTML = '<div class="loading"><span></span><span></span><span></span></div>';
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
        }
      }
      totalStories += pStories; totalTasks += pTasks; doneTasks += pDone;
      projectStats.push({ ...p, epics: eps.length, stories: pStories, tasks: pTasks, done: pDone });
    } catch (e) {
      projectStats.push({ ...p, epics: "?", stories: "?", tasks: "?", done: "?" });
    }
  }

  app.innerHTML = `
    <div class="dashboard">
      <!-- 统计卡片 -->
      <div class="stats-row">
        <div class="stat-card">
          <div class="stat-number">${ps.length}</div>
          <div class="stat-label">项目</div>
        </div>
        <div class="stat-card">
          <div class="stat-number">${totalEpics}</div>
          <div class="stat-label">Epic</div>
        </div>
        <div class="stat-card">
          <div class="stat-number">${totalStories}</div>
          <div class="stat-label">Story</div>
        </div>
        <div class="stat-card">
          <div class="stat-number">${totalTasks}</div>
          <div class="stat-label">任务</div>
        </div>
        <div class="stat-card highlight">
          <div class="stat-number">${totalTasks ? Math.round(doneTasks/totalTasks*100) : 0}%</div>
          <div class="stat-label">完成率</div>
        </div>
      </div>

      <!-- 项目列表 -->
      <div class="section-header">
        <h2>📂 项目总览</h2>
        <button id="home-new-project" class="btn-primary-sm">＋ 新建项目</button>
      </div>
      ${ps.length ? `
        <div class="project-grid">
          ${projectStats.map(p => `
            <a href="#/project/${p.id}" class="project-card">
              <div class="project-card-header">
                <span class="project-card-name">${esc(p.name)}</span>
                ${p.key ? `<span class="badge">${esc(p.key)}</span>` : ""}
              </div>
              ${p.description ? `<div class="project-card-desc">${esc(p.description.substring(0, 100))}${p.description.length > 100 ? "…" : ""}</div>` : ""}
              <div class="project-card-stats">
                <span>${p.epics} Epic</span>
                <span>${p.stories} Story</span>
                <span>${p.tasks} 任务</span>
                ${typeof p.done === "number" ? `<span class="text-success">${p.done} 完成</span>` : ""}
              </div>
            </a>
          `).join("")}
        </div>
      ` : `
        <div class="empty-state">
          <div class="empty-icon">📋</div>
          <h3>还没有项目</h3>
          <p class="muted">创建你的第一个项目管理空间</p>
          <button onclick="document.getElementById('home-new-project').click()" class="btn-primary">创建项目</button>
        </div>
      `}

      <!-- 快捷操作 -->
      <div class="section-header" style="margin-top:32px">
        <h2>⚡ 快捷操作</h2>
      </div>
      <div class="quick-actions">
        <button onclick="route('#/projects')" class="action-card">📋 所有项目</button>
        <button onclick="showNewProjectModal()" class="action-card">➕ 新建项目</button>
      </div>
    </div>`;

  // 绑定新建项目
  bindNewProjectForm("home-new-project-form", "home-new-project-modal");
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
      <div class="empty-state">
        <div class="empty-icon">📋</div>
        <h3>暂无项目</h3>
        <button class="btn-primary" onclick="document.getElementById('proj-new-btn').click()">创建第一个项目</button>
      </div>
    `}`;

  bindNewProjectForm("proj-new-form", "proj-new-modal");
  const btn = $("proj-new-btn");
  if (btn) btn.onclick = () => showNewProjectModal();
}

// ----- 项目详情 -----
async function viewProject(app, id) {
  const p = await api(`/api/projects/${id}`);
  const eps = await api(`/api/projects/${id}/epics`);
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
      <h3>📋 Epics <span class="count">${eps.length}</span></h3>
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
          </a>
        `).join("")}
      </div>
    ` : '<div class="empty-inline">暂无 Epic，点击上方按钮创建</div>'}

    <!-- 新建 Epic 表单（内联） -->
    <div class="card form-section" id="p-epic-form" style="display:none">
      <h4>新建 Epic</h4>
      <form id="p-ef">
        <input name="title" placeholder="Epic 标题" required>
        <textarea name="description" rows="3" placeholder="描述 (markdown)"></textarea>
        <div class="row"><button type="submit" class="btn-primary-sm">创建</button><button type="button" class="ghost-sm" onclick="$('#/p-epic-form').style.display='none'">取消</button></div>
      </form>
    </div>

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
  if (newEpicBtn) newEpicBtn.onclick = () => {
    const f = $("p-epic-form"); f.style.display = f.style.display === "none" ? "" : "none";
    $("p-edit-area").style.display = "none";
  };

  const editToggle = $("p-edit-toggle");
  if (editToggle) editToggle.onclick = () => {
    const f = $("p-edit-area"); f.style.display = f.style.display === "none" ? "" : "none";
    $("p-epic-form").style.display = "none";
  };

  bindForm("p-ef", async (d) => {
    await api(`/api/projects/${id}/epics`, "POST", { title: d.title, description: d.description });
    toast("Epic 已创建"); render();
  });
  bindForm("p-edit-form", async (d) => {
    await api(`/api/projects/${id}`, "PATCH", { name: d.name, key: d.key || null, description: d.description });
    toast("已保存"); render();
  });
  const delBtn = $("p-del-btn");
  if (delBtn) delBtn.onclick = async () => {
    if (!confirm("删除项目及其所有子项？此操作不可撤销！")) return;
    await api(`/api/projects/${id}`, "DELETE"); toast("已删除"); route("#/");
  };
}

// ----- Epic 详情 -----
async function viewEpic(app, id) {
  const ep = await api(`/api/epics/${id}`);
  const sts = await api(`/api/epics/${id}/stories`);
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
      <h3>📝 Stories <span class="count">${sts.length}</span></h3>
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
          </a>
        `).join("")}
      </div>
    ` : '<div class="empty-inline">暂无 Story</div>'}

    <div class="card form-section" id="e-story-form" style="display:none">
      <h4>新建 Story</h4>
      <form id="e-sf">
        <input name="title" placeholder="Story 标题" required>
        <textarea name="description" rows="3" placeholder="描述 (markdown)"></textarea>
        <div class="row"><button type="submit" class="btn-primary-sm">创建</button><button type="button" class="ghost-sm" onclick="$('#/e-story-form').style.display='none'">取消</button></div>
      </form>
    </div>

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
  if (newBtn) newBtn.onclick = () => {
    const f = $("e-story-form"); f.style.display = f.style.display === "none" ? "" : "none";
  };
  bindForm("e-sf", async (d) => {
    await api(`/api/epics/${id}/stories`, "POST", { title: d.title, description: d.description });
    toast("Story 已创建"); render();
  });
  bindForm("e-edit", async (d) => {
    await api(`/api/epics/${id}`, "PATCH", { title: d.title, status: d.status, description: d.description });
    toast("已保存"); render();
  });
  const eDel = $("e-del");
  if (eDel) eDel.onclick = async () => {
    if (!confirm("删除 Epic 及其子项？")) return;
    await api(`/api/epics/${id}`, "DELETE"); toast("已删除"); route(`#/project/${ep.project_id}`);
  };
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
      <h3>🔧 Tasks / Bugs <span class="count">${tasks.length}</span></h3>
      <button id="s-new-task" class="btn-primary-sm">＋ 新建</button>
    </div>
    ${tasks.length ? `
      <div class="entity-list">
        ${tasks.map(t => `
          <a href="#/task/${t.id}" class="entity-item">
            <div class="entity-item-main">
              <span class="type-icon ${t.type}">${t.type === "bug" ? "🐛" : "✅"}</span>
              <span class="entity-item-title">${esc(t.title)}</span>
            </div>
            <div class="entity-item-badges">
              ${t.type === "bug" ? `<span class="badge bug">Bug</span>` : ""}
              ${statusBadge(t.status)}
            </div>
          </a>
        `).join("")}
      </div>
    ` : '<div class="empty-inline">暂无任务</div>'}

    <div class="card form-section" id="s-task-form" style="display:none">
      <h4>新建 Task / Bug</h4>
      <form id="s-tf">
        <input name="title" placeholder="标题" required>
        <label>类型</label>${typeSelect("task")}
        <textarea name="description" rows="3" placeholder="描述 (markdown)"></textarea>
        <div class="row"><button type="submit" class="btn-primary-sm">创建</button><button type="button" class="ghost-sm" onclick="$('#/s-task-form').style.display='none'">取消</button></div>
      </form>
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
  if (newBtn) newBtn.onclick = () => {
    const f = $("s-task-form"); f.style.display = f.style.display === "none" ? "" : "none";
  };
  bindForm("s-tf", async (d) => {
    await api(`/api/stories/${id}/tasks`, "POST",
      { project_id: ep.project_id, title: d.title, type: d.type, description: d.description });
    toast("任务已创建"); render();
  });
  bindForm("s-edit", async (d) => {
    await api(`/api/stories/${id}`, "PATCH", { title: d.title, status: d.status, description: d.description });
    toast("已保存"); render();
  });
  const sDel = $("s-del");
  if (sDel) sDel.onclick = async () => {
    if (!confirm("删除 Story 及其任务？")) return;
    await api(`/api/stories/${id}`, "DELETE"); toast("已删除"); route(`#/epic/${st.epic_id}`);
  };
}

// ----- Task 详情 -----
async function viewTask(app, id) {
  const t = await api(`/api/tasks/${id}`);
  crumbs([
    { label: "仪表盘", href: "#/" },
    { label: "Story", href: `#/story/${t.story_id}` },
    { label: t.title }
  ]);

  app.innerHTML = `
    <div class="page-header">
      <div class="page-title-row">
        <h2>${esc(t.title)}</h2>
        <div class="header-badges">
          ${t.type === "bug" ? `<span class="badge bug">🐛 Bug</span>` : `<span class="badge task-badge">✅ Task</span>`}
          <span class="badge status" id="stbadge">${(STATUS_LABEL[t.status]||t.status)}</span>
        </div>
      </div>
      <div class="page-actions">
        <select id="stsel">${META.statuses.map(s => `<option value="${s}"${s===t.status?" selected":""}>${STATUS_LABEL[s]||s}</option>`).join("")}</select>
        <button id="stbtn" class="btn-primary-sm">更新状态</button>
      </div>
    </div>

    <div class="two-col">
      <div class="card"><h3>📄 Description</h3><div class="md">${md(t.description)}</div></div>
      <div class="card"><h3>📋 Spec（OpenSpec）</h3><div class="md">${md(t.spec)}</div></div>
    </div>

    <div class="card form-section">
      <h3>✏ 编辑</h3>
      <form id="t-edit">
        <label>标题</label><input name="title" value="${esc(t.title)}" required>
        <label>类型</label>${typeSelect(t.type)}
        <label>Description (markdown)</label><textarea name="description" rows="5">${esc(t.description)}</textarea>
        <label>Spec (markdown)</label><textarea name="spec" rows="12">${esc(t.spec)}</textarea>
        <div class="row" style="flex-wrap:wrap">
          <button type="submit" class="btn-primary-sm">💾 保存</button>
          <button type="button" class="ghost-sm" id="tpl">📝 插入提案模板</button>
          <button type="button" class="ghost-sm" id="gen">⚡ 生成子任务</button>
          <button type="button" class="danger" id="del">🗑 删除</button>
        </div>
      </form>
    </div>`;

  $("stbtn").onclick = async () => {
    try {
      await api(`/api/tasks/${id}/status`, "PUT", { status: $("stsel").value });
      toast("状态已更新"); render();
    } catch (e) { toast("更新失败：" + e.message); }
  };
  bindForm("t-edit", async (d) => {
    await api(`/api/tasks/${id}`, "PATCH", { title: d.title, type: d.type, description: d.description, spec: d.spec });
    toast("已保存"); render();
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
}

// ========== 公共组件 ==========

// 新建项目弹窗
function showNewProjectModal() {
  // 如果已有 modal 就复用，否则动态插入
  let modal = document.getElementById("new-project-modal");
  if (!modal) {
    modal = document.createElement("div");
    modal.id = "new-project-modal";
    modal.className = "modal-overlay";
    modal.innerHTML = `
      <div class="modal">
        <div class="modal-header"><h3>➕ 新建项目</h3><button class="modal-close" onclick="closeModal('new-project-modal')">×</button></div>
        <form id="modal-project-form">
          <label>名称 <span class="required">*</span></label>
          <input name="name" placeholder="如：DevPilot AI 平台" required autofocus>
          <label>短码（可选）</label>
          <input name="key" placeholder="如：DEV（用于标识和引用）">
          <label>描述 (markdown)</label>
          <textarea name="description" rows="4" placeholder="项目简介、目标等…"></textarea>
          <div class="row" style="justify-content:flex-end;margin-top:12px">
            <button type="button" class="ghost-sm" onclick="closeModal('new-project-modal')">取消</button>
            <button type="submit" class="btn-primary">创建项目</button>
          </div>
        </form>
      </div>`;
    document.body.appendChild(modal);
  } else {
    modal.style.display = "";
  }

  bindForm("modal-project-form", async (d) => {
    await api("/api/projects", "POST", { name: d.name, key: d.key || null, description: d.description });
    toast("✅ 项目已创建"); closeModal("new-project-modal"); render();
  });

  // 聚焦到名称输入框
  setTimeout(() => { const inp = modal.querySelector('input[name="name"]'); if (inp) inp.focus(); }, 50);
}

function closeModal(id) {
  const el = document.getElementById(id);
  if (el) el.style.display = "none";
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

function bindNewProjectForm(formId, modalId) {
  // 预留：如果页面有独立的表单可绑定到这里
}

function statusSelect(cur, id) {
  return `<select name="status"${id ? ` id="${id}"` : ""}>` +
    META.statuses.map(s =>
      `<option value="${s}"${s === cur ? " selected" : ""}>${STATUS_LABEL[s] || s}</option>`
    ).join("") + "</select>";
}
function typeSelect(cur) {
  return `<select name="type">` +
    META.types.map(t => `<option value="${t}"${t === cur ? " selected" : ""}>${t === "task" ? "Task 📝" : "Bug 🐛"}</option>`).join("") +
    "</select>";
}

// ---------- boot ----------
window.addEventListener("hashchange", render);
const sbToggle = document.getElementById("sidebar-toggle");
if (sbToggle) sbToggle.addEventListener("click", toggleSidebar);

// 侧栏新建项目按钮
const sbNewBtn = document.getElementById("sidebar-new-project");
if (sbNewBtn) sbNewBtn.addEventListener("click", showNewProjectModal);

(async () => {
  try { META = await api("/api/meta"); } catch (e) {}
  // 加载侧栏数据
  try { PROJECTS = await api("/api/projects"); } catch (e) {}
  render();
})();
