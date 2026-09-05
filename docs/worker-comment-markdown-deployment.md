# 执行评论 Markdown 修复：Windows Server 部署

## 生效范围

- 前端将历史 Worker JSON 执行评论转为 Markdown 后渲染。Task、Story、Epic、抽屉和旧详情入口都使用同一转换。
- 普通评论、评审讨论、用户粘贴的 JSON 示例保持原样；原始数据库评论不作批量修改。
- FastAPI 的 Worker 完成接口将新的 Design / Dev / QA 评论保存为 Markdown。
- WorkerWork.result 仍保存完整结构化 JSON，任务状态、租约、复核和重试协议不变。
- 无数据库迁移、无新依赖；本机 Worker 不需要更新。

## 手动部署

通过 MSTSC 进入服务器。先备份当前 IIS 静态目录和实际运行的 FastAPI 程序目录。

1. **前端**：把部署包 `web` 内的文件复制到 IIS 站点当前物理路径，先复制 JS/CSS 等资源，最后替换 `index.html`。
   保留服务器的 `web.config`、API 地址配置及其他服务器专属文件；包中不含这些配置。
   如果修改过旧 `index.html` 注入 API 地址，保留同等配置（默认使用同源 `/api`）。旧 hash 资源可先保留，避免打开中的页面失效。
2. **后端**：更新服务器实际运行的 FastAPI 源码到本次 main 提交，随后重启对应 API 服务/进程。
   若使用源码增量包，将 `backend-source/agentboard` 覆盖到实际 Python 包的 `agentboard` 目录。
   增量包用于已支持 `/api/worker-work` 的当前版本；服务目录明显落后时，使用完整 main 源码部署。
   服务名以服务器配置为准；仓库默认部署脚本使用 `AgentBoard-WebAPI`，不要重装服务或覆盖 `.env`。
3. 浏览器 Ctrl+F5 打开 `/project/3/tasks/1709`：旧评论 #1098 应出现“QA结果 · 提交评审”，
   并有“验收是否通过 / 部署记录 / 测试结果 / 问题与阻塞”等段落；讨论评论 #1099 仍正常。
4. 下一次执行结果提交后，`GET /api/tasks/{id}/comments` 的新执行评论应直接以 `###` 开头，
   Worker 的结构化结果接口仍返回原 JSON。历史评论 API 仍可返回原 JSON，前端负责兼容显示。

只更新前端即可修复页面中的历史与新增 JSON 展示；同时更新后端才能让 API 新评论也保存成 Markdown。
本包包含 main 上此前的 Epic 排序筛选等前端变更，不是仅替换一个独立前端文件。

## 验证

- Python 格式化与 Worker 协议回归：24 passed；包括 completion 重放不会重复评论，WorkerWork.result 保持完整。
- 前端全量：85 passed / 1 skipped；追加真实 Markdown 渲染与 HTML 安全测试后，App 测试 64 passed / 1 skipped。
- Angular 生产构建通过；存在既有 initial bundle budget 警告。
- 线上部署由用户手动执行；以上不代表线上已更新。
