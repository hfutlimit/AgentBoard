# AgentBoard work_items 垂直切片评审报告

评审范围：features/work_items/{models,state_machine,service,router}.py，对照 core/state_machine.py、core/exceptions.py、core/service_helpers.py、core/api/deps.py、core/infrastructure/{auth,cos_client}.py、api.py 中间件、api_helpers.py、features/{webhooks,documents,projects,scheduling}/service 及顶层 service.py。

---

## A. 亮点

- **状态机一等公民化**：`TaskStateMachine` 把迁移边收敛为 `TransitionSpec`（state_machine.py:151-197），status_reason 校验/清空、写历史、previous_status 维护、缓存失效全部做成 side effect/validator 自动跑，`_apply_status_reason` 在 set_state 前执行（读旧值）的设计有明确注释（state_machine.py:51-53、83-91）。
- **并发敏感的路径用了 CAS**：`claim_development_task`（work_items/service.py:244-255）与 `review_task`（scheduling/service.py:664-689）均用条件 UPDATE + rowcount==1，比 set_status 严谨。
- **update_task 单 commit 收口**：整函数 0 次中间 commit，状态迁移与字段更新同事务提交，避免 partial commit（service.py:916-1013，注释详尽）。
- **import_tasks_from_json 用 SAVEPOINT**（work_items/service.py:567）：单条失败只回滚自身，不影响同批条目，是正确示范。
- **路径穿越防御到位**：附件落盘文件名用 `uuid4().hex`（documents/service.py:129）、COS key 用 `uploads/{pid}/{uuid4().hex}{ext}` + 扩展名白名单（projects/router.py:666-665），不可预测、不可穿越。
- **delete_task 全面清理 9 张关联表**（service.py:1035-1076），FK 防御性级联 + 根因注释，比 `_get_or_404` 式偷懒实现负责。
- **Webhook 响应不回 secret**（webhooks/router.py:40-43、55-60），HMAC-SHA256 签名 + 时间戳；`_ser` 反射列序列化不会漏 lazy 列/关系。

## B. 职责 / 重复问题

- `get_task_project_id` / `get_comment_project_id` / `get_dependency_project_id` 在顶层 service.py:1707-1733 与 projects/service.py:882-911 **双份实现**；`get_attachment_project_id` 在 documents/service.py:495 与 projects/service.py:919 又双份（互相延迟 import 引用对方）。
- **三份 `_validate_status_reason`**：state_machine.py:98（`(s,t,to)`）、work_items/service.py:655（`(new,reason)`）、顶层 service.py:1108（第三份）——同一规则三处实现，签名各不同。
- **两张迁移表**：state_machine.py `_TASK_TRANSITIONS`（blocked→done 允许）vs 顶层 service.py:141-147 `TRANSITIONS`（blocked 缺 done），batch 路径用后者、单条路径用前者。
- **两份 `submit_task_for_review`**（work_items/service.py:266 与 scheduling/service.py:705），facade 先后 rebind（service.py:2999 与 3072），后者静默覆盖前者。
- 两份 `_record_status_history`（work_items/service.py:48 与 service.py:1100）；两份 search（顶层 `search_tasks` vs `search_tasks_enhanced`）。
- router 每个端点手写 `authorization: str | None = Header(None)` + `api_helpers._caller_uid_admin`，core 的 `deps.get_current_user_optional`（deps.py:28）**从未被使用**；`_caller_uid_admin` 每请求自开 1-2 个 `SessionLocal()`（api_helpers.py:134,140），与请求级 session 双轨。
- **模型与服务的 feature 归属分裂**：Attachment 模型在 work_items，附件服务在 documents；COS 常量（`_COS_MAX_SIZE` 等）定义在 work_items/router.py:576-578 却被 projects/router.py:656-664 使用（且未导入，见 C-10）。
- `_comment_target` / `list_comments` / `create_comment` 三处重复"目标实体存在性 + 三者恰一"校验（work_items/service.py:596-637）。

## C. 问题清单（按严重度排序）

### 严重

1. `[严重] scheduling/service.py:664-670, 680-693` review_task approve→done 不写 status_reason；reject 到 blocked 不写 status_reason 且不维护 previous_status → 直接违反"done/blocked 必填 reason"不变量，且绕过 SM 的校验/副作用；DB 层无约束兜底（models.py:16 只查 status 枚举）→ 后续"解除 blocked 恢复 previous_status"逻辑拿到 None 失效 → 修复：CAS UPDATE 同时写 status_reason/previous_status，或让 review_task 复用 execute_transition。
2. `[严重] work_items/service.py:156-164 + service.py:924-990` set_status/update_task 读-改-写无行锁/乐观锁（无 version 列、无 SELECT FOR UPDATE）→ 同一 task 两个并发请求（todo→done 与 todo→in_progress）双双通过校验，history 出现两条相同 from_status、终态 last-write-wins、学习 outcome 与最终状态错位；claim/review 有 CAS 而这两条主路径没有 → 修复：条件 UPDATE（WHERE status=旧值）或 version 列。
3. `[严重] work_items/router.py:456 + schemas.py:131` 评论作者取请求体 `body.author` 而非认证用户，Comment 模型也无 author_id FK（models.py:77）→ 任意成员可冒充他人发评论/被 @mention 通知冒名（api_helpers.py:288-299 用伪造 author 通知）→ 修复：author 从 uid 解析，Comment 增加 author_id 并校验。
4. `[严重] work_items/router.py:467-471, 569 + work_items/service.py:640-644` 删除评论/附件只做项目成员校验（且仅 REQUIRE_AUTH=1 时生效），无作者或项目 owner/admin 校验 → 项目内任意成员可删他人评论/附件 → 修复：作者本人或 owner/admin 才可删。
5. `[严重] api.py:367-368` project_access_middleware 仅在 `AGENTBOARD_REQUIRE_AUTH=1` 时启用，默认（开放模式）**所有** work_items 端点零鉴权，伪造 project_id 可跨项目读写；SM/service 层本身从不校验"任务属于项目/调用者是成员"（execute_transition 无 project 上下文）→ 修复：至少写操作强制成员校验，或默认开启鉴权。
6. `[严重] api_helpers.py:200-205 + webhooks/router.py:22-43,47-61` `GET /api/webhooks` 不带 project_id 时 `_resolve_project_id_from_request` 返回 None → 中间件放行 → 列出**所有项目**的 webhook url/events；全局 webhook（project_id=NULL）任何已认证用户可建/删/改（toggle/delete 解析到 NULL 同样放行），且全局 webhook 会收到全部项目事件（webhooks/service.py:95-98）→ 跨项目信息泄露 + 数据外泄通道 → 修复：无 project_id 禁止列表；全局 webhook 仅 admin 可管。
7. `[严重] work_items/service.py:495-519` add_task_dependency 无 A→B→A 环路检测（仅查自依赖与重复）→ 依赖环导致调度/看板死锁；且不校验 task 与 depends_on 同项目（508-513）→ 跨项目耦合、越权建立依赖 → 修复：BFS/拓扑环检测 + 同项目校验。
8. `[严重] work_items/models.py:120-123` 注释声称"防重复依赖放到 DB 层面处理"但 `__table_args__` 为空，DB 无任何唯一约束 → 并发下先查后插的重复检查（service.py:502-507）竞态，产生重复依赖 → 修复：`UNIQUE(task_id, depends_on_id)`（SQLite 支持简单唯一约束）。
9. `[严重] work_items/router.py:402-403` bulk_update_tasks 的 `except Exception: errors.append(...)` 吞掉异常但 session 未回滚：set_status 在 execute 前已改 `t.status_reason`（work_items/service.py:161-162）、update_task 的 setattr 循环在状态校验前已改字段（service.py:937-968）→ 请求结束 get_session commit 时把"报错任务"的脏对象一并落库（部分更新实际成功）→ 修复：每任务 `begin_nested()` 隔离或校验先行、异常后显式 rollback。
10. `[严重] projects/router.py:656,659,664` cos_upload 使用 `_COS_MAX_SIZE/_COS_ALLOWED_TYPES/_COS_ALLOWED_EXTS`，但常量只定义在 work_items/router.py:576-578 且 projects/router 未导入（schemas `*` 不导出下划线名）→ 运行时 NameError → 该端点必然 500 → 修复：常量移到共享模块（如 core/service_helpers）或本地定义。

### 中等

11. `[中等] work_items/router.py:532 + documents/service.py:127 + projects/router.py:653-657` 附件/COS 上传先 `await file.read()` 整读进内存再做大小校验 → 超大文件内存耗尽 DoS（10MB 限制形同虚设）→ 修复：流式读 + 提前按 Content-Length/分块限流。
12. `[中等] documents/service.py:125 + projects/router.py:658-661` MIME 白名单只信客户端 Content-Type（可伪造），无 magic bytes 嗅探 → exe 伪装 image/png 入库，下载时 media_type 照抄（router.py:555）有内联执行风险 → 修复：文件头校验。
13. `[中等] schemas.py:132 + models.py:78 + schemas.py:116-117` 评论 content 仅 min_length 无上限、description/spec 无长度限制，内容原样存储 → DB 膨胀 + 前端渲染 XSS（服务端无 sanitize/转义策略，依赖前端 markdown 渲染器）→ 修复：max_length + 服务端 HTML/Markdown 净化。
14. `[中等] service.py:977` update_task 对非法 status 直接 `Status(new_status)` 抛 ValueError，router（router.py:76-80）只捕 InvalidValue/IllegalTransition → 非法 status 返回 500 而非 400/422 → 修复：先 `_check_status(new_status)`。
15. `[中等] router.py:79-80,122-123 vs core/exceptions.py:64-67` 同一 IllegalTransition 在 update_task/set_status 映射 400，而 core 定义 http_status=409；claim 的 InvalidValue 映射 409（router.py:167）其余映射 422 → HTTP 语义混乱 → 修复：按 core 异常统一映射。
16. `[中等] api_helpers.py:33 vs core/service_helpers.py:92 + state_machine.py:72` 缓存失效双轨：`_invalidate_stats_cache` 删 `stats:{pid}`，SM/core 删 `project_stats:{pid}` → 部分端点失效错键，项目统计缓存陈旧 → 修复：统一 cache key。
17. `[中等] service.py:146 vs state_machine.py:175-184` 迁移表漂移：顶层 TRANSITIONS 的 blocked 目标缺 DONE，SM 预注册 blocked→done → batch_update_task_status 判 blocked→done 非法、set_status 却合法，同任务不同入口行为不一致 → 修复：收敛为单一表。
18. `[中等] scheduling/service.py:695` review_task 用 `reviewer.display_name or username` 拼 author 调 create_comment → display_name 可含任意文本污染作者字段/通知链 → 修复：改用 user_id + 规范名。
19. `[中等] work_items/service.py:531-546` get_task_dependencies 每依赖行两次 `s.get`（536、543 行），N+1 查询 + 重复命中 → 修复：一次 IN 查询 + joinedload。
20. `[中等] api_helpers.py:121-142` `_caller_uid_admin` 每请求自开 1-2 个 SessionLocal 且重复 parse_token，core `get_current_user_optional` 弃用 → 每请求额外会话/查询、鉴权实现双轨 → 修复：改用 deps 注入。

### 轻微

21. `[轻微] service.py:1045 + work_items/service.py:379-383` 删除任务/批量删除只删 Attachment 行不删磁盘文件（documents/service.py 的 get_attachment_path 目录）→ 孤儿文件累积。
22. `[轻微] documents/service.py:130-135, 271-278` create_attachment 先写盘后 commit（失败留孤儿文件）；delete_attachment 先删文件后删行（失败文件丢行留）→ 换序 + 失败补偿。
23. `[轻微] work_items/service.py:576-587` import_tasks_from_json 直接赋 status 绕过 SM：done/blocked 无 reason 校验、无历史、无 previous_status → 不变量被批量导入打破 → 修复：至少校验 reason + 写历史。
24. `[轻微] work_items/service.py:301-342` batch_update_task_status 不失效项目统计缓存（SM 路径自动失效，此处手写绕过没有）→ 缓存陈旧。
25. `[轻微] work_items/service.py:266 vs scheduling/service.py:705` 双份 submit_task_for_review，facade 后导入覆盖（service.py:2996-3006/3068-3078）→ 静默漂移风险。
26. `[轻微] core/state_machine.py:8 vs 121` 基类 execute docstring"…→ commit"与实现"本函数不 commit"矛盾；state_machine.py:242 的 execute_transition 直接摸私有 `_transitions` 而非公开 `can_transition` → 文档/API 修正。
27. `[轻微] state_machine.py:239-249` blocked→blocked、todo→todo 等自转一律 IllegalTransition → 改 reason/重复提交被拒，前端需特判 → 可选 no-op 语义。
28. `[轻微] models.py:133` Webhook secret 明文落库（String），`_ser` 全列序列化 → 若将来对 WebhookConfig 用 _ser 即泄露；建议加密或掩码。
29. `[轻微] work_items/service.py:321-326` batch 解除 blocked 只认 `previous_status` 精确匹配（blocked→done 依赖 prev=done），与 SM 全向可达不一致 → 行为漂移。

## D. 状态机一致性观察（与 core/state_machine.py 基类的契合度）

- **契合部分**：TaskStateMachine 正确实现 get_state/set_state，用 TransitionSpec 注册 side_effects/validators；validator 直接抛 `InvalidValue`（DomainError）而非返回错误串，基类 execute 会原样传播（core/state_machine.py:145-151），行为合规；side effect 在 set_state 前执行（读旧值）的约定被正确利用。
- **绕开基类的手写逻辑清单**：
  1. `claim_development_task`（work_items/service.py:230-263）：CAS UPDATE 直改 status + 手动写历史，跳过 SM 的 `_apply_status_reason`/缓存失效（缓存手动补了）；
  2. `review_task`（scheduling/service.py:629-698）：完全绕过 SM，CAS 直写 done/blocked，**不跑任何 validator**——这是不变量破口最严重的一处；
  3. `batch_update_task_status`（work_items/service.py:301-342）：手写迁移判定（用旧 `transitions_for`）、手写 history/previous_status/status_reason；
  4. `import_tasks_from_json`（work_items/service.py:576-587）：直接赋 status，无校验无历史；
  5. `update_task`（service.py:975-984）：自行先调 `_validate_status_reason` 再进 execute_transition，同一校验跑两遍（SM 内还会再验一次），且 set_status 在 execute 前先改 `t.status_reason`（work_items/service.py:161-162）——通过实体可变状态而非 ctx 传递输入，脆弱耦合。
- **结构性漂移**：两张迁移表（`_TASK_TRANSITIONS` vs 顶层 `TRANSITIONS`）已确认不一致（blocked→done）；`execute_transition` 的 blocked 分支摸私有 `_transitions` 做重复检查（state_machine.py:242），本可用 `can_transition`。
- 结论：SM 框架本身设计良好，但"便捷路径"（claim/review/batch/import）四处绕过导致同一套不变量在多条路径上执行力度不同，规则收敛不足。

## E. 成熟度评级：**beta**

理由：架构骨架已成型（SM 一等公民、CAS 认领/评审、SAVEPOINT 导入、update_task 原子化、全面的 FK 清理与注释文化），但存在：① 默认部署（REQUIRE_AUTH=0）全端点无鉴权；② 多条绕过 SM 的路径打破 status_reason/previous_status 不变量；③ 主状态路径无并发保护；④ 必然 500 的 COS 端点与多处重复实现/表漂移。安全与一致性未达 stable 门槛。

## F. 一句话总结

状态机/事务/上传的基础设计是好的，但"鉴权默认关闭 + 多条绕开状态机的捷径 + 并发无锁 + 双迁移表漂移"让同一套业务规则在不同入口执行力度不一，需先收敛绕过路径与鉴权默认值再谈稳定。
