# Tasks — 前端 Web 自动化测试（Playwright）

## 1. 依赖
- [ ] `requirements.txt` 增加 `playwright` 与 `pytest-playwright`。

## 2. 测试骨架
- [ ] 新增 `tests/test_playwright_e2e.py`：module 级 fixture 启动真实 API + Web（复用 `_start_server` / `_wait`，临时 SQLite）。
- [ ] 提供 UI 辅助函数 `ui_register / ui_login`（按 `auth` 变更后的 DOM 结构选择器操作）。

## 3. 用例（真实浏览器）
- [ ] 注册 UI 流：填写提交 → 进入应用；断言 `localStorage["agentboard_token"]` 存在。
- [ ] 登录 UI 流 + 错误密码报错 + 重复注册报错（断言错误文案出现）。
- [ ] 项目树 CRUD UI：新建 Project → Epic → Story → Task/Bug，断言 DOM 节点出现。
- [ ] 状态流转 UI：切换状态，断言徽标文本更新。
- [ ] spec 编辑与 markdown 渲染：保存 spec，断言渲染后的元素存在。

## 4. 配置与文档
- [ ] `tests/conftest.py` 或 `playwright` marker；README「测试」节补充：`playwright install chromium` 与运行命令。
- [ ] 本地验证：`PYTHONPATH=. python -m pytest tests/test_playwright_e2e.py -q` 通过（依赖 `auth` 变更先完成前端界面）。
