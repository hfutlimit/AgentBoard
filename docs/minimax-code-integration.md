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
2. **402 insufficient balance ≠ Key 无效**：鉴权已过（否则 401），是账户按量余额
   不足——去 platform.minimaxi.com 充值，或改用 Token Plan Key。
3. **Git Bash 下 minimax mcp 报 MODULE_NOT_FOUND**（`e:\c\Users\...` 路径错乱）：
   MSYS 路径转换坑，用 cmd/PowerShell 执行即正常。
4. **MCP HTTP 传输 v1.0.1 有缺陷**：`--transport http` 连 AgentBoard 报 404、
   `streamable_http` 报 "SSE endpoints are not compatible"——待 CLI 升级；期间
   Story/Ticket 执行轮走 codebuddy 通道。
5. **`-p` 是参数不是 stdin**：Worker 协议是 stdin 喂 prompt，必须经
   `scripts/minimax_adapter.py` 桥接。
6. **超长 prompt 拒绝**：适配器对 >20K 的 prompt 直接 fail（Windows 32K 上限）。
7. **AGPL-3.0 许可**：商业分发需注意合规（个人/内部使用无碍）。

## 6. 验证方法

```bash
# 1. 适配器冒烟（协议层，无需真实模型）
echo '{"action":"ask"}' | python scripts/minimax_adapter.py

# 2. 真实无头（需账户有余额）
"C:/Users/<u>/.workbuddy/binaries/node/versions/22.22.2/minimax.cmd" -m MiniMax-M2 -p "用一句话回答：1+1=?"

# 3. 端到端（余额解决后）：起 API 18099 + worker --loop，确认 Story 后
#    观察 worker 拉起适配器 → minimax-cli 输出含决策 JSON → worker 落库
```

## 7. 后续演进（可选）

- MiniMax 官方若发布 headless CLI（对标 claude code），补 `--mcp-config` 支持后
  可升级为全通道 agent（同 codebuddy 模式）；
- minimax-cli 升级修复 MCP HTTP 传输后，本通道可承担 Story/Ticket 执行轮
  （配 `minimax mcp add agentboard --transport http --url <mcp>/mcp`）；
- 给 codebuddy 通道补 `minimax-coding-plan-mcp`（uvx）可让现有 agent 获得
  MiniMax 编码计划/推理能力（MCP 多 server 叠加）。
