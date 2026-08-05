# Tasks: 腾讯云 COS 上传后端（Epic 64 S1）

## Task 991（highest，本 change）

- [x] 创建 OpenSpec change（proposal / design / tasks）
- [ ] `agentboard/cos_client.py`：CosClient（env 配置 + is_configured + _sign + put_object + presigned_get_url + CosError）
- [ ] `agentboard/api.py`：`GET /api/projects/{pid}/cos/config` + `POST /api/projects/{pid}/cos/upload`
- [ ] `tests/test_cos_upload.py`：签名自洽 / URL 构造 / 未配置降级 503 / 项目 404 / 超大 422 / REQUIRe_AUTH 非成员 403 / 成功上传 201（mock urlopen）
- [ ] 本地回归（pytest 目标文件 + 冒烟）
- [ ] 部署：docker cp 注入 api 容器 + restart；curl 冒烟（config 未配置降级）
- [ ] Playwright E2E 冒烟（登录 + 项目视图 0 报错）
- [ ] MCP：Task 991 → in_review；push origin main
