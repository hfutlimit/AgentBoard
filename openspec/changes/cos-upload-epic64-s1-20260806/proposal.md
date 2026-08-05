# Change: Epic 64 S1 腾讯云 COS 上传后端（Task 991 → in_review，2026-08-06）

## Why

AgentBoard 项目（项目 3）中唯一未完成的高优先级 Epic 为 **Epic 64（腾讯云 COS 图片上传与图片展示）**，当前 4 个 Story（61/62/63/64）全部 backlog。本次交付其地基 Story **61：腾讯云 COS 上传后端（配置占位 + 优雅降级）**，为后续 S2 文档图片 / S3 评论图片 / S4 Story/Epic 描述图片提供可复用的上传能力与图片 URL。

现状：现有附件能力（`/api/tasks/{tid}/attachments`）为**本地文件存储**，无法满足「多成员/多 Agent 协作贴图」与「外部可访问 URL」需求；`requirements.txt` 无 COS 相关依赖。

## 方案对比

| 方案 | 说明 | 取舍 |
|---|---|---|
| A. 官方 SDK `cos-python-sdk-v5` | 腾讯云官方 SDK，成熟但引入 ~10 个传递依赖 | Docker api 容器需 `pip install`；dist 打包需同步 requirements；双后端部署成本高 |
| **B. 纯标准库自实现 COS V5 签名**（选定） | 腾讯云 COS V5 签名算法是公开的 HMAC-SHA1（RFC 2104），用 `hmac/hashlib/urllib.request` 即可实现 PUT 直传与预签名 URL | **零新增依赖**，SQLite/MariaDB/Windows IIS/dist 全环境免安装；算法自洽性用固定密钥回归测试保证 |
| C. 预签名 URL 前端直传 | 前端直接 PUT 到 COS，服务端只签发 | 绕过了服务端鉴权/审计；前端需处理签名逻辑，复杂 | 

选定 **方案 B（服务端直传 + 预签名下载 URL）**：`POST /api/projects/{pid}/cos/upload` 收图片 → 服务端 PUT 至 COS → 返回预签名 GET URL（24h 有效）供文档/评论/描述 markdown 引用。

## 设计要点

1. **配置占位**：`COS_SECRET_ID` / `COS_SECRET_KEY` / `COS_BUCKET` / `COS_REGION` 环境变量；未配置时 `is_configured()=False`。
2. **优雅降级**：未配置 → 上传端点返回 503 `{"detail": "COS not configured: ..."}`，`GET /api/projects/{pid}/cos/config` 返回 `{"configured": false}`，不阻断其余功能。
3. **权限**：端点挂在 `/api/projects/{pid}/cos/...` 下，`project_access_middleware` 的 `_resolve_project_id_from_request` 以 `^/api/projects/(\d+)` 直接解析，成员/管理员读写校验自动生效。
4. **对象 key**：`uploads/{pid}/{uuid4().hex}.{ext}`，按项目隔离。
5. **零 REST 契约破坏**：仅新增端点；不动现有 models.py / service.py 契约。

## 验收

- [ ] 新增 `agentboard/cos_client.py`（COS V5 签名 + put_object + presigned GET URL，纯标准库）
- [ ] `POST /api/projects/{pid}/cos/upload` 返回 `{key, url, size, content_type, original_name, cos_configured}`
- [ ] 未配置 COS 环境变量时上传返回 503、config 返回 configured:false，其余端点不受影响
- [ ] 新增 `tests/test_cos_upload.py`（签名格式/URL 构造/降级/404/422/权限 403）通过
- [ ] E2E 冒烟 0 控制台报错
- [ ] MCP 状态：Task 991 → in_review
