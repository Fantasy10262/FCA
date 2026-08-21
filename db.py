"""
数据库抽象层 —— 让同一份业务代码同时跑在 SQLite（本地 / 无 DATABASE_URL）
和 PostgreSQL（Supabase 免费 Postgres / 设了 DATABASE_URL）上。

设计目标：
- 本地开发、CI、未配置 DATABASE_URL 的托管平台，仍然零依赖用 SQLite。
- 设了 DATABASE_URL（postgres:// 或 postgresql://）就自动切到 Postgres。
- 业务代码（app.py）不需要关心方言差异：占位符统一用 `?`，本层在 Postgres 下转成 `%s`。

已处理的方言差异：
- `?` 占位符 -> Postgres 的 `%s`
- `INSERT OR IGNORE` -> `INSERT ... ON CONFLICT DO NOTHING`
- `lastrowid`：psycopg2 没有该属性，本层对 INSERT 自动加 `RETURNING id` 并挂在 cursor.lastrowid 上
- 建表语句按方言分别提供（AUTOINCREMENT / SERIAL、datetime('now') / CURRENT_TIMESTAMP）
- `PRAGMA` 与遗留迁移逻辑只在 SQLite 下执行
"""

import os
import sqlite3

# 读取数据库连接串；为空则用 SQLite（默认 data/oj.db）
_DATABASE_URL = os.environ.get("DATABASE_URL", "")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "data", "oj.db")
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)


def is_postgres():
    return _DATABASE_URL.startswith("postgres")


# 方言无关的「唯一/外键冲突」异常：业务代码统一 except IntegrityError
if is_postgres():
    import psycopg2

    IntegrityError = psycopg2.IntegrityError
else:
    IntegrityError = sqlite3.IntegrityError


# --------------------------------------------------------------------------
# SQLite 建表语句（原样保留，仅 SQLite 使用）
# --------------------------------------------------------------------------
SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'student',
    status TEXT NOT NULL DEFAULT 'active',
    market_contact TEXT NOT NULL DEFAULT '',
    market_bio TEXT NOT NULL DEFAULT '',
    market_verified_at TEXT NOT NULL DEFAULT '',
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS problems (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    difficulty TEXT DEFAULT '简单',
    time_limit_ms INTEGER DEFAULT 2000,
    memory_limit_mb INTEGER DEFAULT 256,
    allowed_languages TEXT NOT NULL DEFAULT '["c","cpp","py"]',
    default_language TEXT DEFAULT 'c',
    order_index INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS testcases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    problem_id INTEGER NOT NULL,
    point_id INTEGER,
    input_text TEXT NOT NULL DEFAULT '',
    expected_text TEXT NOT NULL DEFAULT '',
    is_sample INTEGER DEFAULT 0,
    FOREIGN KEY (problem_id) REFERENCES problems(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS test_points (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    problem_id INTEGER NOT NULL,
    name TEXT NOT NULL DEFAULT '测试点',
    score INTEGER DEFAULT 1,
    order_index INTEGER DEFAULT 0,
    FOREIGN KEY (problem_id) REFERENCES problems(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS submissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    problem_id INTEGER NOT NULL,
    language TEXT NOT NULL,
    code TEXT NOT NULL,
    status TEXT NOT NULL,
    passed INTEGER DEFAULT 0,
    total INTEGER DEFAULT 0,
    max_runtime_ms INTEGER,
    compile_error TEXT DEFAULT '',
    results_json TEXT DEFAULT '',
    submitted_at TEXT DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (user_id) REFERENCES users(id),
    FOREIGN KEY (problem_id) REFERENCES problems(id)
);
CREATE INDEX IF NOT EXISTS idx_sub_user ON submissions(user_id);
CREATE INDEX IF NOT EXISTS idx_sub_prob ON submissions(problem_id);

CREATE TABLE IF NOT EXISTS problem_sets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS problem_set_items (
    set_id INTEGER NOT NULL,
    problem_id INTEGER NOT NULL,
    order_index INTEGER DEFAULT 0,
    PRIMARY KEY (set_id, problem_id),
    FOREIGN KEY (set_id) REFERENCES problem_sets(id) ON DELETE CASCADE,
    FOREIGN KEY (problem_id) REFERENCES problems(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_psi_set ON problem_set_items(set_id);
CREATE INDEX IF NOT EXISTS idx_psi_prob ON problem_set_items(problem_id);

CREATE TABLE IF NOT EXISTS learn_languages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    tag TEXT NOT NULL DEFAULT '',
    intro TEXT NOT NULL DEFAULT '',
    roadmap TEXT NOT NULL DEFAULT '',
    order_index INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS learn_videos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    language_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    author TEXT NOT NULL DEFAULT '',
    embed TEXT NOT NULL,
    order_index INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (language_id) REFERENCES learn_languages(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_lv_lang ON learn_videos(language_id);

CREATE TABLE IF NOT EXISTS market_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT '其他',
    price TEXT NOT NULL DEFAULT '0',
    contact TEXT NOT NULL DEFAULT '',
    pay_qr TEXT NOT NULL DEFAULT '',
    images TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'pending',
    reject_reason TEXT NOT NULL DEFAULT '',
    created_at TEXT DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (user_id) REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS idx_mi_user ON market_items(user_id);
CREATE INDEX IF NOT EXISTS idx_mi_status ON market_items(status);

CREATE TABLE IF NOT EXISTS market_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL,
    buyer_id INTEGER NOT NULL,
    seller_id INTEGER NOT NULL,
    price TEXT NOT NULL DEFAULT '0',
    status TEXT NOT NULL DEFAULT 'pending',
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT DEFAULT (datetime('now','localtime')),
    paid_at TEXT DEFAULT '',
    delivered_at TEXT DEFAULT '',
    completed_at TEXT DEFAULT '',
    cancelled_at TEXT DEFAULT '',
    FOREIGN KEY (item_id) REFERENCES market_items(id),
    FOREIGN KEY (buyer_id) REFERENCES users(id),
    FOREIGN KEY (seller_id) REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS idx_mo_buyer ON market_orders(buyer_id);
CREATE INDEX IF NOT EXISTS idx_mo_seller ON market_orders(seller_id);
CREATE INDEX IF NOT EXISTS idx_mo_item ON market_orders(item_id);

CREATE TABLE IF NOT EXISTS market_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL,
    reviewer_id INTEGER NOT NULL,
    reviewee_id INTEGER NOT NULL,
    rating INTEGER NOT NULL DEFAULT 0,
    comment TEXT NOT NULL DEFAULT '',
    created_at TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE (order_id, reviewer_id),
    FOREIGN KEY (order_id) REFERENCES market_orders(id),
    FOREIGN KEY (reviewer_id) REFERENCES users(id),
    FOREIGN KEY (reviewee_id) REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS idx_mr_reviewee ON market_reviews(reviewee_id);

CREATE TABLE IF NOT EXISTS market_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_type TEXT NOT NULL,
    target_id INTEGER NOT NULL,
    reporter_id INTEGER NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE (target_type, target_id, reporter_id),
    FOREIGN KEY (reporter_id) REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS idx_mrp_status ON market_reports(status);
"""

# --------------------------------------------------------------------------
# PostgreSQL 建表语句（Supabase 使用）
# --------------------------------------------------------------------------
PG_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    student_id TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'student',
    status TEXT NOT NULL DEFAULT 'active',
    market_contact TEXT NOT NULL DEFAULT '',
    market_bio TEXT NOT NULL DEFAULT '',
    market_verified_at TEXT NOT NULL DEFAULT '',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS problems (
    id SERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    difficulty TEXT DEFAULT '简单',
    time_limit_ms INTEGER DEFAULT 2000,
    memory_limit_mb INTEGER DEFAULT 256,
    allowed_languages TEXT NOT NULL DEFAULT '["c","cpp","py"]',
    default_language TEXT DEFAULT 'c',
    order_index INTEGER DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS testcases (
    id SERIAL PRIMARY KEY,
    problem_id INTEGER NOT NULL,
    point_id INTEGER,
    input_text TEXT NOT NULL DEFAULT '',
    expected_text TEXT NOT NULL DEFAULT '',
    is_sample INTEGER DEFAULT 0,
    FOREIGN KEY (problem_id) REFERENCES problems(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS test_points (
    id SERIAL PRIMARY KEY,
    problem_id INTEGER NOT NULL,
    name TEXT NOT NULL DEFAULT '测试点',
    score INTEGER DEFAULT 1,
    order_index INTEGER DEFAULT 0,
    FOREIGN KEY (problem_id) REFERENCES problems(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS submissions (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    problem_id INTEGER NOT NULL,
    language TEXT NOT NULL,
    code TEXT NOT NULL,
    status TEXT NOT NULL,
    passed INTEGER DEFAULT 0,
    total INTEGER DEFAULT 0,
    max_runtime_ms INTEGER,
    compile_error TEXT DEFAULT '',
    results_json TEXT DEFAULT '',
    submitted_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id),
    FOREIGN KEY (problem_id) REFERENCES problems(id)
);
CREATE INDEX IF NOT EXISTS idx_sub_user ON submissions(user_id);
CREATE INDEX IF NOT EXISTS idx_sub_prob ON submissions(problem_id);

CREATE TABLE IF NOT EXISTS problem_sets (
    id SERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS problem_set_items (
    set_id INTEGER NOT NULL,
    problem_id INTEGER NOT NULL,
    order_index INTEGER DEFAULT 0,
    PRIMARY KEY (set_id, problem_id),
    FOREIGN KEY (set_id) REFERENCES problem_sets(id) ON DELETE CASCADE,
    FOREIGN KEY (problem_id) REFERENCES problems(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_psi_set ON problem_set_items(set_id);
CREATE INDEX IF NOT EXISTS idx_psi_prob ON problem_set_items(problem_id);

CREATE TABLE IF NOT EXISTS learn_languages (
    id SERIAL PRIMARY KEY,
    code TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    tag TEXT NOT NULL DEFAULT '',
    intro TEXT NOT NULL DEFAULT '',
    roadmap TEXT NOT NULL DEFAULT '',
    order_index INTEGER DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS learn_videos (
    id SERIAL PRIMARY KEY,
    language_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    author TEXT NOT NULL DEFAULT '',
    embed TEXT NOT NULL,
    order_index INTEGER DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (language_id) REFERENCES learn_languages(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_lv_lang ON learn_videos(language_id);

CREATE TABLE IF NOT EXISTS market_items (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT '其他',
    price TEXT NOT NULL DEFAULT '0',
    contact TEXT NOT NULL DEFAULT '',
    pay_qr TEXT NOT NULL DEFAULT '',
    images TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'pending',
    reject_reason TEXT NOT NULL DEFAULT '',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS idx_mi_user ON market_items(user_id);
CREATE INDEX IF NOT EXISTS idx_mi_status ON market_items(status);

CREATE TABLE IF NOT EXISTS market_orders (
    id SERIAL PRIMARY KEY,
    item_id INTEGER NOT NULL,
    buyer_id INTEGER NOT NULL,
    seller_id INTEGER NOT NULL,
    price TEXT NOT NULL DEFAULT '0',
    status TEXT NOT NULL DEFAULT 'pending',
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    paid_at TEXT DEFAULT '',
    delivered_at TEXT DEFAULT '',
    completed_at TEXT DEFAULT '',
    cancelled_at TEXT DEFAULT '',
    FOREIGN KEY (item_id) REFERENCES market_items(id),
    FOREIGN KEY (buyer_id) REFERENCES users(id),
    FOREIGN KEY (seller_id) REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS idx_mo_buyer ON market_orders(buyer_id);
CREATE INDEX IF NOT EXISTS idx_mo_seller ON market_orders(seller_id);
CREATE INDEX IF NOT EXISTS idx_mo_item ON market_orders(item_id);

CREATE TABLE IF NOT EXISTS market_reviews (
    id SERIAL PRIMARY KEY,
    order_id INTEGER NOT NULL,
    reviewer_id INTEGER NOT NULL,
    reviewee_id INTEGER NOT NULL,
    rating INTEGER NOT NULL DEFAULT 0,
    comment TEXT NOT NULL DEFAULT '',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (order_id, reviewer_id),
    FOREIGN KEY (order_id) REFERENCES market_orders(id),
    FOREIGN KEY (reviewer_id) REFERENCES users(id),
    FOREIGN KEY (reviewee_id) REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS idx_mr_reviewee ON market_reviews(reviewee_id);

CREATE TABLE IF NOT EXISTS market_reports (
    id SERIAL PRIMARY KEY,
    target_type TEXT NOT NULL,
    target_id INTEGER NOT NULL,
    reporter_id INTEGER NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (target_type, target_id, reporter_id),
    FOREIGN KEY (reporter_id) REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS idx_mrp_status ON market_reports(status);
"""

# 复合主键、无单独 id 列的表（INSERT 时不要自动追加 RETURNING id）
_NO_ID_TABLES = ("problem_set_items",)


def _fix_sql(sql):
    """把 SQLite 风格 SQL 转成 Postgres 可接受的形式。"""
    sql = sql.replace("?", "%s")
    if "INSERT OR IGNORE" in sql:
        sql = sql.replace("INSERT OR IGNORE", "INSERT")
        if "ON CONFLICT" not in sql:
            sql += " ON CONFLICT DO NOTHING"
    return sql


class _PGCursor:
    """包装 psycopg2 原生 cursor：补上业务代码依赖的 lastrowid，
    其余属性/方法全部委托给真实 cursor。

    为什么需要包装：psycopg2 的 Cursor 类带 __slots__，不能动态添加
    lastrowid 属性（会抛 AttributeError）。用一个普通的包装对象承载
    lastrowid，业务代码读写 cur.lastrowid 就不会踩这个坑。
    """

    def __init__(self, cur, lastrowid=None):
        self._cur = cur
        self.lastrowid = lastrowid

    def __getattr__(self, name):
        # 未显式定义的属性（description / rowcount / close / ...）委托真实 cursor
        return getattr(self._cur, name)

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    def __iter__(self):
        return iter(self._cur)


class _PGConn:
    """对 psycopg2 连接的轻封装，抹平与 sqlite3 的差异。

    关键点：整条连接复用**同一个游标**（self._cur），不要每条 execute
    都新建 cursor。原因：Supabase 用的是 pgbouncer **事务池**（Transaction
    pooler，端口 6543），在该模式下若在一个已开事务里新建第二个 cursor 跑
    INSERT...RETURNING 再 fetchone，会偶发「no results to fetch」（RETURNING
    结果丢失）。单个游标顺序执行可稳定规避此问题。业务代码都是
    fetchone/fetchall 后立即发下一句，不存在同时持有多游标的情况，故安全。
    """

    def __init__(self, conn):
        self._conn = conn
        self._cur = conn.cursor()

    # --- 方言修正后的执行入口 ---
    def execute(self, sql, params=()):
        sql = _fix_sql(sql)
        auto_returning = (
            sql.lstrip().upper().startswith("INSERT")
            and "RETURNING" not in sql
            and "problem_set_items" not in sql
        )
        # 关键：Postgres 没有 lastrowid，业务代码又依赖它拿自增主键，
        # 故对普通 INSERT 自动追加 RETURNING id，再从结果里取 id。
        if auto_returning:
            sql += " RETURNING id"
        self._cur.execute(sql, params)
        # 包装一层，让 psycopg2 也能用 .lastrowid
        if auto_returning:
            row = self._cur.fetchone()
            return _PGCursor(self._cur, row["id"] if row else None)
        return _PGCursor(self._cur, None)

    def executemany(self, sql, params_seq):
        sql = _fix_sql(sql)
        self._cur.executemany(sql, params_seq)
        return _PGCursor(self._cur, None)

    def executescript(self, sql):
        # Postgres 不支持一次性执行多语句，按分号拆分逐条执行（复用同一游标）
        for stmt in sql.split(";"):
            stmt = stmt.strip()
            if stmt:
                self._cur.execute(_fix_sql(stmt))
        return None

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()


def connect():
    """返回数据库连接：Postgres 走 _PGConn 封装，否则返回 sqlite3 连接。"""
    if is_postgres():
        import psycopg2
        from psycopg2.extras import RealDictCursor

        conn = psycopg2.connect(_DATABASE_URL, cursor_factory=RealDictCursor)
        return _PGConn(conn)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn
