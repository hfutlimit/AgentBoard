# Proposals 特性评审报告（AgentBoard 垂直切片）

> 评审日期/对象：agentboard/features/proposals/{models.py, state_machine.py, service.py, router.py, ticket_ref.py, display.py}，对照 core/state_machine.py、core/service_helpers.py、core/api/deps.py、api_helpers.py、api.py(middleware)、顶层 agentboard/service.py。
> 背景：proposals 为"需求澄清提案流"（提案状态机 + 转换成 ticket），core 层提供 StateMachine 基类、exceptions 体系、service_helpers、deps.get_current_user_optional。

## A. 亮点

- **并发仲裁正确**：`claim_proposal`（service.py:183-229）与 `claim_ticket_request`（service.py:560-587）用单条条件 UPDATE 做 CAS，判定与写入压进同一条 SQL，由数据库仲裁，无 TOCTOU；注释（service.py:189-205）把"为什么 PUT /status 不能仲裁、UPDATE 必须是会话第一条 SQL"讲透了。
- **幂等设计成体系**：`(proposal_id, round_no)`（models.py:180）、`(proposal_id, type)`（models.py:233）唯一约束兜底 at-least-once；`create_ticket_request` 对 done 复用、failed 重置重排（service.py:373-389）；`execute_ticket_request` DONE 短路复用（service.py:465-467）；`convert_proposal_to_story` 以 story_id 防重放（service.py:906-912）。
- **租约语义正确**：租约挂在独立 `claimed_at` 而非 onupdate 的 `updated_at`（models.py:166-170），reclaim 判定与 NULL 兜底（service.py:738-741）处理了历史遗留行；`reclaim_stale_proposals`/`recover_failed_proposals` 的"崩溃回收 + Agent 不可用自动重投 + max_retries + AGENT_ERROR_KEYWORDS 区分人工/自动失败"（service.py:720-811, 顶层 service.py:2590-2593）构成完整自愈闭环。
- **编辑并发防护**：编辑回退时取消未完成转换请求（service.py:994-1002），防"用并发修改后的内容生成 ticket"。
- **状态表单一事实源**：`PROPOSAL_TRANSITIONS` 集中在 models.py:60-97，display.py 为纯展示映射（不碰 DB、不碰状态机），分层干净。
- **转换归属校验**：`request_id` 必须属于 URL 中的 proposal（service.py:450-455）；`_validate_ticket_parents` 做层级 + 跨项目校验（service.py:113-144）。
- **TicketRef 职责集中**：把 4 类型创建/回填集中（ticket_ref.py:35-74），并用函数内惰性 import 解除 `service→models(shim)→ticket_ref` 循环（ticket_ref.py:13-16）。

## B. 职责 / 重复问题

- **顶层 service.py 与 features/proposals/service.py 双实现 + 部分重绑（最严重结构问题）**：顶层 service.py:2437-2977 仍保留 update_proposal / delete_proposal / convert_proposal_to_story / answer_proposal_question / _maybe_mark_answered / list_proposal_rounds / claim_ticket_request / fail_ticket_request / list_ticket_requests / list_pending_ticket_requests / get_ticket_request / _cancel_open_ticket_requests / _ticket_execute_result / reclaim_stale_proposals / recover_failed_proposals 的完整副本，而末尾重绑（service.py:3034-3039）只覆盖 10 个名字。router 全部 `from ... import service`（顶层 facade），因此 **update_proposal 等走顶层旧副本、set_proposal_status / claim_proposal / create_ticket_request / execute_ticket_request 等走 features 新副本**——同一特性运行时混用两套实现，任何修复都要双写，否则静默漂移（当前靠人工同步保持一致）。
- features/proposals/service.py:675-717 的 `_sm_*` 副作用与顶层 service.py:2494-2529 完全重复，且绑定的是顶层版本（顶层 service.py:2532 bind_side_effects），features 版是死代码。
- domains/proposals/{models,state_machine}.py 是 features 的薄 facade，导致顶层经 `domains.proposals.*`、features 经 `.models`/`.state_machine` 两条路径 import 同一份对象（可接受，但属于迁移残留）。
- 状态机三套约定并存：core.StateMachine（core/state_machine.py:68-169）、TaskStateMachine（work_items 正确继承 core）、ProposalStateMachine（自成一派，未继承 core）——见 D。
- 双转换路径：`convert_proposal_to_story`（service.py:883-964）与 `execute_ticket_request`+TicketRef（service.py:427-512）各自实现 story/task 创建、spec 清单解析（`_SPEC_TASK_RE`，service.py:420）、幂等回填，语义分裂（见 C-M10）。
- 状态直写点 7+ 处（见 C-M5），状态机"唯一事实源"被架空。
- 展示契约三处定义：display.py 6 态（自称唯一来源）vs models.py 11 态枚举 vs 文档 #59；DB 事实是 11 态。

## C. 问题清单（按严重度排序）

### 严重

1. **[严重] api_helpers.py:223-225 + router.py:475-495** PUT `/api/proposals/{qid}/answer` 的 qid 是 **question id**，而 project_access_middleware 的 `_resolve_project_id_from_request` 把它当 proposal_id 解析（`get_proposal_project_id(qid)`）→ 多数情况 pid=None → 直接放行；端点内只用 `_optional_user_id`（可选认证）且不校验成员。
   → 影响：REQUIRE_AUTH=1 生产姿势下该写端点完全无鉴权，任意未认证者可跨项目覆盖任意问题的作答。
   → 修复：端点内显式 `_enforce_member_or_admin`（按 question→proposal→project 解析），或 middleware 增加 question id 解析（get_question_project_id）。

2. **[严重] router.py:66-73 / 78-96 / 101-123** `GET /api/proposals/pending`、`POST /api/proposals/reclaim-stale`、`POST /api/proposals/recover-failed` 无任何鉴权，且路径不含数字 pid → middleware 解析不到项目 → 生产姿势下也直接放行。
   → 影响：匿名可批量读取全部 queued 提案（title/content 跨项目泄露）、可带 `lease_seconds=0`/`max_retries=0` 强制批量重置/重投。
   → 修复：加 worker/admin 鉴权（同 `/api/admin/ticket-requests/*` 的 `_auth_is_required() and not is_admin → 403` 模式）。

3. **[严重] router.py:44-62 + service.py:318-347** `GET /api/proposals` 不带 project_id 时：middleware 解析不到项目放行；`service.list_proposals(user_id=None)` 走不到成员过滤分支（`elif user_id is not None`）→ 返回**全部项目**提案。
   → 影响：生产姿势下匿名跨项目列表泄露。
   → 修复：列表端点强制鉴权；匿名/无 project_id 时返回空集（与 admin 端点"未登录→空统计"语义一致）。

4. **[严重] service.py:480-486 + 350-411 + models.py:90-93** 多类型 ticket 请求死锁：proposal 允许 `(proposal_id, type)` 多个请求（models.py:233），但只有一个终态 ticket_created。若用户在请求 A 完成前创建请求 B：A 执行后 p=ticket_created，B 的 CAS 认领成功却撞上"仅 ticket_preparing 可执行"守卫（service.py:480-486）→ B 卡 processing → 超时 failed → 重试时 create_ticket_request 把 failed 重置 pending 但 p 是 TICKET_CREATED 不再回退（service.py:379-381）→ B 永远无法执行，且无删除端点，**永久卡死无恢复路径**。
   → 修复：守卫改为 `p.status in (TICKET_PREPARING, TICKET_CREATED)`（或按请求维度判定）；failed 重置时对 TICKET_CREATED 给出明确拒绝。

5. **[严重] service.py:3034-3039 vs 2437-2977** 双实现 + 部分重绑（见 B）。
   → 影响：同一提案特性两套代码在跑，修复只落一处即漂移（当前无任何机制保证同步）。
   → 修复：把 features 全部函数加入重绑或删除顶层副本。

### 中等

6. **[中等] ticket_ref.py:42-43 + service.py:488-509 + core/service_helpers.py:68-84** 事务一致性是"条件成立"的：`_commit` 在请求作用域（database.py:80 `auto_commit=False`）只 flush，整段 execute 事务原子成立；但 TicketRef.create 依赖 `create_epic/create_story/create_task` 内部 `_commit`（注释明言"内部各自 commit"），一旦有调用方以 `auto_commit=True` 会话执行，创建之间真 commit → 崩溃窗口产生孤儿 ticket（request 仍 processing → 超时回退 → 重执行再建一个）。
   → 修复：TicketRef.create 内禁止依赖外部 auto_commit 语义（改为纯 flush 型 ORM 写入），或在函数内断言 `auto_commit is False`。

7. **[中等] service.py:469-477 + 560-587** execute 流程里 `create_ticket_request`（新请求 flush 进同一事务）后 `claim_ticket_request` 竞争失败会 `s.rollback()` **把刚创建的请求一起回滚**，却报"正在生成中/无法执行"→ 前端拿到 409 但实际什么都没落库，重试才重建。
   → 修复：claim 失败时只回滚 UPDATE 语句（savepoint），或先 CAS 再创建请求。

8. **[中等] service.py:367-411** `create_ticket_request` 并发首击：两个请求都查到 existing=None → 双 INSERT → 第二个撞唯一约束抛裸 IntegrityError → 500（`_commit` 未传 duplicate，service_helpers.py:82）。
   → 修复：捕获 IntegrityError 后重查返回既有请求（幂等语义）。

9. **[中等] service.py:286-315** `create_proposal_round` 并发（round_no=None 时都算 current_round+1）同轮插入撞唯一约束 → 500。租约被 reclaim 后旧 worker 仍在跑时真实可发生。
   → 修复：同 8（IntegrityError → 重查复用）。

10. **[中等] service.py:259-261 / 836-857 / 994-1007 / 621-622 / 954-958 / 750-760 / 803** 7+ 处直接写 `p.status` 绕开 `ProposalStateMachine.execute`：提问后（259-261）租约 `claimed_by/claimed_at` 残留不清理；`_maybe_mark_answered`（856）非 CAS 写 ANSWERED；`reclaim/recover` 批量直写。
    → 影响：状态机唯一事实源被架空，副作用语义漂移（迁移表改动后这些路径静默失同步）。
    → 修复：统一走 SM.execute（含批量场景提供条件 UPDATE + 副作用分派），至少补租约清理。

11. **[中等] models.py:85 + state_machine.py:99-102 + service.py:391-399** PUT /status 可直接 `converged→ticket_preparing`（表允许）而绕过 `create_ticket_request` 的 converged_spec 非空校验；随后 TicketRef 回退用 content（ticket_ref.py:48）照样出 ticket → 与"converged_spec 为空无法生成"契约矛盾。**validator 机制存在但从未注册任何校验器**。
    → 修复：把必要条件（converged_spec 非空等）注册为 SM validator。

12. **[中等] service.py:427-512 + ticket_ref.py:49** 按 request_id 执行时忽略 `req.title`（TicketRef 用入参 title 或 proposal.title）→ 创建请求时用户填的自定义标题被丢弃，RPC 执行需重复传参。
    → 修复：title 解析优先级 `req.title → 入参 → proposal.title`。

13. **[中等] router.py:162-164 / 271-272 / 492-494 + api_helpers.py:323-333** MQ 派发在请求作用域 commit（get_session 退出）**之前**（service 只 flush）→ outbox 反模式：MariaDB 下 worker 可能读到未提交状态（SQLite 靠写锁+busy_timeout 碰巧串行化；polling + reclaim 兜底）。
    → 修复：after_commit 钩子或 outbox 表。

14. **[中等] service.py:883-964 vs 427-512/ticket_ref.py** convert（story+checklist tasks）与 ticket 转换（TicketRef）双路径创建 story/task，spec 清单解析、幂等回填各一份，且 convert 不产生 ticket request、不回填 ticket_type → 审计与幂等模型分裂。
    → 修复：统一到 TicketRef + checklist 扩展，convert 走同一请求状态机。

### 轻微

15. **[轻微] state_machine.py:12** 模块文档"commit 由 execute() 统一管理"与实现矛盾：execute() 不 commit（set_proposal_status:177 才 `_commit`）→ 误导维护者（与 core 语义其实一致，文档写错）。

16. **[轻微] state_machine.py:44-50 + features service:675-717** 模块级可变全局 `_SIDE_EFFECTS` + `bind_side_effects().clear()`，注册顺序敏感；且 features 版 `_sm_*` 副作用是死代码（绑定的是顶层 2494-2529 版本）→ 双份副作用实现。

17. **[轻微] state_machine.py:103-108** 同状态迁移视为 no-op 但仍执行副作用：PUT /status `analyzing→analyzing` 刷新 claimed_at 却不写 claimed_by（租约"有时间戳无持有者"）；与 claim_proposal 的写入不对称。

18. **[轻微] service.py:596-601** `_ticket_execute_result` 对 `s.get(Epic/Story/Task)` 各查两次（条件 + `_ser` 各一次）→ 多余 DB 往返。

19. **[轻微] service.py:404 / 814-833 / 242-244** create_ticket_request 的 title 静默 `[:300]` 截断而非 `_required` 报错；answer/questions/summary/content 均无长度上限（Text 无界）→ 超大作答撑爆行/响应。

20. **[轻微] service.py:1005-1006** `claimed_by/claimed_at` 清空重复两行（复制粘贴冗余，顶层 2475-2476 同样）。

21. **[轻微] display.py:107-166** 无 XSS：纯枚举映射、不拼 HTML、不接触用户内容，安全。但 content/converged_spec/问答正文经 `_ser` 原样输出且服务端无 sanitize/长度控制，存储型 XSS 完全依赖前端转义 → 建议内容层统一渲染白名单（不在本模块范围）。

22. **[轻微] ticket_ref.py:76-81** `attach_to_proposal` 全库无调用（execute_ticket_request 手动回填 service.py:505-508）→ 死代码。

23. **[轻微] service.py:465-467** DONE 短路不校验 ticket 实体仍存在（被删后返回陈旧 done 结果）。

24. **[轻微] state_machine.py:90-93** from_ 解析失败时用原始字符串查 `PROPOSAL_TRANSITIONS`，依赖 StrEnum 哈希等于字符串值的隐晦行为。

## D. 状态机一致性观察（与 core/state_machine.py 基类契合度）

- **未继承 core.StateMachine**：core.StateMachine（core/state_machine.py:68-169）要求子类填 `_transitions: dict[(from,to), TransitionSpec]`、实现 get_state/set_state、自带 per-transition log（core:161-167）与 core.exceptions.IllegalTransition；`ProposalStateMachine`（state_machine.py:59-109）是自建类：按目标状态注册全局副作用 + 查 dict-of-sets，自抛 `IllegalTransitionError/TransitionValidationError`（service 层再转 core.IllegalTransition），无任何观测日志。work_items 的 `TaskStateMachine` 正确继承了 core（features/work_items/state_machine.py:125-253）——同库两种范式并存。
- **副作用执行顺序相反（最大陷阱）**：core 文档与实现为 `validators → side_effects → set_state`（副作用读旧状态，core/state_machine.py:8,153-158），work_items 的 `_record_status_history` 明确依赖"读旧值"（work_items/state_machine.py:57-66）；proposals 为 `validators → set_state → side_effects`（副作用读**新**状态，state_machine.py:105-108）。跨模块复用副作用函数必踩坑。
- **validator 签名不一致**：core 为 `(s, entity, to) -> err|None`（core:32），proposals 为 `(s, proposal) -> err|None`（state_machine.py:28-29）；且 proposals 从未注册过 validator（见 C-M11）。
- **一致点**：两者都不在 execute 内 commit、由调用方 commit（proposals 实际行为与 core 一致，只是文档写反）；同状态 no-op 是 proposals 特判（core 查不到 spec 会抛 IllegalTransition）。
- **全局注册表**：`_SIDE_EFFECTS` 是模块级可变状态，由顶层 service.py:2532 在 import 时填充，features 与顶层共享同一 registry 但各有一份 `_sm_apply_side_effects`（顶层版本生效）——绑定时机与归属都隐晦。

## E. 成熟度评级：**beta**

理由：核心澄清闭环（claim 租约 CAS、轮次/请求幂等、失败回退自愈、编辑防护、层级校验、归属校验）设计成熟、注释详实、多处 review 修复留痕（2026-08-09/10/15），接近 stable；但存在生产姿势下的权限缺口（answer/批量端点/无参列表）、多类型 ticket 请求永久卡死、双实现漂移风险、并发 500 与条件性事务一致性，且状态机"唯一事实源"被 7+ 处直写架空——尚不宜 stable，亦非 alpha。

## F. 一句话总结

澄清与转换的并发/幂等/自愈设计是亮点，但"权限只靠 middleware 兜底 + 状态机被大面积直写绕开 + 顶层/features 双实现混跑 + 多类型 ticket 死锁"四件事不解决，谈不上 stable。
