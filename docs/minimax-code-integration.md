# MiniMax Code 集成（Worker 无头 Agent）—— 适配器方案

> 状态：适配器 + CLI 安装 + Key 配置就绪（2026-08-09 实测）；模型调用遇
> **402 insufficient balance**（账户余额不足），充值后即可跑通
> 相关：Ticket 全流程（Story 确认 → Agent 自动处理）、docs/workbuddy-cli-integration.md

## 1. 结论（先说清边界）

- **官方「MiniMax Code」是桌面 GUI 应用**（v3.0.56，macOS/Windows，基于 OpenCode+Pi
  harness），**没有 headless CLI 接口** → 无法直接接入 Worker 的
  stdin→prompt / stdout→JSON 无头协议。
- **本仓库接入的是 npm CLI `minimax-cli`**（v1.0.1，AGPL-3.0）：提供无头模式
  `minimax -p "<prompt>"`（单次执行退出），经 `scripts/minimax_adapter.py`
  桥接 Worker 协议。
- **2026-08-09 实测结论**：
  - ✅ CLI 安装、Key 鉴权、域名/模型打通：**`https://api.minimaxi.com/v1` + `MiniMax-M2`**
    （国内平台；CLI 自带 minimax-01/minimax-pro 等模型名均为海外/旧名，国内平台无效）
  - ✅ CLI 自带 `minimax mcp` 子命令（add/list/test），声明支持 http/streamable_http
    传输——但 v1.0.1 的 HTTP transport 实现有缺陷（http 报 404、streamable_http 报
    "SSE endpoints are not compatible"），**暂时无法连接 AgentBoard MCP**，
    Story/Ticket 执行轮仍建议走 codebuddy 通道；待 CLI 升级后重试
  - ❌ **402 insufficient balance**：当前 Key 鉴权通过但账户 API 余额不足，
    任何模型调用被拒——需充值（platform.minimaxi.com 按量充值）或改用
    Token Plan Key（订阅平台生成）

## 2. MiniMax 生态盘点（选型依据）

| 工具 | 类型 | headless | MCP | 能否作 Worker agent |
|---|---|---|---|---|
| MiniMax Code（官方桌面） | GUI 应用 | ❌ | - | ❌ 无 CLI 接口 |
| `minimax-cli`（npm，v1.0.1） | 对话式 CLI | ✅ `-p` | ⚠️ 有命令但 HTTP 传输有缺陷 | ✅ 澄清/分析轮；执行轮待 MCP 修复 |
| `minmax-code`（npm TUI） | TUI | ❌ | ✅ | ❌ 无 headless |
| `mmx-cli`（官方 API 工具） | 媒体/检索 CLI | ✅ | 被调用方 | 工具集，非编码 agent |
| `minimax-coding-plan-mcp`（uvx） | MCP server | - | 对外提供 | 可给 codebuddy 等补 MiniMax 编码能力 |

## 3. 安装与配置（本机已就绪，2026-08-09）

```bash
# 安装（managed node 的 npm 全局，隔离目录）
npm install -g minimax-cli            # 实测 v1.0.1，244 packages
# Key 写入 ~/.minimax/user-settings.json（CLI 自动读取）：
#   { "apiKey": "sk-...", "baseURL": "https://api.minimaxi.com/v1",
#     "defaultModel": "MiniMax-M2" }
# 注意：CLI 的 -p 无头模式实际不读 defaultModel（仍用 minimax-01），
# 必须 -m MiniMax-M2 显式指定（适配器用 MINIMAX_MODEL 传）。
```

**模型/域名实测矩阵**（2026-08-09）：

| baseURL | 模型 | 结果 |
|---|---|---|
| api.minimax.chat/v1（CLI 默认） | minimax-01 / minimax-pro | 400 unknown model |
| api.minimax.io/v1（海外） | minimax-pro | 401 invalid api key（Key 非海外） |
| api.minimaxi.com/v1（国内） | MiniMax-M2 / M2.5 / M2.7 | **402 insufficient balance**（模型名正确！） |

## 4. Worker 启动（agent_cmd 模板）

```bash
AGENTBOARD_API_URL=<api_url> \
AGENTBOARD_WORKER_TOKEN=<abk_ key 或登录 token> \
AGENTBOARD_WORKER_AGENT_CMD="\"C:/Users/<user>/.workbuddy/binaries/python/envs/default/Scripts/python.exe\" \"E:/Projects/WorkBuddy/AgentBoard/scripts/minimax_adapter.py\"" \
MINIMAX_CLI_PATH="\"C:/Users/<user>/.workbuddy/binaries/node/versions/22.22.2/minimax.cmd\"" \
MINIMAX_MODEL="MiniMax-M2" \
AGENTBOARD_WORKER_AGENT_TIMEOUT=300 \
python -m agentboard.worker --loop
```

适配器环境变量：`MINIMAX_CLI_PATH`（可多段）、`MINIMAX_MODEL`（**国内平台必须
MiniMax-M2 等有效名**）、`MINIMAX_DIRECTORY`、`MINIMAX_TIMEOUT`（默认 600s）。

## 5. 踩坑记录（重要）

1. **国内平台必须 -m 显式模型**：CLI 内置模型列表（minimax-01/minimax-pro...）是
   海外/旧 API 名，国内 api.minimaxi.com 全 400；正确名 MiniMax-M2 / M2.5 / M2.7。
2. **Token Plan Key（sk-cp 开头）才带配额**：普通 API Key 鉴权通过但报
   402 insufficient balance；换 `sk-cp-` 开头 Token Plan Key 即正常。
3. **`-p` 只取 prompt 第一行（v1.0.1 实测限制）**：多行 prompt 被截断，适配器
   把换行转义为字面 `\n` 单行化后模型可完整理解。
4. **CLI 输出 JSONL 流**：每行一个 `{"role":...}` 对象，决策 JSON 嵌套在
   assistant content 字符串内（顶层无 action 键）→ worker 的括号配对扫描
   提取不到，适配器需解析 JSONL 重组 assistant 纯文本输出（`_assistant_text`）。
5. **MiniMax 会自主调工具**（view_file 等探索工作目录）：-p 无头模式也会；
   prompt 需明确"直接输出决策"，完整协议送达后模型通常遵守。
6. **Git Bash 下 minimax mcp 报 MODULE_NOT_FOUND**（`e:\c\Users\...` 路径错乱）：
   MSYS 路径转换坑，用 cmd/PowerShell 执行即正常。
7. **MCP HTTP 传输 v1.0.1 有缺陷**：`--transport http` 连 AgentBoard 报 404、
   `streamable_http` 报 "SSE endpoints are not compatible"——待 CLI 升级；期间
   Story/Ticket 执行轮走 codebuddy 通道。
8. **`-p` 是参数不是 stdin**：Worker 协议是 stdin 喂 prompt，必须经
   `scripts/minimax_adapter.py` 桥接。
9. **超长 prompt 拒绝**：适配器对 >20K 的 prompt 直接 fail（Windows 32K 上限）。
10. **AGPL-3.0 许可**：minimax-cli 为 AGPL-3.0，商业分发需注意合规（个人/内部使用无碍）。

## 6. 验证记录（2026-08-09，真实 Token Plan Key 端到端）

- **环境**：minimax-cli v1.0.1（managed node 全局）、`sk-cp-` Token Plan Key、
  `api.minimaxi.com/v1` + `MiniMax-M2`、本地 API 18099 + worker --once；
- **澄清轮**：提案 queued → worker 拉起适配器 → MiniMax-M2 输出决策 JSON
  `{"action":"ask","questions":[...],"summary":...}` → 落库，提案 → **awaiting**（第 1 轮，
  7 个具体问题：泳道配置/字段/拖拽规则/编辑交互/持久化/技术栈）；
- 全程无需 MCP（澄清轮纯文本决策），Story/Ticket 执行轮仍走 codebuddy（CLI MCP 缺陷未解）。

## 7. 后续演进（可选）

- MiniMax 官方若发布 headless CLI（对标 claude code），补 `--mcp-config` 支持后
  可升级为全通道 agent（同 codebuddy 模式）；
- minimax-cli 升级修复 MCP HTTP 传输后，本通道可承担 Story/Ticket 执行轮
  （配 `minimax mcp add agentboard --transport http --url <mcp>/mcp`）；
- 给 codebuddy 通道补 `minimax-coding-plan-mcp`（uvx）可让现有 agent 获得
  MiniMax 编码计划/推理能力（MCP 多 server 叠加）。
