# Epic 151 / Story 326 / Task 1298 踩坑 — 首次登录 Agent 加载竞态 + Playwright sync API hang

**日期**：2026-08-20
**Story**：Epic 151 / Story 326 / Task 1298「首次登录 Agent 加载竞态」
**关联提交**：(待写)

## 背景

Epic 149 静态 Review 阻断级 3：
- `ngOnInit()` 同步调 `validateAuth() + loadAgents()`
- 首次访问无 token → loadAgents 失败（prod 401 / dev 空数据）
- `authenticate()` 成功回调**没补** loadAgents
- 结果：登录后侧栏「X 个 Agents 在线」= 0、members tab Agent 表为空

Task 1298 修复 = `authenticate()` 成功回调追加 `void this.loadAgents()`。

## 关键发现

### 1. Playwright sync API `fill` / `click` / `wait_for_selector` 偶发 hang

**症状**：headless Chromium + Angular SPA（zone.js 持续重渲染）场景下：
- `page.locator(...).count()` 第一次成功（=1），第二次同 selector hang 永远
- `page.wait_for_selector("...", timeout=30000)` 30s 后不返回
- `page.fill(...)` / `page.click(...)` 同样 hang
- 进程必须被 SIGKILL 才能终止

**根因猜测**：Playwright sync API 通过 stdin/stdout 与 Chromium 通信；Angular zone.js
触发频繁 micro-task → 通信管道积压 → Playwright 内部消息等待超时但 Sync API 不抛。

**绕开方案**：完全用 `page.evaluate("...js...")` 执行所有浏览器操作：
- 表单 fill → `nativeSetter` + `dispatchEvent('input')`
- 按钮 click → `document.querySelector(...).click()`
- 元素存在性检查 → `document.querySelectorAll('...').length`
- 元素等待 → 用 `time.sleep(0.5)` + `page.evaluate` 自旋

实测：page.evaluate 立即返回（不通过 Playwright 消息队列），不再 hang。

### 2. PowerShell 把 stderr ANSI color 当 RemoteException 抛

`ng build` 成功输出含 ANSI（`[1m[32m√[39m`），但 bash tool 包装下 PowerShell 把
stderr 当作 `NativeCommandError` 抛。修法：直接验证 dist 存在
（`frontend/dist/frontend/browser/index.html`）即可，不看 exit code。

### 3. ng serve / dev uvicorn 启动后需要等编译完成

`ng serve` 第一次编译 5-7s，JIT 模式（Vite dev server）。E2E 必须等
`Application bundle generation complete` 后再跑，否则 `goto /` 拿不到完整 SPA。

### 4. 复用 task 1297 的 dev 服务启动模式

```bash
# 1. dev API (18000)
.venv\Scripts\python.exe -m uvicorn agentboard.api:app --host 127.0.0.1 --port 18000
# 2. ng serve (4200) + proxy → 18000
cd frontend
npx.cmd ng serve --port 4200 --host 127.0.0.1 --proxy-config ..\tests\e2e_epic149\proxy.local.conf.json
```

`tests/e2e_epic149/proxy.local.conf.json` 模板（Task 1297 创建）：ng serve 4200 路径
代理到 127.0.0.1:18000（dev API），不打到生产。

### 5. `authenticate()` 修复：success callback 补 `loadAgents()`

```typescript
async authenticate(username: string, password: string): Promise<void> {
  this.submitting.set(true);
  try {
    const result = await firstValueFrom(this.api.login(username, password));
    // ... set localStorage / signals ...
    this.authVisible.set(false);
    this.notify('登录成功');
    // ★ Task 1298 修复
    void this.loadAgents();
    if (this.router.url.startsWith('/login')) {
      await this.router.navigateByUrl('/');
    } else {
      await this.loadRoute();
    }
  } catch (error) { ... }
}
```

ngOnInit 已调 loadAgents，但**首次登录**走 authenticate 路径（不走 ngOnInit），
所以必须 authenticate 内部补一次。

### 6. dev DB admin 密码需手动 reset

`tests/factories/` 没造 admin/admin123 的本地 fixture；dev DB 实际 password hash
不是 admin123。修法：跑 E2E 前直接 reset：

```python
from agentboard.core.infrastructure.auth import hash_password
admin.password_hash = hash_password('admin123')
s.commit()
```

`scripts/_login.json` 是临时 fixture（`{"username":"admin","password":"admin123"}`），
不入仓（`.gitignore` 排除 `scripts/_login.json`）。

## 验证

- **后端 pytest**：`tests/test_agent_public_dict.py` 4/4 PASS（Task 1297 回归）
- **前端 vitest**：3 files / 69 passed / 1 skipped（无 regression）
- **E2E Playwright**：`tests/e2e_epic149/test_x_a1_first_login_agent_load.py` PASS
  - 不复用 token-injection 模式
  - 清空 localStorage → page.goto / → 看到 login modal
  - 填表 + 提交 → home shell 出现
  - 切到 Agents tab → 看到 **3 个 agent rows**（dev DB seed + 2 历史 fixture）
  - 证明 `authenticate() → loadAgents()` 路径生效

## 改进要点（Future Work）

- 把 Playwright sync API hang 模式写到 `tests/e2e_epic149/HARNESS.md` 供未来 E2E 参考
- 抽 `safe_fill()` / `safe_click()` helper 统一用 page.evaluate 模式
- 写一个 fixture 自旋 script 验证 dev services 编译完成（`scripts/wait_for_ng_serve.py`）
