"""
合并迁移：将本地 SQLite（data/agentboard.db）中的数据并入运行中的 MariaDB。
- 两遍处理：Pass A 逐表重新分配主键 ID（从目标库当前 MAX(id)+1 起）并建立 old->new 映射；Pass B 按映射改写外键后写入。
- 不依赖拓扑排序：Pass A 先为所有表建好映射，Pass B 再统一改写；写入时关闭 FK 检查以规避插入顺序/外键环问题。
- 运行环境：agentboard-api 容器内（含 pymysql/sqlite3），源库以只读挂载。
"""
import sqlite3, pymysql, sys

MODE = sys.argv[1] if len(sys.argv) > 1 else "dry"
SRC_PATH = "/src_sqlite/agentboard.db"
DST_HOST, DST_USER, DST_PWD, DST_DB = "db", "agentboard", "agentboard", "agentboard"

src = sqlite3.connect(SRC_PATH)
src.row_factory = sqlite3.Row
dst = pymysql.connect(host=DST_HOST, user=DST_USER, password=DST_PWD, database=DST_DB, autocommit=False)
dcur = dst.cursor()


def src_tables():
    cur = src.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name NOT IN ('alembic_version')")
    return [r[0] for r in cur.fetchall()]


def pk_cols(t):
    cur = src.cursor()
    cur.execute(f"PRAGMA table_info('{t}')")
    return [c['name'] for c in cur.fetchall() if c['pk']]


def fks(t):
    cur = src.cursor()
    cur.execute(f"PRAGMA foreign_key_list('{t}')")
    return [(c['from'], c['table'], c['to']) for c in cur.fetchall()]


def dst_columns(t):
    dcur.execute("SELECT COLUMN_NAME FROM information_schema.columns WHERE table_schema=%s AND table_name=%s", (DST_DB, t))
    return set(r[0] for r in dcur.fetchall())


tables = src_tables()
print("SOURCE TABLES:", tables)

maps = {}          # table -> {old_pk: new_pk}
plans = {}         # table -> (valid_cols, [vals_dict,...])

# ---------- Pass A: 分配新 ID + 建映射 ----------
for t in tables:
    pks = pk_cols(t)
    if len(pks) != 1 or pks[0] != 'id':
        print(f"SKIP {t}: 非单 id 主键 ({pks})")
        continue
    dcols = dst_columns(t)
    cur = src.cursor()
    cur.execute(f"SELECT * FROM {t}")
    rows = cur.fetchall()
    colnames = [c[0] for c in cur.description]
    valid_cols = [c for c in colnames if c in dcols]
    if len(valid_cols) < len(colnames):
        print(f"  {t}: 源列 {set(colnames)-dcols} 在目标库不存在，忽略")
    try:
        dcur.execute(f"SELECT MAX(id) FROM {t}")
        mx = dcur.fetchone()[0]
        base = (mx or 0) + 1
    except Exception:
        base = 1
    tmap = {row['id']: base + i for i, row in enumerate(rows)}
    maps[t] = tmap
    plans[t] = (valid_cols, rows)
    print(f"  {t}: {len(rows)} 行 -> 新 id 从 {base} 起")

# ---------- Pass B: 改写外键 ----------
for t in list(plans.keys()):
    valid_cols, rows = plans[t]
    fk_list = fks(t)
    new_rows = []
    for row in rows:
        vals = {}
        for c in valid_cols:
            v = row[c]
            matched_fk = next((ref_t for (fc, ref_t, ref_c) in fk_list if fc == c and ref_c == 'id'), None)
            if matched_fk is not None:
                if v is None:
                    vals[c] = None
                else:
                    if matched_fk not in maps:
                        raise SystemExit(f"FK 引用表 {matched_fk} 未迁移")
                    nv = maps[matched_fk].get(v)
                    if nv is None:
                        raise SystemExit(f"{t}.{c}={v} 引用的 {matched_fk} 记录不在源库，无法映射")
                    vals[c] = nv
            else:
                vals[c] = v
        vals['id'] = maps[t][row['id']]
        new_rows.append(vals)
    plans[t] = (valid_cols, new_rows)

if MODE == 'dry':
    print("DRY RUN 完成，未写入任何数据。")
    src.close(); dst.close()
    sys.exit(0)

# ---------- APPLY ----------
dcur.execute("SET FOREIGN_KEY_CHECKS=0")
for t in list(plans.keys()):
    valid_cols, new_rows = plans[t]
    if not new_rows:
        continue
    col_sql = ", ".join(f"`{c}`" for c in valid_cols)
    placeholders = ", ".join(["%s"] * len(valid_cols))
    for vals in new_rows:
        dcur.execute(f"INSERT INTO `{t}` ({col_sql}) VALUES ({placeholders})", [vals[c] for c in valid_cols])
    print(f"INSERTED {t}: {len(new_rows)} 行")
dcur.execute("SET FOREIGN_KEY_CHECKS=1")
dst.commit()
print("MIGRATION APPLIED")
src.close(); dst.close()
