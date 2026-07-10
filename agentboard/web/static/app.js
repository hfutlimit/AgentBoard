// AgentBoard SPA —— 纯前端，通过 fetch 调用 REST API（前后端分离）。
const API = window.AGENTBOARD_API || "http://127.0.0.1:8000";
let META = { types: ["task", "bug"], statuses: ["backlog", "todo", "in_progress", "in_review", "verifying", "done"] };

// ---------- HTTP ----------
async function api(path, method = "GET", body) {
  const opt = { method, headers: {} };
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
function toast(msg) { const t = $("toast"); t.textContent = msg; t.classList.add("show"); setTimeout(() => t.classList.remove("show"), 2000); }
function route(hash) { location.hash = hash; }

// minimal markdown renderer (headers, code, bold, lists, line breaks)
function md(src) {
  if (!src) return '<span class="muted">（空）</span>';
  let h = esc(src);
  h = h.replace(/```([\s\S]*?)```/g, (m, c) => `<pre>${c.replace(/^\n/, "")}</pre>`);
  h = h.replace(/`([^`]+)`/g, "<code>$1</code>");
  h = h.replace(/^###### (.*)$/gm, "<h6>$1</h6>").replace(/^##### (.*)$/gm, "<h5>$1</h5>")
       .replace(/^#### (.*)$/gm, "<h4>$1</h4>").replace(/^### (.*)$/gm, "<h3>$1</h3>")
       .replace(/^## (.*)$/gm, "<h2>$1</h2>").replace(/^# (.*)$/gm, "<h1>$1</h1>");
  h = h.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  h = h.replace(/^\s*[-*] (.*)$/gm, "<li>$1</li>");
  h = h.replace(/(<li>[\s\S]*?<\/li>)/g, m => "<ul>" + m + "</ul>");
  h = h.replace(/\n{2,}/g, "<br><br>").replace(/\n/g, "<br>");
  return h;
}

function crumbs(items) {
  $("crumbs").innerHTML = items.map((it, i) =>
    i < items.length - 1 ? `<a href="${it.href}">${esc(it.label)}</a> / ` : `<span>${esc(it.label)}</span>`
  ).join("");
}

// ---------- Router ----------
async function render() {
  const h = location.hash || "#/";
  const app = $("app");
  app.innerHTML = "加载中…";
  try {
    const m = h.match(/^#\/(project|epic|story|task)\/(\d+)$/);
    if (h === "#/") await viewHome(app);
    else if (m && m[1] === "project") await viewProject(app, +m[2]);
    else if (m && m[1] === "epic") await viewEpic(app, +m[2]);
    else if (m && m[1] === "story") await viewStory(app, +m[2]);
    else if (m && m[1] === "task") await viewTask(app, +m[2]);
    else app.innerHTML = "页面不存在";
  } catch (e) {
    app.innerHTML = `<div class="card">加载失败：${esc(e.message)}<br><span class="muted">请确认 API 已启动：${API}</span></div>`;
  }
}

// ---------- Views ----------
async function viewHome(app) {
  crumbs([{ label: "项目" }]);
  const ps = await api("/api/projects");
  app.innerHTML = `
    <h2>项目</h2>
    <ul class="tree">${ps.map(p => `
      <li><a href="#/project/${p.id}">${esc(p.name)}</a>
        ${p.key ? `<span class="badge">${esc(p.key)}</span>` : ""}</li>`).join("") || '<li class="muted">暂无项目</li>'}
    </ul>
    <div class="card"><h2>新建项目</h2>
      <form id="f">
        <input name="name" placeholder="名称" required>
        <input name="key" placeholder="短码 (可选)">
        <textarea name="description" rows="3" placeholder="描述 (markdown)"></textarea>
        <button>创建项目</button>
      </form>
    </div>`;
  $("f").onsubmit = async (e) => {
    e.preventDefault();
    const d = Object.fromEntries(new FormData(e.target));
    await api("/api/projects", "POST", { name: d.name, key: d.key || null, description: d.description });
    toast("项目已创建"); render();
  };
}

async function viewProject(app, id) {
  const p = await api(`/api/projects/${id}`);
  const eps = await api(`/api/projects/${id}/epics`);
  crumbs([{ label: "项目", href: "#/" }, { label: p.name }]);
  app.innerHTML = `
    <div class="card">
      <div class="row"><h2 style="margin:0;flex:1">${esc(p.name)}</h2>
        ${p.key ? `<span class="badge">${esc(p.key)}</span>` : ""}</div>
      <div class="md">${md(p.description)}</div>
      <details><summary>编辑项目</summary>
        <form id="fe">
          <label>名称</label><input name="name" value="${esc(p.name)}" required>
          <label>短码</label><input name="key" value="${esc(p.key || "")}">
          <label>描述 (markdown)</label><textarea name="description" rows="4">${esc(p.description)}</textarea>
          <div class="row"><button>保存</button>
            <button type="button" class="danger" id="del">删除项目</button></div>
        </form>
      </details>
    </div>
    <h2>Epics</h2>
    <ul class="tree">${eps.map(e => `
      <li><a href="#/epic/${e.id}">${esc(e.title)}</a>
        <span class="badge status">${esc(e.status)}</span></li>`).join("") || '<li class="muted">暂无 Epic</li>'}
    </ul>
    <div class="card"><h2>新建 Epic</h2>
      <form id="fc">
        <input name="title" placeholder="标题" required>
        <textarea name="description" rows="3" placeholder="描述 (markdown)"></textarea>
        <button>创建 Epic</button>
      </form>
    </div>`;
  $("fe").onsubmit = async (e) => {
    e.preventDefault(); const d = Object.fromEntries(new FormData(e.target));
    await api(`/api/projects/${id}`, "PATCH", { name: d.name, key: d.key || null, description: d.description });
    toast("已保存"); render();
  };
  $("del").onclick = async () => {
    if (!confirm("删除项目及其所有子项？")) return;
    await api(`/api/projects/${id}`, "DELETE"); toast("已删除"); route("#/");
  };
  $("fc").onsubmit = async (e) => {
    e.preventDefault(); const d = Object.fromEntries(new FormData(e.target));
    await api(`/api/projects/${id}/epics`, "POST", { title: d.title, description: d.description });
    toast("Epic 已创建"); render();
  };
}

async function viewEpic(app, id) {
  const ep = await api(`/api/epics/${id}`);
  const sts = await api(`/api/epics/${id}/stories`);
  crumbs([{ label: "项目", href: "#/" }, { label: "项目", href: `#/project/${ep.project_id}` }, { label: ep.title }]);
  app.innerHTML = `
    <div class="card">
      <div class="row"><h2 style="margin:0;flex:1">${esc(ep.title)}</h2>
        <span class="badge status">${esc(ep.status)}</span></div>
      <div class="md">${md(ep.description)}</div>
      <details><summary>编辑 Epic</summary>
        <form id="fe">
          <label>标题</label><input name="title" value="${esc(ep.title)}" required>
          <label>状态</label>${statusSelect(ep.status)}
          <label>描述 (markdown)</label><textarea name="description" rows="4">${esc(ep.description)}</textarea>
          <div class="row"><button>保存</button>
            <button type="button" class="danger" id="del">删除 Epic</button></div>
        </form>
      </details>
    </div>
    <h2>Stories</h2>
    <ul class="tree">${sts.map(s => `
      <li><a href="#/story/${s.id}">${esc(s.title)}</a>
        <span class="badge status">${esc(s.status)}</span></li>`).join("") || '<li class="muted">暂无 Story</li>'}
    </ul>
    <div class="card"><h2>新建 Story</h2>
      <form id="fc">
        <input name="title" placeholder="标题" required>
        <textarea name="description" rows="3" placeholder="描述 (markdown)"></textarea>
        <button>创建 Story</button>
      </form>
    </div>`;
  $("fe").onsubmit = async (e) => {
    e.preventDefault(); const d = Object.fromEntries(new FormData(e.target));
    await api(`/api/epics/${id}`, "PATCH", { title: d.title, status: d.status, description: d.description });
    toast("已保存"); render();
  };
  $("del").onclick = async () => {
    if (!confirm("删除 Epic 及其子项？")) return;
    await api(`/api/epics/${id}`, "DELETE"); toast("已删除"); route(`#/project/${ep.project_id}`);
  };
  $("fc").onsubmit = async (e) => {
    e.preventDefault(); const d = Object.fromEntries(new FormData(e.target));
    await api(`/api/epics/${id}/stories`, "POST", { title: d.title, description: d.description });
    toast("Story 已创建"); render();
  };
}

async function viewStory(app, id) {
  const st = await api(`/api/stories/${id}`);
  const ep = await api(`/api/epics/${st.epic_id}`);
  const tasks = await api(`/api/stories/${id}/tasks`);
  crumbs([{ label: "项目", href: "#/" }, { label: "项目", href: `#/project/${ep.project_id}` },
          { label: "Epic", href: `#/epic/${st.epic_id}` }, { label: st.title }]);
  app.innerHTML = `
    <div class="card">
      <div class="row"><h2 style="margin:0;flex:1">${esc(st.title)}</h2>
        <span class="badge status">${esc(st.status)}</span></div>
      <div class="md">${md(st.description)}</div>
      <details><summary>编辑 Story</summary>
        <form id="fe">
          <label>标题</label><input name="title" value="${esc(st.title)}" required>
          <label>状态</label>${statusSelect(st.status)}
          <label>描述 (markdown)</label><textarea name="description" rows="4">${esc(st.description)}</textarea>
          <div class="row"><button>保存</button>
            <button type="button" class="danger" id="del">删除 Story</button></div>
        </form>
      </details>
    </div>
    <h2>Tasks / Bugs</h2>
    <ul class="tree">${tasks.map(t => `
      <li><a href="#/task/${t.id}">${esc(t.title)}</a>
        <span class="badge ${t.type === "bug" ? "bug" : ""}">${esc(t.type)}</span>
        <span class="badge status">${esc(t.status)}</span></li>`).join("") || '<li class="muted">暂无任务</li>'}
    </ul>
    <div class="card"><h2>新建 Task / Bug</h2>
      <form id="fc">
        <input name="title" placeholder="标题" required>
        <label>类型</label>${typeSelect("task")}
        <textarea name="description" rows="3" placeholder="描述 (markdown)"></textarea>
        <button>创建</button>
      </form>
    </div>`;
  $("fe").onsubmit = async (e) => {
    e.preventDefault(); const d = Object.fromEntries(new FormData(e.target));
    await api(`/api/stories/${id}`, "PATCH", { title: d.title, status: d.status, description: d.description });
    toast("已保存"); render();
  };
  $("del").onclick = async () => {
    if (!confirm("删除 Story 及其任务？")) return;
    await api(`/api/stories/${id}`, "DELETE"); toast("已删除"); route(`#/epic/${st.epic_id}`);
  };
  $("fc").onsubmit = async (e) => {
    e.preventDefault(); const d = Object.fromEntries(new FormData(e.target));
    await api(`/api/stories/${id}/tasks`, "POST",
      { project_id: ep.project_id, title: d.title, type: d.type, description: d.description });
    toast("任务已创建"); render();
  };
}

async function viewTask(app, id) {
  const t = await api(`/api/tasks/${id}`);
  crumbs([{ label: "项目", href: "#/" },
          { label: "Story", href: `#/story/${t.story_id}` }, { label: t.title }]);
  app.innerHTML = `
    <div class="card">
      <div class="row"><h2 style="margin:0;flex:1">${esc(t.title)}</h2>
        <span class="badge ${t.type === "bug" ? "bug" : ""}">${esc(t.type)}</span>
        <span class="badge status" id="stbadge">${esc(t.status)}</span></div>
      <div class="row"><label>状态流转</label>${statusSelect(t.status, "stsel")}
        <button type="button" id="stbtn" class="ghost">更新状态</button></div>
    </div>
    <div class="two-col">
      <div class="card"><h2>Description</h2><div class="md">${md(t.description)}</div></div>
      <div class="card"><h2>Spec (OpenSpec / Superpowers)</h2><div class="md">${md(t.spec)}</div></div>
    </div>
    <div class="card"><h2>编辑</h2>
      <form id="fe">
        <label>标题</label><input name="title" value="${esc(t.title)}" required>
        <label>类型</label>${typeSelect(t.type)}
        <label>Description (markdown)</label><textarea name="description" rows="5">${esc(t.description)}</textarea>
        <label>Spec (markdown)</label><textarea name="spec" rows="12">${esc(t.spec)}</textarea>
          <div class="row"><button>保存</button>
            <button type="button" class="ghost" id="tpl">插入 OpenSpec 提案模板</button>
            <button type="button" class="ghost" id="gen">从 spec 生成子任务</button>
            <button type="button" class="danger" id="del">删除任务</button></div>
      </form>
    </div>`;
  $("stbtn").onclick = async () => {
    try {
      await api(`/api/tasks/${id}/status`, "PUT", { status: $("stsel").value });
      toast("状态已更新"); render();
    } catch (e) { toast("状态更新失败：" + e.message); }
  };
  $("fe").onsubmit = async (e) => {
    e.preventDefault(); const d = Object.fromEntries(new FormData(e.target));
    await api(`/api/tasks/${id}`, "PATCH", { title: d.title, type: d.type, description: d.description, spec: d.spec });
    toast("已保存"); render();
  };
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
      toast(`已生成 ${created.length} 个子任务`);
      render();
    } catch (e) { toast("生成失败：" + e.message); }
  };
}

function statusSelect(cur, id) {
  return `<select name="status"${id ? ` id="${id}"` : ""}>` +
    META.statuses.map(s => `<option value="${s}"${s === cur ? " selected" : ""}>${s}</option>`).join("") + "</select>";
}
function typeSelect(cur) {
  return `<select name="type">` +
    META.types.map(t => `<option value="${t}"${t === cur ? " selected" : ""}>${t}</option>`).join("") + "</select>";
}

// ---------- boot ----------
window.addEventListener("hashchange", render);
(async () => {
  try { META = await api("/api/meta"); } catch (e) {}
  render();
})();
