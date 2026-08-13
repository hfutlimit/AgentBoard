# Design — Codex + MiniMax 端到端验证

## 整体定位

```
                          ┌──────────────────────────────────────┐
                          │  Executor 主循环（Story 104 daemon）  │
                          │  pending run → 选 Adapter → launch   │
                          └────────────────┬─────────────────────┘
                                           │
                ┌──────────────────────────┼───────────────────────────┐
                │                          │                           │
        ┌───────▼─────────┐      ┌─────────▼──────────┐      ┌────────▼─────────┐
        │ CodexLauncher   │      │ ClaudeLauncher     │      │ MiniMaxLauncher  │ ★本 Change
        │ codex exec --json      │ claude -p          │      │ python minimax_  │
        │ (Story 102)     │      │ (Story 102)        │      │ invoker.py       │
        └─────────────────┘      └────────────────────┘      └────────┬─────────┘
                                                                     │
                                                              直打 MiniMax API
                                                              (绕过 minimax-cli)
```

## 关键设计决策

### 1. Codex E2E fake CLI 协议对齐

`codex exec --json` 真实输出近似（OpenAI Codex CLI 0.x）：
- **stderr/stdout 混合**：进度日志行（`{"type":"progress","msg":"..."}`）散落；
- **最后一行为决策 JSON**（`{"type":"result","action":"ask","questions":[...]}`）；
- 退出码 0 = 成功（即便决策是 ask/finalize/fail）；非 0 = 子进程错误。

fake codex CLI 设计（`tests/_fake_codex.py`）：

```python
# 1. 读 stdin prompt（验证长度 > 0，校验含 "run #N"）
# 2. 写进度行到 stderr（模拟真实 codex 的 progress chatter）
# 3. 写决策 JSON 到 stdout
# 4. 按 FAKE_EXIT_CODE 退出（默认 0）
```

测试断言：
- `run.output` 含决策 JSON（被 CliLauncher 当 raw 字符串透传，由调用方解析）；
- 进度行被 `stderr=STDOUT` 合并后**也**进入 `output`（不退化）。

### 2. MiniMaxLauncher 实现策略

不新增抽象基类（HTTP API 路径），直接复用 `CliLauncher`：

```python
@adapter("minimax")
class MiniMaxLauncher(CliLauncher):
    name = "minimax"
    description = "MiniMax 直打 chat API（minimax_invoker.py 桥接）"
    command = [sys.executable, str(Path(__file__).parent.parent / "scripts" / "minimax_invoker.py")]
    env_var = "AGENTBOARD_MINIMAX_BIN"
    timeout_seconds = 600.0  # API 调用 5min 上限，比 CLI Agent 短
```

**为什么不写一个 `HttpApiLauncher`？** 三个理由：
- 现有 `minimax_invoker.py` 已经把 HTTP 细节封装好了，**复用 > 重构**；
- `CliLauncher.launch` 的 `communicate(input=prompt)` 完美匹配 invoker 的 stdin→stdout 协议；
- 引入新基类会动 `executor.py` 已有架构（Story 101/102 的成果），scope 蔓延。

### 3. MiniMax 单元测试 mock 策略

`minimax_invoker.py` 用 `urllib.request` 同步打 API，不引 httpx。Mock 用 `unittest.mock.patch("urllib.request.urlopen", ...)`：

```python
# 场景 1：正常 ask 决策（含 <think> 块 + markdown 包裹）
mock_response = mock.Mock()
mock_response.read.return_value = json.dumps({
    "choices": [{"message": {"content": "<think>...</think>```json\n{\"action\":\"ask\"}\n```"}}]
}).encode()
mock_response.__enter__ = mock.Mock(return_value=mock_response)
mock_response.__exit__ = mock.Mock(return_value=False)
```

测试目标函数：`minimax_invoker._extract_decision()` 和 `minimax_invoker._http_post_chat()`。

### 4. MiniMax E2E：起本地 fake API server

`http.server.HTTPServer` 监听 `127.0.0.1:0`（随机端口），handler 回放固定 JSON。
通过 `MINIMAX_BASE_URL=http://127.0.0.1:<port>/v1` env 注入。
与 minimax-cli's `MINIMAX_BASE_URL` 同一变量名（invoker 文档字符串已声明），无新 env。

测试流程：
1. 起 `fake_minimax_server`（fixture），回放 `{"choices":[{"message":{"content":"<决策 JSON>"}}]}`；
2. 设置 `AGENTBOARD_MINIMAX_BIN = <python> <minimax_invoker.py>`、`MINIMAX_API_KEY=sk-test-fake`、`MINIMAX_BASE_URL=http://127.0.0.1:<port>/v1`；
3. seed schedule + run，`launch_run` → 断言 success + `output` 含 fake server 回的 `converged_spec`。

## 兼容性与回退

- `MiniMaxLauncher` 是**纯新增**：`agentboard/executor.py` 追加 8 行 + 一个 import，**不修改** `CodexLauncher` / `ClaudeLauncher` / `CliLauncher` / `ADAPTERS` 既有键；
- `KNOWN_AGENTS = ("codex", "claude", "workbuddy", "qoder")` 不动（MiniMax 走 schedule 字段，不依赖 KNOWN_AGENTS）；
- 既有测试 fixture 隔离已能兜底（`test_epic78_story102_launcher.py` 顶部 `_isolate_registry` fixture），新测试同款即可；
- 失败回退：fake server 崩了 / port 占用 → E2E 失败，不影响生产；生产 invoker 路径靠 `MINIMAX_API_KEY` 是否设置启用，缺 key 走 `sys.exit(1)`，与现状一致。

## 风险与缓解

| 风险 | 缓解 |
|---|---|
| Codex 真实输出格式后续变 → fake CLI 与生产脱节 | fake CLI 在 docstring 注明"对齐 OpenAI Codex CLI 0.x JSONL 格式"，CI 只验 e2e 跑通；后续若变，单独 change 同步 fake 脚本 |
| MiniMax API 改 base URL / 鉴权方式 → invoker 逻辑要改 | 这次只动 executor 包装，invoker 本身零改动；后续 invoker 改时 e2e 还能兜住（只验协议） |
| fake HTTP server 端口冲突 | `_free_port()` 模式（同 `test_epic78_story102_launcher_e2e.py`）拿随机端口 |
| Windows 下 `python scripts/minimax_invoker.py` 路径 | `command` 用 `Path(__file__).parent.parent / "scripts" / "minimax_invoker.py"` 解析，pytest 临时目录不影响 |
| CliLauncher 子进程 stdout 编码 bug | 修：env 注入 PYTHONIOENCODING=utf-8 + PYTHONUTF8=1（本次 Change 顺手修了） |
| Python 3.14 移除 urllib.server | 用 http.server（已在测试中用） |
| monkeypatch.setattr 不支持 side_effect | mock.Mock(side_effect=...) 包一层再传 |
| 模块级 env 常量 import 时就冻结 | 测试用 `_import_invoker(**pre_env)` 在 import 前塞 env |
