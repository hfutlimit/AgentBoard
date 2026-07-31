# Tasks: Proposal 澄清 Worker（Epic 96 P1-2 / Task 932）

## 实现

- [x] 新增 `agentboard/worker.py` 模块骨架（`WorkerConfig` / `AgentDecision` / `AgentInvoker`）
- [x] `WorkerConfig.from_env()`：9 个环境变量，整数解析失败回退默认值并告警
- [x] `extract_decision_json()`：括号配对扫描 + 字符串态跟踪，容忍噪声日志与 Markdown 包裹
- [x] `AgentDecision.from_dict()`：action 白名单 / ask 必须有非空问题 / finalize 必须有非空规格
- [x] `build_prompt()`：决策协议 + 提案正文 + 全量历史问答（含 unsure 标记）
- [x] `SubprocessAgentInvoker`：prompt 走 stdin，超时 / 非零退出码 / 无法启动分别转明确异常
- [x] `split_command()`：Windows 下 `posix=False` + 剥离成对外层引号（双坑一起填）
- [x] `fetch_work()`：双源发现 queued + answered，按 batch_size 截断
- [x] `claim()`：先 GET 复核状态再迁移（服务端同状态迁移是幂等 no-op，无法仲裁并发）
- [x] `build_context()`：全量重放，语义与 MCP `proposal_get` 对齐
- [x] `_apply_ask` / `_apply_finalize` / `mark_failed`：三种决策落库
- [x] `reclaim_stale()`：analyzing 超租约回退 queued
- [x] `handle()`：三层异常兜底，任何路径都不留在 analyzing
- [x] 轮次上限护栏：达 `max_rounds` 仍要提问 → failed 转人工
- [x] `poll_once()` / `run_forever(stop, max_cycles)`
- [x] CLI：`python -m agentboard.worker --once | --loop`，缺配置时构造期 fail fast

## 测试

- [x] 纯函数层：噪声抽取 / 字符串内花括号 / 垃圾输入 / 5 组决策校验 / 时间解析按 UTC
- [x] 闭环层：queued → 提问 → 用户作答 → answered 续轮 → finalize → converged
- [x] 全量重放：第二轮上下文必须带用户答案；unsure 标记透传
- [x] 并发认领：两个 Worker 抢同一提案，仅一个成功
- [x] 崩溃恢复：超租约 analyzing 被回收重投；未到期不被回收
- [x] 失败路径：Agent 抛异常 / 输出非法 / 主动 fail，均落 failed 带可读原因
- [x] 轮次上限：max_rounds=2 时第 3 轮触发护栏
- [x] 同轮重投幂等：不产生重复轮次
- [x] 双源发现：queued 与 answered 都能被 `fetch_work()` 看到
- [x] `run_forever(max_cycles=2)` 可收敛
- [x] 真实子进程层：fake CLI 脚本自证 stdin/stdout 协议 + 驱动完整两轮闭环 + 超时/非零退出码
- [x] Playwright E2E：真实 Worker + 真实 Agent 子进程推动，浏览器可见问题渲染 → 作答 → 收敛，0 报错

## 验证结果

- `tests/test_epic96_p12_proposal_worker.py` — **27 passed**
- `tests/test_epic96_p12_proposal_worker_e2e.py` — **1 passed**（Playwright，0 console error）
- 回归：见提交说明

## 约束核对

- [x] 零 REST 契约变更（`api.py` / `service.py` / `models.py` / 前端均未改动）
- [x] 未触碰端口 18001，未改动任何 docker 配置
- [x] 测试完全自包含（自起 uvicorn 子进程 + 独立临时 SQLite）
