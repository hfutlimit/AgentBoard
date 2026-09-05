# Worker 的 Design 文档交付

Design 执行结果必须包含 `design_document: { title, content }`。`content` 是完整 Markdown
设计正文，包含范围、设计决策、契约、风险和验收条件，并与提交的仓库设计文件一致；仅有摘要或路径不能提交评审。
该要求属于 Worker 固定执行协议，不需要用户在配置页填写额外提示词。

Worker 先保存模型结果，再调用既有文档 API 创建 `design / in_review` 文档，关联项目、Epic 和 Story，
回读全文校验，在 Task 评论留下可点击链接并回读，最后通过带租约的 completion 接口提交结果。
结果中的 `design_document_id`、`design_document_url` 和正文进入后续 Review / Dev 的上游证据。
讨论轮次不创建文档。文档评审状态目前独立于 Task 状态，不自动宣告文档通过评审。

发布或评论请求失败时，Worker 保留已有结果重试，不因网络错误重新调用模型。使用工作 ID 与内容摘要标记
查找已创建文档，处理创建成功但响应丢失的重试；每轮修订是独立交付，不覆盖人工编辑。
发现重复标记或已修改正文时停止提交并保留结果，需人工核对。

现有文档 API 与任务 completion 不在同一事务，也没有服务端唯一幂等键。因此租约失效等跨 Worker
竞争仍可能留下未被任务接受的待评审文档；不能将文档存在视作任务已提交成功。常规单 Worker 重试已有去重保护。
服务端 API 无变更。更新并重启执行 Worker 后，对新开始的 Design 生效；此前已完成的任务需单独补文档。
