# Automation Memory: AgentBoard 自动开发 [GLM-5.2] 23:00

## 2026-07-15 23:00 执行记录

### 完成项
- **B-03 Task due_date 功能**（Backlog B 第 3 项）
  - 后端：service.py `_parse_due_date()` 修复 SQLite Date 类型错误；api.py 增加 due_date 到 TaskIn/TaskPatch；update_task 改用 exclude_unset=True
  - 前端：Angular Task 接口+创建弹窗+编辑表单+列表/看板/详情徽章（逾期红脉冲/近期黄/正常灰）
  - 测试：5 项 pytest 全绿
  - commit c8b7983，push 成功

### 踩坑
- Docker API 容器代码与宿主机不同步：需 force-recreate 获取原始代码，再 docker cp 注入修改
- 容器原 api.py 缺少 due_date 字段 → 用 Python 脚本在容器内 patch
- MariaDB 迁移标记 applied 但列实际缺失 → 手动 ALTER TABLE 添加
- web_app.py 容器版本缺少 /static/ 路径重写 → docker cp 更新

### 未完成
- Playwright 截图验证（时间不足，API E2E 已通过 curl 验证）
- 现有测试套件回归（test_crud_smoke 端口冲突 pre-existing issue）
