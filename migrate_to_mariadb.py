"""
一次性迁移脚本：将本地 SQLite（agentboard.db）数据迁移到 MariaDB。

运行环境：主机 Python（需 sqlalchemy + pymysql），PYTHONPATH 指向项目根。
目标：mysql+pymysql://agentboard:agentboard@127.0.0.1:13306/agentboard

步骤：
  1) 用 ORM metadata 在 MariaDB 建表（create_all）
  2) 按外键安全顺序（projects→users→epics→stories→tasks→comments）保留主键迁移数据
  3) alembic stamp head，使后续 init_db 的 Alembic 路径为干净 no-op
"""
import os
from sqlalchemy import create_engine, inspect, Text, String
from sqlalchemy.orm import sessionmaker
from agentboard import models

HERE = r"E:/Projects/WorkBuddy/AgentBoard"
SRC = f"sqlite:///{HERE}/agentboard.db"
DST = "mysql+pymysql://agentboard:agentboard@127.0.0.1:13306/agentboard"

src = create_engine(SRC, future=True)
dst = create_engine(DST, future=True)

# 1) 建表
models.Base.metadata.create_all(dst)
print("TABLES_CREATED on MariaDB")

Src = sessionmaker(bind=src, future=True)
Dst = sessionmaker(bind=dst, future=True)

order = [models.Project, models.User, models.Epic, models.Story, models.Task, models.Comment]

with Src() as s, Dst() as d:
    for cls in order:
        rows = s.query(cls).all()
        for r in rows:
            data = {}
            for c in inspect(cls).columns:
                v = getattr(r, c.name)
                # MariaDB 对 NOT NULL 文本列不接受 NULL；源库偶有遗留 NULL 时兜底为空串
                if v is None and isinstance(c.type, (Text, String)) and not c.nullable:
                    v = ""
                data[c.name] = v
            d.add(cls(**data))
        d.flush()
        print(f"  {cls.__tablename__}: {len(rows)} rows migrated")
    d.commit()
print("DATA_MIGRATED")

# 2) 对齐 Alembic 版本，避免后续 init_db 重复建表
try:
    from alembic.config import Config
    from alembic import command
    cfg = Config(os.path.join(HERE, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(HERE, "migrations"))
    cfg.set_main_option("sqlalchemy.url", DST)
    command.stamp(cfg, "head")
    print("ALEMBIC_STAMPED_HEAD")
except Exception as e:
    print("ALEMBIC_STAMP_SKIPPED:", repr(e))

print("MIGRATION_DONE")
