-- =============================================================
-- AgentBoard — MariaDB 独立建库 / 建表脚本（离线评审 + DBA 初始化）
-- =============================================================
-- 本脚本与 `agentboard/models.py` 及 Alembic 迁移（migrations/versions/*）
-- 完全对齐，是「Alembic 在线迁移」之外的第二条权威路径：
--   - 供 DBA 离线评审表结构（字符集 / 索引 / 外键 / 唯一约束）
--   - 供容器或裸机 MariaDB 做一次性初始化
--
-- 用法：
--   1) 用有足够权限的账号（如 root）执行：
--        mysql -h <host> -P 3306 -u root -p < schema.sql
--   2) 之后让应用以 AGENTBOARD_DB_URL 指向本库即可；应用启动时
--      init_db() 会优先跑 Alembic，若表已存在则自动降级为 create_all，
--      不会重建。也可在执行本脚本后跑 `alembic stamp head` 显式标记已迁移。
--
-- 注意：
--   - 字符集统一 utf8mb4（emoji / 四字节字符安全）。
--   - created_at / updated_at / priority 的默认值由应用层（Python）写入，
--     priority 在此显式 DEFAULT 'medium' 以匹配 Alembic 迁移终态，
--     保证纯 SQL 直插也不缺列。
--   - 级联删除由服务层（service.py）负责，外键仅设 RESTRICT（默认），
--     不在此处加 ON DELETE CASCADE，避免误删。
-- =============================================================

-- 1) 建库（如不存在）
CREATE DATABASE IF NOT EXISTS agentboard
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

-- 2) 建专用应用账号并授权（幂等；容器镜像已建的账号可跳过此段）
CREATE USER IF NOT EXISTS 'agentboard'@'%' IDENTIFIED BY 'agentboard';
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, DROP, INDEX, REFERENCES
  ON agentboard.* TO 'agentboard'@'%';
FLUSH PRIVILEGES;

USE agentboard;

-- 3) 业务表 ------------------------------------------------------

CREATE TABLE IF NOT EXISTS projects (
  id          INT          NOT NULL AUTO_INCREMENT,
  name        VARCHAR(200) NOT NULL,
  `key`       VARCHAR(20)  NULL,
  description TEXT         NOT NULL,
  created_at  DATETIME     NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY `key` (`key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS epics (
  id          INT          NOT NULL AUTO_INCREMENT,
  project_id  INT          NOT NULL,
  title       VARCHAR(300) NOT NULL,
  description TEXT         NOT NULL,
  status      VARCHAR(20)  NOT NULL,
  created_at  DATETIME     NOT NULL,
  PRIMARY KEY (id),
  KEY `ix_epics_project_id` (project_id),
  CONSTRAINT `fk_epics_project`
    FOREIGN KEY (`project_id`) REFERENCES `projects` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS stories (
  id          INT          NOT NULL AUTO_INCREMENT,
  epic_id     INT          NOT NULL,
  title       VARCHAR(300) NOT NULL,
  description TEXT         NOT NULL,
  status      VARCHAR(20)  NOT NULL,
  created_at  DATETIME     NOT NULL,
  PRIMARY KEY (id),
  KEY `ix_stories_epic_id` (epic_id),
  CONSTRAINT `fk_stories_epic`
    FOREIGN KEY (`epic_id`) REFERENCES `epics` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS tasks (
  id              INT          NOT NULL AUTO_INCREMENT,
  project_id      INT          NOT NULL,
  story_id        INT          NULL,
  type            VARCHAR(10)  NOT NULL,
  title           VARCHAR(300) NOT NULL,
  status          VARCHAR(20)  NOT NULL,
  priority        VARCHAR(10)  NOT NULL DEFAULT 'medium',
  description     TEXT         NOT NULL,
  spec            TEXT         NOT NULL,
  source_spec_id  INT          NULL,
  created_at      DATETIME     NOT NULL,
  updated_at      DATETIME     NOT NULL,
  PRIMARY KEY (id),
  KEY `ix_tasks_project_id` (project_id),
  KEY `ix_tasks_story_id` (story_id),
  CONSTRAINT `fk_tasks_project`
    FOREIGN KEY (`project_id`) REFERENCES `projects` (`id`),
  CONSTRAINT `fk_tasks_story`
    FOREIGN KEY (`story_id`) REFERENCES `stories` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS users (
  id            INT          NOT NULL AUTO_INCREMENT,
  username      VARCHAR(64)  NOT NULL,
  password_hash VARCHAR(256) NOT NULL,
  created_at    DATETIME     NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY `username` (`username`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS comments (
  id          INT          NOT NULL AUTO_INCREMENT,
  task_id     INT          NOT NULL,
  author      VARCHAR(100) NOT NULL,
  content     TEXT         NOT NULL,
  created_at  DATETIME     NOT NULL,
  updated_at  DATETIME     NOT NULL,
  PRIMARY KEY (id),
  KEY `ix_comments_task_id` (task_id),
  CONSTRAINT `fk_comments_task`
    FOREIGN KEY (`task_id`) REFERENCES `tasks` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
