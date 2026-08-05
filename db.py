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


class _PGConn:
    """对 psycopg2 连接的轻封装，抹平与 sqlite3 的差异。"""

    def __init__(self, conn):
        self._conn = conn

    # --- 方言修正后的执行入口 ---
    def execute(self, sql, params=()):
        sql = _fix_sql(sql)
        auto_returning = (
            sql.lstrip().upper().startswith("INSERT")
            and "RETURNING" not in sql
            and "problem_set_items" not in sql
        )
        if auto_returning:
            sql += " RETURNING id"
        cur = self._conn.cursor()
        cur.execute(sql, params)
        # 让 psycopg2 的 cursor 也能用 .lastrowid（业务代码依赖它拿自增主键）
        if auto_returning:
            row = cur.fetchone()
            cur.lastrowid = row["id"] if row else None
        else:
            cur.lastrowid = None
        return cur

    def executemany(self, sql, params_seq):
        sql = _fix_sql(sql)
        cur = self._conn.cursor()
        cur.executemany(sql, params_seq)
        cur.lastrowid = None
        return cur

    def executescript(self, sql):
        # Postgres 不支持一次性执行多语句，按分号拆分逐条执行
        for stmt in sql.split(";"):
            stmt = stmt.strip()
            if stmt:
                self._conn.cursor().execute(_fix_sql(stmt))
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
