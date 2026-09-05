# Worker-owned 生产 Codex E2E（2026-09-05）

结论：生产 API + 生产 RabbitMQ + 两个真实 Codex/terra Node Worker 的受控故障闭环通过。
这不是隔离 SQLite/FastAPI 的测试，也不是将 KnowledgeVault 主应用部署到生产。
测试应用仅在本地独立 checkout 的 127.0.0.1 上运行。

## 生产业务结果

- 专用 Project **16**，Epic **166**，Proposal **18** → Story **428（done）**。
- 初始 Design **1702**、Dev **1703**、QA **1704** 全部 done。
- QA 真实测到中文姓名回退为 `world`，提交 `tests_passed=false`；QA Review 批准其工作质量后，Worker 自动新建 **bug 1705** 和 **QA 复测 1706**。
- Bug 1705 走 `dev` / `dev_review`，修复后才解锁 QA 1706；最终五类 HTTP 请求与回归测试通过，独立 QA Review 后 Story 自动关闭。
- 原 Dev 1703 始终保留 done，原 QA 的失败结果未覆盖；原 QA → Bug → 复测的依赖通过生产 MCP 读回核对。
- 创建 Epic 自动生成的默认 backlog Story **427** / Tasks **1700、1701** 未激活、未执行、未删除，不属于 Proposal 18 的验收范围。现有项目 8 的六个旧 assignment 指针也未改动。

| 本地独立 Worker / Agent | Provider / model | 实际执行工作 |
| --- | --- | --- |
| prod-codex-20260905-0 | codex / gpt-5.6-terra | proposal、design、dev（含 Bug）、qa_review |
| prod-codex-20260905-1 | codex / gpt-5.6-terra | design_review、dev_review、qa（含复测） |

两个 Node 只订阅 Project 16 的七种队列；本轮没有启动其他 provider 的测试 Worker。
全部业务工作由 Worker 发起 offer、RabbitMQ 交付、HTTP fenced claim/complete 驱动；
没有直接插入 WorkerWork、伪造完成结果或手工修改 Task/Story 终态。

## 故障注入边界及真实证据

Proposal 明确规定初始 Dev 交付一个故意保留 Unicode 回退的测试样例，初始 Dev Review 按样例合同评审；
QA 则按最终产品合同真实测出该错误。这验证缺陷闭环，不声称是在未知产品中自然发现了随机 Bug。

专用 worktree：`tmp/worker-owned-e2e/workspace-prod-codex-01`，未推送其测试分支。

- 起点：`6d1f825689c9225405117e7500f5aa14f2c11684`。
- 设计：`b265c67`；故障样例：`b583cf2c193254487fc00ed481d3763722258d10`。
- 修复：`1eaa2a70ea4dbd3bc0e25985fb5a50fb05b39a64`；最终 HEAD 不变且工作树干净。
- 修复前真实 HTTP：张三 → `Hello, world!`；修复后 → `Hello, 张三!`（原始 UTF-8 字节 `e5bca0e4b889`）。
- 最终 QA 在端口 **54099** 验证缺失名、空名、ASCII、中文、404，5 项回归测试通过；QA Review 又在 **54034** 独立重跑并确认端口释放。
- 本地报告：`tmp/worker-owned-e2e/prod-codex-02/report.json`，`passed=true`；生产 MCP 独立读回 Story 428 为 done。
- 原始请求/响应、汇总 JSON、回归日志保存在同目录 `qa-artifacts/`，运行日志和本地 journal 保留供审计。journal 是敏感运行数据，不提交仓库。

运行中 Node 来自 `dd97f71`，DLL SHA256：
`8dd40029adf00e7569ff6f085ba8702ba6df638becc8ae840aadaead16c87b7e`。
服务器本轮没有进一步部署；测试中发现的提示/类型反馈修复在运行目录之外实施，不能把旧二进制实测当作新补丁的真实 CLI 覆盖。

## 真实遇到的异常（未掩盖）

1. Proposal 两次将 `spec` 返回为对象而非字符串，被生产 API 拒绝；第三次自行修正后成功。没有达到三次同因失败，不触发 provider 切换。
2. QA 第一次给 defect 加了额外结构化字段，被两字段契约拒绝；第二次保留证据并放入 description 后成功。
3. QA Review 真正驳回过一次报告：汇总文件的中文期望值乱码，与其他证据矛盾。QA 重跑、修正 UTF-8 证据后才获批准并创建 Bug。
4. RabbitMQ 管理 HTTP 连接断开曾让观测脚本提前停止；保留原 Proposal/Worker/journal 后恢复。管理端观测随后降级为非阻断；AMQP 鉴权、七种队列与真实交付另行确认。本轮不是无中断运行测试。
5. Node 的生产 offer 扫描记录过 HTTP 400 和一次 **POST /api/worker-work/offers HTTP 500**，随后恢复并完成业务。500 根因未确认，需要服务器异常栈进一步定位，不能据最终通过称运行零异常。

本地修复：明确 Proposal spec 是 Markdown JSON 字符串、defect 只有 title/description 两个字段，
增加发送前类型反馈；不静默强转模型结果、不丢弃证据、不放松生产验证。
**Node 全套 297 项通过**。这部分是 Worker-only 改动，无新增迁移、不要求重发 FastAPI。

收尾：两个测试 Node 和测试 HTTP 服务已退出；不 purge 生产队列、不删除生产测试记录。
RabbitMQ URI 按用户要求保存在 Windows 当前用户环境变量 `RabbitMq__Uri`，未写入仓库；测试账号建议轮换。
本次只验收生产 Codex，不能外推 WorkBuddy 或 MiniMax Code 的生产结果。
