# Design — 前端注册 / 登录集成

## Token 生命周期
- 存储：`localStorage["agentboard_token"]`（无状态 Bearer，形如 `userId.hmac`）。
- 登录 / 注册成功：服务端返回 `{id, username, token}`，前端 `setToken(token)` 后 `render()`。
- 登出：`clearToken()` 并回到登录界面。

## `api()` 改造（`app.js`）
```js
function getToken(){ return localStorage.getItem("agentboard_token") || ""; }
function setToken(t){ localStorage.setItem("agentboard_token", t); }
function clearToken(){ localStorage.removeItem("agentboard_token"); }

async function api(path, method="GET", body){
  const opt = { method, headers: {} };
  const tk = getToken();
  if (tk) opt.headers["Authorization"] = "Bearer " + tk;
  if (body !== undefined){ opt.headers["Content-Type"]="application/json"; opt.body=JSON.stringify(body); }
  const r = await fetch(API + path, opt);
  if (r.status === 401){ clearToken(); /* 回登录 */ }
  ...
}
```

## 启动守卫（`render` 之前）
1. 读取 token；若无 → 渲染登录 / 注册界面。
2. 若有 → `GET /api/auth/me`，成功则进入应用，失败（伪造/过期）→ 清 token 回登录。

## 登录 / 注册界面（`renderAuth`）
- 单卡片：用户名 + 密码；"登录 / 注册"切换按钮。
- 注册：`POST /api/auth/register`；登录：`POST /api/auth/login`。
- 错误展示（重复注册 409、错误密码 401）：在卡片内显示 `msg`。

## 顶部栏
- 登录后显示当前用户名（来自 `/api/auth/me`）+「登出」按钮。
- 未登录时隐藏应用内容与登出按钮。

## 可选：后端强制鉴权（`api.py`）
- 新增环境变量 `AGENTBOARD_REQUIRE_AUTH=1`：开启时 CRUD 端点经现有 `_current_user` 校验（401 拒绝）；默认关闭，保持单用户开放。
- 该开关与 MCP 透传 token（见变更 `mcp-auth`）配合使用。

## 样式（`style.css`）
- 登录卡片居中、表单纵向布局；顶部用户信息条（用户名 + 登出）。
- 复用既有 `.card / .badge / .muted` 设计语言。
