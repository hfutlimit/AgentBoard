# WorkBuddy CLI 集成（Worker 无头 Agent）—— 已验证配置

> 状态：已端到端跑通（2026-08-08，Proposal → Grill → 收敛 → Ticket 全链路真实 codebuddy 执行）
> 相关：Epic 96 Proposal 澄清回路 + 文档 #59 Proposal→Ticket 异步转化

## 1. 结论

Worker 的 `AGENTBOARD_WORKER_AGENT_CMD` 可直接集成 **WorkBuddy 的 CodeBuddy CLI**
（`codebuddy`），无头模式（`-p`）经 stdin 喂 prompt、stdout 回读决策 JSON，
并通过 `--mcp-config` 连接 AgentBoard MCP 完成提问（proposal_ask）与工单创建
（proposal_create_ticket）。2026-08-08 已用真实 CLI 跑通：创建提案 → 3 轮 grill
（codebuddy 提出 9+3+3 个澄清问题）→ 收敛 → 生成 Story（codebuddy 经 MCP 创建）。

## 2. 环境事实（Windows）

| 项 | 值 |
|---|---|
| CLI 路径 | `E:\Program Files\WorkBuddy\resources\app.asar.unpacked\cli\bin\codebuddy` |
| 包 | `@genie/agent-cli`（CodeBuddy 2.115.0，Node shebang 脚本） |
| 无头参数 | `-p/--print`（stdin 读 prompt）+ `--output-format text` |
| 工具放行 | `-y`（否则工具调用被权限拦截） |
| MCP 配置 | `--mcp-config <json>`，HTTP server 必须带 `"transport": "http"` |
| 登录态 | codebuddy CLI 独立登录（与 Desktop 共享凭据，已登录可直用） |

## 3. 关键命令模板

```bash
# 单次无头调用（验证）
node "E:/Program Files/WorkBuddy/resources/app.asar.unpacked/cli/bin/codebuddy" \
  -p -y --mcp-config <mcp.json> --output-format text <<< "prompt"
```

**MCP 配置（mcp.json）**——HTTP/Streamable 传输，**必须带 transport 字段**：
```json
{
  "mcpServers": {
    "agentboard": {
      "transport": "http",
      "url": "http://124.220.44.12/mcp"
    }
  }
}
```

**Worker 启动（agent_cmd 模板）**：
```bash
AGENTBOARD_API_URL=<api_url> \
AGENTBOARD_WORKER_TOKEN=<abk_ key 或登录 token> \
AGENTBOARD_WORKER_AGENT_CMD="\"C:/Users/<user>/.workbuddy/binaries/node/versions/<ver>/node.exe\" \"E:/Program Files/WorkBuddy/resources/app.asar.unpacked/cli/bin/codebuddy\" -p -y --mcp-config \"<abs>/mcp.json\" --output-format text" \
AGENTBOARD_WORKER_AGENT_TIMEOUT=300 \
python -m agentboard.worker --loop
```

## 4. 踩坑记录（重要）

1. **WinError 193（不是有效的 Win32 应用程序）**：`codebuddy` 是 `#!/usr/bin/env node`
   shebang 脚本，`subprocess.run([...])` 直接执行会失败（Windows 不解析 shebang）。
   **必须用 `node.exe` 显式执行**（worker `split_command` 会正确处理带空格/引号路径）。
2. **`-y` 必须加**：`-p` 模式下工具调用默认要求确认，无 `-y` 会输出
   "Re-run with `-y` flag" 并拒绝（worker 解析不到 JSON 决策）。
3. **`--mcp-config` 缺 `"transport": "http"` 会静默失败**：codebuddy 按 stdio 默认
   尝试启动子进程，HTTP server 收不到请求、CLI 卡住直到超时。
4. **MCP 身份**：`AGENTBOARD_MCP_REQUIRE_AUTH=0`（默认）时 MCP 固定以服务端
   `AGENTBOARD_MCP_TOKEN` 身份运行——worker 机器上 MCP 服务须设该 env 为
   有目标项目写权限的 abk_ key / 登录 token，否则 `proposal_create_ticket` 403。
5. **收敛倾向**：codebuddy 会多轮追问（1-3 轮正常）；若一直不收敛，由
   `AGENTBOARD_WORKER_MAX_ROUNDS`（默认 5）护栏兜底转 failed 人工介入。

## 5. 验证记录（2026-08-08 本地 18101/18002）

- 澄清轮：claim → codebuddy 提出 9 问（泳道结构/交互/排序/持久化）→ 作答 →
  再问 3 问（错配重确认，codebuddy 正确识别答案错配）→ 作答 → converged；
- 收敛规格：完整 Markdown（视图结构/未分配泳道/拖拽/排序/持久化）；
- Ticket 轮：点击生成 → ticket_preparing → worker 拉起 codebuddy →
  codebuddy 经 MCP 调 `proposal_create_ticket` → Story 落库（title=提案标题、
  description=converged_spec 原文）→ ticket_created（终态）。
