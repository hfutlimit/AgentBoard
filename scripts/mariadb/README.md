# AgentBoard · MariaDB 脚本

本目录提供 AgentBoard 生产数据库（MariaDB 11）的**独立 SQL 路径**，
与代码内的 Alembic 在线迁移（`migrations/`）互补，构成「离线脚本 + 在线迁移」双路径。

## 文件

| 文件 | 作用 |
|------|------|
| `schema.sql` | 与 `agentboard/models.py` 及 Alembic 迁移完全对齐的建库 / 建表 DDL（utf8mb4、索引、外键、唯一约束）。 |

## 表结构概览

| 表 | 说明 | 关键约束 |
|----|------|----------|
| `projects` | 项目 | `key` 唯一 |
| `epics` | 史诗（隶属 project） | FK → projects |
| `stories` | 故事（隶属 epic） | FK → epics |
| `tasks` | 任务 / Bug（隶属 project，可选隶属 story） | FK → projects / stories；`type` ∈ {task,bug}；`status`/`priority` 枚举；`source_spec_id` 标记由哪个任务 spec 生成 |
| `users` | 注册用户（鉴权） | `username` 唯一 |
| `comments` | 任务评论（隶属 task） | FK → tasks；`ix_comments_task_id` 索引 |

> 枚举取值见 `agentboard/models.py`：`Status` / `Priority` / `ItemType`。
> 状态合法迁移由服务层 `service.TRANSITIONS` 校验，数据库层不限制。

## 用法一：DBA 离线评审

直接阅读 `schema.sql`，确认字符集、索引、外键、唯一约束符合预期。
无需连接数据库。

## 用法二：一次性初始化（推荐 + Alembic 并存）

```bash
# 1) 用有足够权限的账号执行建库脚本
mysql -h <host> -P 3306 -u root -p < schema.sql

# 2) 标记 Alembic 已迁移到 head，避免应用启动时重复建表
export AGENTBOARD_DB_URL="mysql+pymysql://agentboard:agentboard@<host>:3306/agentboard"
alembic upgrade head        # 表已存在，本步为 no-op，但会写入 alembic_version
# 或（等价）：
alembic stamp head
```

## 用法三：仅用本脚本、不跑 Alembic

应用 `init_db()` 逻辑为：优先 `alembic upgrade head`，**失败则自动降级为 `create_all`**
（`agentboard/database.py`）。因此即便表已由 `schema.sql` 建好、未执行 `alembic stamp`，
应用启动时也能正常容错运行（Alembic 因表已存在报错 → 走 create_all 幂等补齐）。
但为保持元数据一致，仍建议执行一次 `alembic stamp head`。

## 与 docker-compose 对接

`docker-compose.yml` 的 `db` 服务已内置：

- `MARIADB_DATABASE=agentboard`
- `MARIADB_USER=agentboard` / `MARIADB_PASSWORD=agentboard`
- 宿主机映射 `13306:3306`（容器内 3306；服务经 `db:3306` 访问）

API 通过环境变量对接：

```yaml
environment:
  AGENTBOARD_DB_URL: "mysql+pymysql://agentboard:agentboard@db:3306/agentboard"
```

本地裸机若用本脚本初始化，请确保账号 `agentboard` 与上方 `AGENTBOARD_DB_URL` 一致。

## 字符集说明

统一 `utf8mb4` / `utf8mb4_unicode_ci`。MariaDB 11 的默认排序规则为
`utf8mb4_uca1400_ai_ci`，与 `unicode_ci` 在绝大多数业务场景下行为一致；
如所在环境 MariaDB 版本较旧（10.x），`unicode_ci` 兼容性更好。
