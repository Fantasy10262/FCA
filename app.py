"""
PTA 风格在线判题平台（Flask + SQLite）
- 学生端：题目列表 / 答题提交 / 判题结果 / 我的提交
- 管理端：题目增删改 + 测试用例管理 + 批量导入学生(CSV) + 批量导入题目(JSON)
- 判题：调用 judge.py，支持 C / C++ / Python，默认 C
"""
import os
import re
import json
import time
import gzip
import threading
from functools import wraps

from db import connect, is_postgres, PG_SCHEMA, SQLITE_SCHEMA, IntegrityError

from flask import (
    Flask, request, session, redirect, url_for, render_template, g, flash,
    send_file,
)
from werkzeug.security import generate_password_hash, check_password_hash
from flask_wtf.csrf import CSRFProtect, CSRFError

import judge
# PTA 一键导入：抓取逻辑复用 pta_import.scrape_problem_set
from pta_import import scrape_problem_set, parse_curl_auth

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "data", "oj.db")
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

app = Flask(__name__)
app.secret_key = os.environ.get("OJ_SECRET", "change-me-in-production-oj-secret")
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024  # 8MB 上传上限
# 静态资源强缓存 1 年：模板引用均带 ?v=<文件mtime>，内容变更自动换 URL 失效旧缓存。
# 收益：Monaco 编辑器等数百个静态文件二次访问直接走浏览器缓存，跨境不再重复下载。
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 31536000

# CSRF 防护：所有 POST/PUT/DELETE 请求必须携带 csrf_token。
# 令牌由 base.html 的 <meta name="csrf-token"> 提供，app.js 在表单提交时自动注入。
csrf = CSRFProtect(app)


@app.errorhandler(CSRFError)
def _csrf_error(e):
    """CSRF 校验失败（通常因页面停留过久、会话令牌过期）时给友好提示，而非裸 400。"""
    flash("安全校验未通过（页面可能已过期），请返回重试", "danger")
    return redirect(request.referrer or url_for("login"))


# 静态资源缓存：全部永久缓存（immutable）。CSS/JS 引用均带 ?v=<mtime>，
# 文件一变 URL 即变，浏览器自动拉新版，不会出现「改了还是旧样式」。
# 收益：Monaco 等数百个静态文件二次访问零网络请求，跨境页面秒开。
@app.after_request
def _cache_headers(resp):
    p = request.path
    if p.startswith("/static/"):
        resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    else:
        resp.headers["Cache-Control"] = "no-store"
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    return resp


@app.context_processor
def _static_versions():
    """给模板注入 style.css/app.js 的最近修改时间戳作为版本号，配合 HTML ?v= 强绕过浏览器缓存。"""
    base = app.static_folder
    def _mtime(name):
        try:
            return int(os.path.getmtime(os.path.join(base, name)))
        except OSError:
            return 0
    return {"static_v": {"style": _mtime("style.css"), "appjs": _mtime("app.js")}}


# 启动时为当前进程补充常见 MinGW-w64 安装目录到 PATH，
# 这样只要在本机任意标准位置装好 gcc/g++，网站无需改代码即可自动识别。
def _augment_compiler_path():
    extra = [
        "C:/mingw64/bin",
        "C:/WinLibs/bin",
        "C:/msys64/mingw64/bin",
        "C:/cygwin64/bin",
        "C:/Program Files (x86)/Dev-Cpp/MinGW64/bin",
        "C:/Program Files/Dev-Cpp/MinGW64/bin",
        "C:/Program Files/CodeBlocks/MinGW/bin",
        "C:/Program Files (x86)/CodeBlocks/MinGW/bin",
    ]
    import glob
    for pat in ["C:/Program Files/mingw-w64/*/bin",
                "C:/Program Files (x86)/mingw-w64/*/bin",
                "C:/Program Files/WinLibs/*/bin",
                "C:/WinLibs/*/bin"]:
        extra.extend(glob.glob(pat))
    existing = os.environ.get("PATH", "").split(os.pathsep)
    added = [d for d in extra if os.path.isdir(d) and d not in existing]
    if added:
        os.environ["PATH"] = os.pathsep.join(added + existing)


_augment_compiler_path()


@app.after_request
def _gzip_response(resp):
    """对文本类响应做 gzip 压缩：跨境链路下 HTML/CSS/JS 体积减约 70%，点击明显变快。"""
    if resp.status_code < 200 or resp.status_code >= 300 or resp.direct_passthrough:
        return resp
    mimetype = resp.mimetype or ""
    if not (mimetype.startswith("text/") or mimetype in (
            "application/json", "application/javascript", "image/svg+xml")):
        return resp
    if "gzip" not in (request.headers.get("Accept-Encoding") or ""):
        return resp
    if "Content-Encoding" in resp.headers:
        return resp
    data = resp.get_data()
    if len(data) < 300:  # 太小不值得压
        return resp
    resp.set_data(gzip.compress(data, 6))
    resp.headers["Content-Encoding"] = "gzip"
    resp.headers["Content-Length"] = str(len(resp.get_data()))
    resp.headers.add("Vary", "Accept-Encoding")
    return resp


# ----------------------------- 数据库 -----------------------------
# 连接跨请求复用（按线程各持一条）：省掉每页一次 TCP+TLS 握手到 Supabase 的
# 200~400ms。线程本地存储保证并发安全（每线程独立连接）。
_db_tls = threading.local()


def _db_drop():
    db = getattr(_db_tls, "db", None)
    if db is not None:
        try:
            db.close()
        except Exception:
            pass
        _db_tls.db = None


def get_db():
    db = getattr(_db_tls, "db", None)
    now = time.time()
    if db is not None:
        idle = now - getattr(_db_tls, "last_used", 0)
        if idle > 5:
            # 空闲超过 5s 的连接先轻探活（pooler/防火墙可能掐掉空闲连接），
            # 一次 SELECT 1 的往返远比完整 TLS 握手便宜；坏了就重建。
            try:
                db.execute("SELECT 1")
            except Exception:
                _db_drop()
                db = None
    if db is None:
        db = _db_tls.db = connect()
    _db_tls.last_used = now
    return db


@app.teardown_appcontext
def close_db(exc):
    _db_tls.last_used = time.time()
    if exc is not None:
        # 请求出错时丢弃连接，避免下次复用到坏连接
        _db_drop()


def init_db():
    db = connect()
    db.executescript(PG_SCHEMA if is_postgres() else SQLITE_SCHEMA)
    db.commit()
    seed(db)
    if not is_postgres():
        migrate_db(db)
    db.close()


def seed(db):
    cur = db.execute("SELECT COUNT(*) AS c FROM users")
    if cur.fetchone()["c"] > 0:
        return
    # 管理员
    db.execute(
        "INSERT INTO users (student_id, name, password_hash, role) VALUES (?,?,?,?)",
        ("2025081034", "史稳祺", generate_password_hash("Ss15855484912"), "admin"),
    )
    # 示例学生（可用管理后台 CSV 批量导入更多）
    for sid, name, pw in [
        ("2021001", "张三", "123456"),
        ("2021002", "李四", "123456"),
    ]:
        db.execute(
            "INSERT INTO users (student_id, name, password_hash, role) VALUES (?,?,?,?)",
            (sid, name, generate_password_hash(pw), "student"),
        )
    # 一道示例题：A+B
    cur = db.execute(
        """INSERT INTO problems
           (title, description, difficulty, time_limit_ms, memory_limit_mb,
            allowed_languages, default_language, order_index)
           VALUES (?,?,?,?,?,?,?,?)""",
        (
            "A+B 问题",
            "读入两个整数 a, b，输出它们的和 a+b。\n\n输入格式：一行两个整数 a b。\n输出格式：一个整数。",
            "简单",
            2000,
            256,
            json.dumps(["c", "cpp", "py"]),
            "c",
            1,
        ),
    )
    pid = cur.lastrowid
    tests = [
        ("1 2", "3", 1),
        ("100 200", "300", 1),
        ("-5 5", "0", 0),
        ("0 0", "0", 0),
    ]
    db.executemany(
        "INSERT INTO testcases (problem_id, input_text, expected_text, is_sample) VALUES (?,?,?,?)",
        [(pid, i, e, s) for (i, e, s) in tests],
    )
    db.commit()
    seed_learn(db)


def seed_learn(db):
    """首次启动时把 B 站公认宝藏教程塞进学习中心（管理员可后续增删改）。"""
    if db.execute("SELECT COUNT(*) c FROM learn_languages").fetchone()["c"] > 0:
        return
    langs = [
        # code, name, tag, intro, roadmap (list), videos (title, author, embed)
        ("c", "C 语言", "经典底层·系统之门",
         "C 是几乎所有现代系统软件的母语：操作系统、嵌入式、编译器、数据库内核都靠它。\n"
         "它的语法朴素、贴近硬件、运行极快，是理解『程序到底怎么跑起来的』最直接的语言。\n"
         "学完 C，再学任何其他语言都会觉得『下面有底』；指针和内存模型是它的灵魂。",
         [
             "环境搭建：Dev-C++ / VS Code + MinGW，把 Hello World 跑通",
             "基础语法：变量、类型、运算符、scanf/printf 格式化输入输出",
             "控制流与循环：if/switch/for/while，写九九乘法表和猜数字",
             "数组与字符串：排序算法（冒泡/选择/插入）、字符串处理",
             "函数与递归：递归求阶乘、汉诺塔，理解调用栈",
             "指针与内存：指针运算、数组与指针的关系、动态内存 malloc/free",
             "结构体与文件：自定义数据类型、文件读写小项目（学生管理系统）",
         ],
         [
             ("浙大翁恺 C 语言程序设计（公认最佳入门）", "翁恺 @ 浙江大学", "BV1dr4y1n7vA"),
             ("郝斌 C 语言自学教程（B 站最高播放量）", "郝斌", "BV1os411h77o"),
             ("C 语言灵魂——指针与内存专题精讲", "鱼C工作室", "BV1qZ4y1V7sE"),
         ]),
        ("cpp", "C++", "工业级主力·性能与抽象兼得",
         "C++ 是『带类的 C』，既能写出操作系统级别的极致性能，又能像 Python 一样写高层抽象。\n"
         "游戏引擎（Unreal/Unity 部分模块）、高频交易、浏览器内核、嵌入式 AI，\n"
         "几乎所有追求性能上限的领域都离不开 C++。C++ 11/14/17/20 现代化以后，写法也优雅了许多。",
         [
             "C 基础回顾：指针、结构体、内存模型",
             "面向对象：类与对象、封装、继承、多态、运算符重载",
             "现代 C++：auto、范围 for、智能指针、lambda、移动语义",
             "STL 标准库：vector/map/unordered_map/algorithm，告别手写排序",
             "模板与泛型：写出能处理任意类型的函数/类",
             "实战项目：写一个小型学生成绩管理系统（控制台）",
             "进阶方向：游戏开发 / 高频交易 / 嵌入式 / 音视频",
         ],
         [
             ("黑马程序员 C++ 入门基础（零基础首选）", "黑马程序员", "BV1Tb411j7uM"),
             ("黑马 C++ 核心编程（面向对象 + STL）", "黑马程序员", "BV1et411b73Z"),
             ("侯捷 C++ 全系列（OOP/STL/内存管理，进阶必看）", "侯捷", "BV1r6h5zgE2i"),
         ]),
        ("python", "Python", "上手最快·AI 与数据首选",
         "Python 语法接近自然语言，库生态极其丰富：AI、爬虫、数据分析、Web、自动化运维、\n"
         "科学计算都能用，是『第一门语言』的最佳选择。\n"
         "C 负责把性能拉满，Python 负责把想法变成代码的速度拉满——两者搭配是工程界黄金组合。",
         [
             "环境：Anaconda 或 Miniconda + PyCharm / VS Code",
             "基础语法：变量、类型、运算符、if/for、列表/字典/集合",
             "函数与模块：def、参数、返回值、import、pip 装第三方库",
             "面向对象：class、继承、魔法方法、写一个小游戏（坦克大战/贪吃蛇）",
             "文件与异常：读写 JSON/CSV、try/except 异常处理",
             "方向选择：爬虫（requests+BeautifulSoup）/ 数据分析（pandas）/ AI（PyTorch）",
             "实战：用 Flask 写一个迷你 Web 项目",
         ],
         [
             ("黑马 Python 600 集（最全面体系课）", "黑马程序员", "BV1ex411x7Em"),
             ("千锋 Python 700 集（案例丰富、覆盖 AI/数据/Web）", "千锋教育", "BV1R7411F7JV"),
             ("嵩天 Python（北理工高校风格、严谨系统）", "嵩天 @ 北京理工大学", "BV1qW4y1a7fU"),
         ]),
    ]
    for oi, (code, name, tag, intro, roadmap, videos) in enumerate(langs):
        cur = db.execute(
            "INSERT INTO learn_languages (code, name, tag, intro, roadmap, order_index) VALUES (?,?,?,?,?,?)",
            (code, name, tag, intro, json.dumps(roadmap, ensure_ascii=False), oi),
        )
        lid = cur.lastrowid
        for voi, (title, author, bvid) in enumerate(videos):
            embed = f"https://player.bilibili.com/player.html?bvid={bvid}&page=1&high_quality=1&danmaku=0"
            db.execute(
                "INSERT INTO learn_videos (language_id, title, author, embed, order_index) VALUES (?,?,?,?,?)",
                (lid, title, author, embed, voi),
            )
    db.commit()


def migrate_db(db):
    """把历史数据归并为「测试点」模型，并保证表结构兼容。"""
    # 旧库可能还没有 point_id 列，补上
    cols = [r[1] for r in db.execute("PRAGMA table_info(testcases)")]
    if "point_id" not in cols:
        db.execute("ALTER TABLE testcases ADD COLUMN point_id INTEGER")
    # 把遗留的「每用例=1测试点」数据归并为正式测试点（仅当尚无测试点时）
    if db.execute("SELECT COUNT(*) c FROM test_points").fetchone()["c"] == 0:
        rows = db.execute(
            "SELECT id, problem_id FROM testcases ORDER BY problem_id, id"
        ).fetchall()
        idx = {}
        for r in rows:
            pid = r["problem_id"]
            idx[pid] = idx.get(pid, 0) + 1
            cur = db.execute(
                "INSERT INTO test_points (problem_id, name, score, order_index) VALUES (?,?,?,?)",
                (pid, "测试点%d" % idx[pid], 1, idx[pid]),
            )
            db.execute("UPDATE testcases SET point_id=? WHERE id=?", (cur.lastrowid, r["id"]))
    db.commit()


# ----------------------------- 工具 -----------------------------
def platform_default_language():
    avail = judge.detect_languages()
    for lang in ("c", "cpp", "py"):
        if avail.get(lang):
            return lang
    return "py"


def row_to_dict(row):
    return dict(row)


@app.context_processor
def inject_globals():
    return {
        "available_langs": judge.detect_languages(),
        "lang_names": {k: v["name"] for k, v in judge.LANGUAGES.items()},
        "current_user": session.get("user"),
    }


def login_required(f):
    @wraps(f)
    def wrapper(*a, **k):
        if "user" not in session:
            return redirect(url_for("login"))
        return f(*a, **k)
    return wrapper


def admin_required(f):
    @wraps(f)
    def wrapper(*a, **k):
        if "user" not in session or session["user"].get("role") != "admin":
            return redirect(url_for("login"))
        return f(*a, **k)
    return wrapper


def get_problem(pid):
    db = get_db()
    p = db.execute("SELECT * FROM problems WHERE id=?", (pid,)).fetchone()
    if not p:
        return None
    tests = db.execute(
        "SELECT * FROM testcases WHERE problem_id=? ORDER BY id", (pid,)
    ).fetchall()
    d = row_to_dict(p)
    d["allowed_languages"] = json.loads(d["allowed_languages"])
    d["tests"] = [row_to_dict(t) for t in tests]
    d["points"] = [
        row_to_dict(pt)
        for pt in db.execute(
            "SELECT * FROM test_points WHERE problem_id=? ORDER BY order_index, id",
            (pid,),
        ).fetchall()
    ]
    return d


def get_set_problems(db, sid):
    """返回某题目集下的题目 id 列表（按 order_index, id 有序）。"""
    rows = db.execute(
        "SELECT problem_id FROM problem_set_items WHERE set_id=? ORDER BY order_index, problem_id",
        (sid,),
    ).fetchall()
    return [r["problem_id"] for r in rows]


def set_completion(db, sid):
    """
    计算某题目集的完成度与排名。
    返回 (pids, total, ranking, overall, n_students)
    - ranking: 按 solved 降序（相同 solved 共享名次，1224 竞赛排名），
      每项 {uid, name, sid, solved, total, pct, rank}；
      从未尝试过该题目集任何题目的学生不出现在榜中
    - overall: 上榜学生的平均完成度（%）
    """
    pids = get_set_problems(db, sid)
    total = len(pids)
    students = db.execute(
        "SELECT id, name, student_id FROM users WHERE role='student' ORDER BY student_id"
    ).fetchall()
    solved_map, tried = {}, set()
    if pids:
        ph = ",".join("?" * total)
        # 一条查询同时取 solved 与 tried，省一次跨区数据库往返
        rows = db.execute(
            "SELECT user_id, problem_id, status FROM submissions "
            "WHERE problem_id IN (%s)" % ph,
            pids,
        ).fetchall()
        for r in rows:
            if r["status"] == "AC":
                solved_map.setdefault(r["user_id"], set()).add(r["problem_id"])
            tried.add(r["user_id"])
    ranking = []
    for u in students:
        if u["id"] not in tried:
            continue  # 一道都没尝试过的学生不上榜
        solved = len(solved_map.get(u["id"], set()))
        pct = round(solved / total * 100) if total else 0
        ranking.append({
            "uid": u["id"], "name": u["name"], "sid": u["student_id"],
            "solved": solved, "total": total, "pct": pct,
        })
    ranking.sort(key=lambda x: (-x["solved"], -x["pct"], x["name"]))
    prev_solved = None
    rank = 0
    for i, r in enumerate(ranking):
        if r["solved"] != prev_solved:
            rank = i + 1
            prev_solved = r["solved"]
        r["rank"] = rank
    n = len(ranking)
    overall = round(sum(r["pct"] for r in ranking) / n) if n else 0
    return pids, total, ranking, overall, n


# ----------------------------- 路由：登录/登出 -----------------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        sid = (request.form.get("student_id") or "").strip()
        name = (request.form.get("name") or "").strip()
        pw = request.form.get("password") or ""
        db = get_db()
        user = db.execute(
            "SELECT * FROM users WHERE student_id=?", (sid,)
        ).fetchone()
        if not user:
            flash("该学号尚未注册，请先注册或检查学号", "danger")
        elif user["name"] != name:
            flash("该学号对应的姓名不正确", "danger")
        elif not check_password_hash(user["password_hash"], pw):
            flash("密码错误", "danger")
        else:
            session["user"] = {
                "id": user["id"],
                "student_id": user["student_id"],
                "name": user["name"],
                "role": user["role"],
            }
            if user["role"] == "admin":
                return redirect(url_for("admin_dashboard"))
            return redirect(url_for("problems"))
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ----------------------------- 路由：注册 / 个人中心 -----------------------------
@app.route("/register", methods=["GET", "POST"])
def register():
    # 已登录用户无需再注册
    if "user" in session:
        if session["user"]["role"] == "admin":
            return redirect(url_for("admin_dashboard"))
        return redirect(url_for("problems"))

    if request.method == "POST":
        sid = (request.form.get("student_id") or "").strip()
        name = (request.form.get("name") or "").strip()
        pw = request.form.get("password") or ""
        pw2 = request.form.get("password2") or ""

        errors = []
        if not sid:
            errors.append("学号不能为空")
        elif not (sid.isdigit() and 2026085001 <= int(sid) <= 2026085120):
            errors.append("学号必须为 2026085001 ~ 2026085120 之间的 10 位数字")
        if not name:
            errors.append("姓名不能为空")
        if len(pw) < 6:
            errors.append("密码至少 6 位")
        if pw != pw2:
            errors.append("两次输入的密码不一致")
        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("register.html", sid=sid, name=name)

        db = get_db()
        try:
            cur = db.execute(
                "INSERT INTO users (student_id, name, password_hash, role) "
                "VALUES (?,?,?,?)",
                (sid, name, generate_password_hash(pw), "student"),
            )
            db.commit()
        except IntegrityError:
            flash("该学号已被注册，请直接登录或换一个学号", "danger")
            return render_template("register.html", sid=sid, name=name)

        # 注册成功，自动登录
        uid = cur.lastrowid
        session["user"] = {
            "id": uid, "student_id": sid, "name": name, "role": "student",
        }
        flash("注册成功，已自动登录 🎉", "success")
        return redirect(url_for("problems"))

    return render_template("register.html")


@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    db = get_db()
    if request.method == "POST":
        current = request.form.get("current_password") or ""
        new = request.form.get("new_password") or ""
        new2 = request.form.get("new_password2") or ""

        user = db.execute(
            "SELECT * FROM users WHERE id=?", (session["user"]["id"],)
        ).fetchone()

        if not check_password_hash(user["password_hash"], current):
            flash("当前密码不正确", "danger")
        elif len(new) < 6:
            flash("新密码至少 6 位", "danger")
        elif new != new2:
            flash("两次输入的新密码不一致", "danger")
        else:
            db.execute(
                "UPDATE users SET password_hash=? WHERE id=?",
                (generate_password_hash(new), user["id"]),
            )
            db.commit()
            flash("密码修改成功", "success")
            return redirect(url_for("profile"))

    return render_template("profile.html")


# ----------------------------- 路由：学生端 -----------------------------
@app.route("/")
def index():
    if "user" in session:
        if session["user"]["role"] == "admin":
            return redirect(url_for("admin_dashboard"))
        return redirect(url_for("problems"))
    db = get_db()
    # 四个统计合并为一条 SQL：跨区链路下省掉 3 次数据库往返
    try:
        row = db.execute(
            "SELECT (SELECT COUNT(*) FROM problems) AS problems, "
            "(SELECT COUNT(*) FROM submissions) AS submissions, "
            "(SELECT COUNT(DISTINCT language) FROM problems) AS languages, "
            "(SELECT COUNT(*) FROM users) AS students"
        ).fetchone()
        stats = {k: row[k] for k in ("problems", "submissions", "languages", "students")}
    except Exception:
        stats = {"problems": 0, "submissions": 0, "languages": 0, "students": 0}
    return render_template("index.html", stats=stats)


@app.route("/problems")
@login_required
def problems():
    db = get_db()
    probs = db.execute(
        "SELECT * FROM problems ORDER BY order_index, id"
    ).fetchall()
    # 用户每题的作答状态：solved(已通过) / tried(尝试过未通过) / none(未作答)
    pstate = {}
    rows = db.execute(
        "SELECT problem_id, status FROM submissions WHERE user_id=?",
        (session["user"]["id"],),
    ).fetchall()
    for r in rows:
        pid = r["problem_id"]
        if r["status"] == "AC":
            pstate[pid] = "solved"
        elif pstate.get(pid) != "solved":
            pstate[pid] = "tried"
    total = len(probs)
    solved = sum(1 for v in pstate.values() if v == "solved")
    tried = sum(1 for v in pstate.values() if v == "tried")
    rate = round(solved / total * 100) if total else 0
    return render_template(
        "problems.html", problems=probs, pstate=pstate,
        solved=solved, total=total, tried=tried, rate=rate,
    )


@app.route("/problem/<int:pid>", methods=["GET", "POST"])
@login_required
def problem(pid):
    p = get_problem(pid)
    if not p:
        flash("题目不存在", "danger")
        return redirect(url_for("problems"))

    if request.method == "POST":
        code = request.form.get("code") or ""
        language = request.form.get("language") or p["default_language"]
        if language not in p["allowed_languages"]:
            language = p["default_language"]
        if language not in p["allowed_languages"]:
            flash("该题目不支持所选语言", "danger")
            return render_template("problem.html", problem=p, code=code,
                                   default_lang=p["default_language"])
        db = get_db()
        points = [
            {"id": pt["id"], "name": pt["name"], "score": pt["score"]}
            for pt in p["points"]
        ]
        cases = [
            {
                "point_id": c["point_id"],
                "input": c["input_text"],
                "expected": c["expected_text"],
                "is_sample": bool(c["is_sample"]),
                "id": c["id"],
            }
            for c in p["tests"]
        ]
        result = judge.judge_submission(
            code, language, points, cases,
            time_limit_ms=p["time_limit_ms"],
            memory_limit_mb=p["memory_limit_mb"],
        )
        max_rt = None
        for pt in result["points"]:
            if pt["runtime_ms"] is not None:
                max_rt = pt["runtime_ms"] if max_rt is None else max(max_rt, pt["runtime_ms"])
        cur = db.execute(
            """INSERT INTO submissions
               (user_id, problem_id, language, code, status, passed, total,
                max_runtime_ms, compile_error, results_json)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                session["user"]["id"], pid, language, code, result["status"],
                result["passed"], result["total"], max_rt,
                result.get("compile_error", ""),
                json.dumps(result, ensure_ascii=False),
            ),
        )
        sid = cur.lastrowid
        db.commit()
        return redirect(url_for("submission", sid=sid))

    # GET：取出该生本题最近一次提交，用于预填编辑器与展示「上次提交」
    db = get_db()
    last_sub = None
    if session.get("user"):
        uid = session["user"]["id"]
        last_sub = db.execute(
            "SELECT * FROM submissions WHERE user_id=? AND problem_id=? "
            "ORDER BY submitted_at DESC, id DESC LIMIT 1",
            (uid, pid),
        ).fetchone()
    default_lang = last_sub["language"] if last_sub else p["default_language"]
    return render_template(
        "problem.html", problem=p,
        code=(last_sub["code"] if last_sub else ""),
        default_lang=default_lang, last_sub=last_sub,
    )


@app.route("/submission/<int:sid>")
@login_required
def submission(sid):
    db = get_db()
    sub = db.execute(
        "SELECT s.*, p.title AS ptitle FROM submissions s "
        "JOIN problems p ON p.id=s.problem_id WHERE s.id=?",
        (sid,),
    ).fetchone()
    if not sub or sub["user_id"] != session["user"]["id"] and session["user"]["role"] != "admin":
        flash("无权限查看该提交", "danger")
        return redirect(url_for("problems"))
    result = json.loads(sub["results_json"] or "{}")
    return render_template(
        "submission.html", sub=sub, result=result,
        lang_names={k: v["name"] for k, v in judge.LANGUAGES.items()},
    )


@app.route("/my")
@login_required
def my_submissions():
    db = get_db()
    subs = db.execute(
        "SELECT s.*, p.title AS ptitle FROM submissions s "
        "JOIN problems p ON p.id=s.problem_id "
        "WHERE s.user_id=? ORDER BY s.submitted_at DESC, s.id DESC",
        (session["user"]["id"],),
    ).fetchall()
    return render_template("my.html", subs=subs)


@app.route("/learn")
@login_required
def learn():
    """学习中心：语言学习方向 + 教学视频（数据来自数据库，管理员可增删改）。"""
    db = get_db()
    langs = db.execute(
        "SELECT * FROM learn_languages ORDER BY order_index, id"
    ).fetchall()
    out = []
    for la in langs:
        videos = db.execute(
            "SELECT * FROM learn_videos WHERE language_id=? ORDER BY order_index, id",
            (la["id"],),
        ).fetchall()
        d = dict(la)
        try:
            d["roadmap"] = json.loads(la["roadmap"] or "[]")
        except Exception:
            d["roadmap"] = []
        d["videos"] = [dict(v) for v in videos]
        out.append(d)
    return render_template("learn.html", langs=out)


@app.route("/sets")
@login_required
def sets():
    """学生端：题目集列表（含本人完成度）。"""
    db = get_db()
    rows = db.execute("SELECT * FROM problem_sets ORDER BY id").fetchall()
    uid = session["user"]["id"]
    data = []
    for s in rows:
        pids = get_set_problems(db, s["id"])
        total = len(pids)
        solved = 0
        if pids:
            ph = ",".join("?" * total)
            solved = db.execute(
                "SELECT COUNT(DISTINCT problem_id) c FROM submissions "
                "WHERE user_id=? AND problem_id IN (%s) AND status='AC'" % ph,
                [uid] + pids,
            ).fetchone()["c"]
        pct = round(solved / total * 100) if total else 0
        data.append({
            "id": s["id"], "title": s["title"], "description": s["description"],
            "total": total, "solved": solved, "pct": pct,
        })
    return render_template("sets.html", sets=data)


@app.route("/set/<int:sid>")
@login_required
def set_detail(sid):
    """学生端：题目集详情——本人完成度、题目列表（含作答状态）、排名列表。"""
    db = get_db()
    s = db.execute("SELECT * FROM problem_sets WHERE id=?", (sid,)).fetchone()
    if not s:
        flash("题目集不存在", "danger")
        return redirect(url_for("sets"))
    pids, total, ranking, overall, n_students = set_completion(db, sid)
    problems = []
    if pids:
        ph = ",".join("?" * len(pids))
        prows = db.execute(
            "SELECT id, title, difficulty FROM problems WHERE id IN (%s) ORDER BY id" % ph,
            pids,
        ).fetchall()
        subs = db.execute(
            "SELECT problem_id, status FROM submissions "
            "WHERE user_id=? AND problem_id IN (%s)" % ph,
            [session["user"]["id"]] + pids,
        ).fetchall()
        pstate = {}
        for r in subs:
            pid = r["problem_id"]
            if r["status"] == "AC":
                pstate[pid] = "solved"
            elif pstate.get(pid) != "solved":
                pstate[pid] = "tried"
        for p in prows:
            problems.append({
                "id": p["id"], "title": p["title"], "difficulty": p["difficulty"],
                "status": pstate.get(p["id"], "none"),
            })
    my = next((r for r in ranking if r["uid"] == session["user"]["id"]), None)
    return render_template(
        "set_detail.html", s=s, problems=problems, ranking=ranking,
        overall=overall, n_students=n_students, my=my,
    )


# ----------------------------- 路由：管理端 -----------------------------
@app.route("/admin")
@admin_required
def admin_dashboard():
    db = get_db()
    n_students = db.execute(
        "SELECT COUNT(*) c FROM users WHERE role='student'"
    ).fetchone()["c"]
    n_problems = db.execute("SELECT COUNT(*) c FROM problems").fetchone()["c"]
    n_subs = db.execute("SELECT COUNT(*) c FROM submissions").fetchone()["c"]
    return render_template(
        "admin_dashboard.html",
        n_students=n_students, n_problems=n_problems, n_subs=n_subs,
    )


@app.route("/admin/problems")
@admin_required
def admin_problems():
    db = get_db()
    probs = db.execute(
        "SELECT * FROM problems ORDER BY order_index, id"
    ).fetchall()
    return render_template("admin_problems.html", problems=probs)


@app.route("/admin/problem/new", methods=["GET", "POST"])
@app.route("/admin/problem/<int:pid>/edit", methods=["GET", "POST"])
@admin_required
def admin_problem_edit(pid=None):
    db = get_db()
    p = get_problem(pid) if pid else None
    if request.method == "POST":
        title = (request.form.get("title") or "").strip()
        description = request.form.get("description") or ""
        difficulty = request.form.get("difficulty") or "简单"
        time_limit = int(request.form.get("time_limit_ms") or 2000)
        memory_limit = int(request.form.get("memory_limit_mb") or 256)
        allowed = request.form.getlist("allowed_languages") or ["c", "cpp", "py"]
        default_language = request.form.get("default_language") or allowed[0]
        if default_language not in allowed:
            default_language = allowed[0]
        if not title:
            flash("题目标题不能为空", "danger")
        else:
            if p:
                db.execute(
                    """UPDATE problems SET title=?, description=?, difficulty=?,
                       time_limit_ms=?, memory_limit_mb=?, allowed_languages=?,
                       default_language=? WHERE id=?""",
                    (title, description, difficulty, time_limit, memory_limit,
                     json.dumps(allowed), default_language, pid),
                )
                flash("题目已更新", "success")
            else:
                cur = db.execute(
                    """INSERT INTO problems
                       (title, description, difficulty, time_limit_ms,
                        memory_limit_mb, allowed_languages, default_language,
                        order_index)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (title, description, difficulty, time_limit, memory_limit,
                     json.dumps(allowed), default_language,
                     db.execute("SELECT COALESCE(MAX(order_index),0)+1 m FROM problems").fetchone()["m"]),
                )
                pid = cur.lastrowid
                flash("题目已创建", "success")
            db.commit()
            return redirect(url_for("admin_problem_edit", pid=pid))
    return render_template("admin_problem_edit.html", problem=p,
                           lang_names={k: v["name"] for k, v in judge.LANGUAGES.items()})


@app.route("/admin/problem/<int:pid>/delete", methods=["POST"])
@admin_required
def admin_problem_delete(pid):
    db = get_db()
    db.execute("DELETE FROM problems WHERE id=?", (pid,))
    db.commit()
    flash("题目已删除", "success")
    return redirect(url_for("admin_problems"))


@app.route("/admin/problem/<int:pid>/tests", methods=["GET", "POST"])
@admin_required
def admin_tests(pid):
    db = get_db()
    p = db.execute("SELECT * FROM problems WHERE id=?", (pid,)).fetchone()
    if not p:
        flash("题目不存在", "danger")
        return redirect(url_for("admin_problems"))
    if request.method == "POST":
        action = request.form.get("action")
        if action == "add_point":
            name = (request.form.get("name") or "测试点").strip() or "测试点"
            score = int(request.form.get("score") or 1)
            oi = db.execute(
                "SELECT COALESCE(MAX(order_index),0)+1 m FROM test_points WHERE problem_id=?",
                (pid,),
            ).fetchone()["m"]
            db.execute(
                "INSERT INTO test_points (problem_id, name, score, order_index) VALUES (?,?,?,?)",
                (pid, name, score, oi),
            )
            flash("测试点已添加", "success")
        elif action == "edit_point":
            tid = request.form.get("tid")
            name = (request.form.get("name") or "测试点").strip() or "测试点"
            score = int(request.form.get("score") or 1)
            db.execute(
                "UPDATE test_points SET name=?, score=? WHERE id=? AND problem_id=?",
                (name, score, tid, pid),
            )
            flash("测试点已更新", "success")
        elif action == "delete_point":
            tid = request.form.get("tid")
            db.execute("DELETE FROM testcases WHERE point_id=? AND problem_id=?", (tid, pid))
            db.execute("DELETE FROM test_points WHERE id=? AND problem_id=?", (tid, pid))
            flash("测试点已删除（含其下用例）", "success")
        elif action == "add_case":
            tid = request.form.get("tid")
            inp = request.form.get("input_text") or ""
            exp = request.form.get("expected_text") or ""
            is_sample = 1 if request.form.get("is_sample") else 0
            db.execute(
                "INSERT INTO testcases (problem_id, input_text, expected_text, is_sample, point_id) VALUES (?,?,?,?,?)",
                (pid, inp, exp, is_sample, tid),
            )
            flash("用例已添加", "success")
        elif action == "delete_case":
            cid = request.form.get("cid")
            db.execute("DELETE FROM testcases WHERE id=? AND problem_id=?", (cid, pid))
            flash("用例已删除", "success")
        db.commit()
        return redirect(url_for("admin_tests", pid=pid))
    points = db.execute(
        "SELECT * FROM test_points WHERE problem_id=? ORDER BY order_index, id", (pid,)
    ).fetchall()
    cases = db.execute(
        "SELECT * FROM testcases WHERE problem_id=? ORDER BY id", (pid,)
    ).fetchall()
    return render_template("admin_tests.html", problem=p, points=points, cases=cases)


@app.route("/admin/students", methods=["GET", "POST"])
@admin_required
def admin_students():
    db = get_db()
    if request.method == "POST":
        action = request.form.get("action")
        if action == "add":
            sid = (request.form.get("student_id") or "").strip()
            name = (request.form.get("name") or "").strip()
            pw = request.form.get("password") or ""
            if not sid or not name or not pw:
                flash("学号、姓名、密码均不能为空", "danger")
            else:
                try:
                    db.execute(
                        "INSERT INTO users (student_id, name, password_hash, role) VALUES (?,?,?,?)",
                        (sid, name, generate_password_hash(pw), "student"),
                    )
                    db.commit()
                    flash("学生已添加：%s %s" % (sid, name), "success")
                except IntegrityError:
                    flash("学号已存在：%s" % sid, "danger")
        elif action == "delete":
            uid = request.form.get("uid")
            db.execute("DELETE FROM users WHERE id=? AND role='student'", (uid,))
            db.commit()
            flash("学生已删除", "success")
        elif action == "import":
            f = request.files.get("file")
            if not f or not f.filename:
                flash("请选择 CSV 文件", "danger")
            else:
                text = f.read().decode("utf-8-sig", errors="ignore")
                added, skipped = import_students_csv(db, text)
                db.commit()
                flash("导入完成：新增 %d 人，跳过 %d 人（学号重复）" % (added, skipped), "success")
        return redirect(url_for("admin_students"))

    students = db.execute(
        "SELECT * FROM users WHERE role='student' ORDER BY student_id"
    ).fetchall()
    return render_template("admin_students.html", students=students)


def import_students_csv(db, text):
    added, skipped = 0, 0
    lines = [ln for ln in text.splitlines() if ln.strip()]
    for i, line in enumerate(lines):
        # 允许首行为表头
        if i == 0 and line.replace(",", "").startswith("student_id"):
            continue
        parts = line.split(",")
        if len(parts) < 3:
            continue
        sid, name, pw = parts[0].strip(), parts[1].strip(), parts[2].strip()
        if not sid or not pw:
            continue
        try:
            db.execute(
                "INSERT INTO users (student_id, name, password_hash, role) VALUES (?,?,?,?)",
                (sid, name, generate_password_hash(pw), "student"),
            )
            added += 1
        except IntegrityError:
            skipped += 1
    return added, skipped


@app.route("/admin/import", methods=["GET", "POST"])
@admin_required
def admin_import():
    if request.method == "POST":
        # 优先用文本框粘贴的内容，其次用上传文件
        text = (request.form.get("json_text") or "").strip()
        f = request.files.get("file")
        if not text and f and f.filename:
            try:
                text = f.read().decode("utf-8")
            except Exception as e:
                flash("文件读取失败：%s" % e, "danger")
                return redirect(url_for("admin_import"))
        if not text:
            flash("请上传 JSON 文件，或直接粘贴 JSON 内容", "danger")
            return redirect(url_for("admin_import"))
        try:
            data = json.loads(text)
        except Exception as e:
            flash("JSON 解析失败：%s" % e, "danger")
            return redirect(url_for("admin_import"))
        db = get_db()
        cnt = import_problems_json(db, data)
        db.commit()
        flash("成功导入 %d 道题目" % cnt, "success")
        return redirect(url_for("admin_problems"))
    return render_template("admin_import.html")


@app.route("/admin/pta_import", methods=["GET", "POST"])
@admin_required
def admin_pta_import():
    if request.method == "POST":
        psid_raw = (request.form.get("psid") or "").strip()
        auth = (request.form.get("cookie") or "").strip()
        # 若用户把整段 cURL 粘到 Cookie 框，自动从中抠出题目集 / 考试 ID
        ch, th, lh, eh, psid_h = parse_curl_auth(auth)
        if not psid_raw and psid_h:
            psid_raw = psid_h
        # 从完整链接里抠题目集 ID
        m = re.search(r"problem-sets/(\d+)", psid_raw)
        psid = m.group(1) if m else re.sub(r"\D", "", psid_raw)
        if not psid:
            flash("请填写题目集 ID（或把整段 cURL 粘到下方 Cookie 框，会自动识别）", "danger")
            return redirect(url_for("admin_pta_import"))
        if not auth:
            flash("请填写 PTA 的登录 Cookie，或把 F12 复制的整段 cURL 粘进来（推荐）", "danger")
            return redirect(url_for("admin_pta_import"))
        # 注意：Cookie 属于用户凭据，绝不写入日志、绝不回显
        try:
            problems = scrape_problem_set(psid, auth)
        except ValueError as e:
            flash("抓取失败：" + str(e), "danger")
            return redirect(url_for("admin_pta_import"))
        except Exception as e:  # pragma: no cover - 兜底
            flash("抓取失败：%s" % e, "danger")
            return redirect(url_for("admin_pta_import"))
        if not problems:
            flash("没抓到任何题目，请检查题目集 ID 是否正确、Cookie 是否过期", "danger")
            return redirect(url_for("admin_pta_import"))
        db_ins = get_db()
        cnt = import_problems_json(db_ins, problems)
        db_ins.commit()
        flash("成功从 PTA 导入 %d 道题目！（仅含样例测试点，请到「测试用例」补全隐藏点）" % cnt, "success")
        return redirect(url_for("admin_problems"))
    return render_template("admin_pta_import.html")


@app.route("/admin/download_sample")
@admin_required
def admin_download_sample():
    sample = os.path.join(BASE_DIR, "sample_problems.json")
    return send_file(sample, as_attachment=True, download_name="sample_problems.json")


def import_problems_json(db, data):
    if isinstance(data, dict):
        data = [data]
    cnt = 0
    for item in data:
        cur = db.execute(
            """INSERT INTO problems
               (title, description, difficulty, time_limit_ms, memory_limit_mb,
                allowed_languages, default_language, order_index)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                item.get("title", "未命名题目"),
                item.get("description", ""),
                item.get("difficulty", "简单"),
                int(item.get("time_limit_ms", 2000)),
                int(item.get("memory_limit_mb", 256)),
                json.dumps(item.get("allowed_languages", ["c", "cpp", "py"])),
                item.get("default_language", "c"),
                db.execute("SELECT COALESCE(MAX(order_index),0)+1 m FROM problems").fetchone()["m"],
            ),
        )
        pid = cur.lastrowid
        pts = item.get("points")
        if pts:
            oi = 0
            for pt in pts:
                oi += 1
                pc = db.execute(
                    "INSERT INTO test_points (problem_id, name, score, order_index) VALUES (?,?,?,?)",
                    (pid, pt.get("name", "测试点"), int(pt.get("score", 1)), oi),
                )
                new_pid = pc.lastrowid
                for tc in pt.get("cases", []):
                    db.execute(
                        "INSERT INTO testcases (problem_id, input_text, expected_text, is_sample, point_id) VALUES (?,?,?,?,?)",
                        (pid, tc.get("input", ""), tc.get("expected", ""),
                         1 if tc.get("is_sample") else 0, new_pid),
                    )
        else:
            # 兼容旧格式：每个用例自成测试点
            for tc in item.get("tests", []):
                pc = db.execute(
                    "INSERT INTO test_points (problem_id, name, score, order_index) VALUES (?,?,?,?)",
                    (pid, "测试点", 1, 0),
                )
                new_pid = pc.lastrowid
                db.execute(
                    "INSERT INTO testcases (problem_id, input_text, expected_text, is_sample, point_id) VALUES (?,?,?,?,?)",
                    (pid, tc.get("input", ""), tc.get("expected", ""),
                     1 if tc.get("is_sample") else 0, new_pid),
                )
        cnt += 1
    return cnt


@app.route("/admin/submissions")
@admin_required
def admin_submissions():
    db = get_db()
    subs = db.execute(
        "SELECT s.*, p.title AS ptitle, u.name AS uname, u.student_id AS usid "
        "FROM submissions s JOIN problems p ON p.id=s.problem_id "
        "JOIN users u ON u.id=s.user_id ORDER BY s.submitted_at DESC, s.id DESC"
    ).fetchall()
    return render_template("admin_submissions.html", subs=subs)


@app.route("/admin/sets")
@admin_required
def admin_sets():
    """管理端：题目集列表（含整体完成度）。"""
    db = get_db()
    rows = db.execute("SELECT * FROM problem_sets ORDER BY id").fetchall()
    data = []
    for s in rows:
        pids, total, ranking, overall, n_students = set_completion(db, s["id"])
        data.append({
            "id": s["id"], "title": s["title"], "description": s["description"],
            "total": total, "overall": overall, "n_students": n_students,
        })
    return render_template("admin_sets.html", sets=data)


@app.route("/admin/set/new", methods=["GET", "POST"])
@app.route("/admin/set/<int:sid>/edit", methods=["GET", "POST"])
@admin_required
def admin_set_edit(sid=None):
    """管理端：创建/编辑题目集（标题、描述、勾选题目）。"""
    db = get_db()
    s = db.execute("SELECT * FROM problem_sets WHERE id=?", (sid,)).fetchone() if sid else None
    all_problems = db.execute(
        "SELECT id, title, difficulty FROM problems ORDER BY order_index, id"
    ).fetchall()
    member_ids = set()
    stats = None
    if s:
        rows = db.execute(
            "SELECT problem_id FROM problem_set_items WHERE set_id=?", (sid,)
        ).fetchall()
        member_ids = {r["problem_id"] for r in rows}
        pids, total, ranking, overall, n_students = set_completion(db, sid)
        stats = {"total": total, "overall": overall,
                 "n_students": n_students, "ranking": ranking}
    if request.method == "POST":
        title = (request.form.get("title") or "").strip()
        description = request.form.get("description") or ""
        selected = []
        for x in request.form.getlist("problem_ids"):
            try:
                selected.append(int(x))
            except ValueError:
                pass
        if not title:
            flash("题目集名称不能为空", "danger")
        else:
            if s:
                db.execute(
                    "UPDATE problem_sets SET title=?, description=? WHERE id=?",
                    (title, description, sid),
                )
                db.execute("DELETE FROM problem_set_items WHERE set_id=?", (sid,))
                flash("题目集已更新", "success")
            else:
                cur = db.execute(
                    "INSERT INTO problem_sets (title, description) VALUES (?,?)",
                    (title, description),
                )
                sid = cur.lastrowid
                flash("题目集已创建", "success")
            oi = 0
            for pid in selected:
                oi += 1
                db.execute(
                    "INSERT OR IGNORE INTO problem_set_items (set_id, problem_id, order_index) VALUES (?,?,?)",
                    (sid, pid, oi),
                )
            db.commit()
            return redirect(url_for("admin_set_edit", sid=sid))
    return render_template(
        "admin_set_edit.html", s=s, all_problems=all_problems,
        member_ids=member_ids, stats=stats,
    )


@app.route("/admin/set/<int:sid>/delete", methods=["POST"])
@admin_required
def admin_set_delete(sid):
    db = get_db()
    db.execute("DELETE FROM problem_sets WHERE id=?", (sid,))
    db.commit()
    flash("题目集已删除", "success")
    return redirect(url_for("admin_sets"))


@app.route("/admin/learn")
@admin_required
def admin_learn():
    """管理端：学习中心 —— 语言 / 视频 增删改。"""
    db = get_db()
    langs = db.execute("SELECT * FROM learn_languages ORDER BY order_index, id").fetchall()
    out = []
    for la in langs:
        vs = db.execute(
            "SELECT * FROM learn_videos WHERE language_id=? ORDER BY order_index, id",
            (la["id"],),
        ).fetchall()
        out.append({**dict(la), "videos": [dict(v) for v in vs]})
    return render_template("admin_learn.html", langs=out)


@app.route("/admin/learn/language/add", methods=["POST"])
@admin_required
def admin_learn_language_add():
    db = get_db()
    code = (request.form.get("code") or "").strip().lower()
    name = (request.form.get("name") or "").strip()
    tag = (request.form.get("tag") or "").strip()
    intro = request.form.get("intro") or ""
    roadmap_raw = request.form.get("roadmap") or ""
    roadmap = [ln.strip() for ln in roadmap_raw.splitlines() if ln.strip()]
    if not code or not name:
        flash("代码与名称不能为空", "danger")
        return redirect(url_for("admin_learn"))
    oi = db.execute("SELECT COALESCE(MAX(order_index),0)+1 m FROM learn_languages").fetchone()["m"]
    try:
        db.execute(
            "INSERT INTO learn_languages (code, name, tag, intro, roadmap, order_index) VALUES (?,?,?,?,?,?)",
            (code, name, tag, intro, json.dumps(roadmap, ensure_ascii=False), oi),
        )
        db.commit()
        flash("语言已添加：%s" % name, "success")
    except IntegrityError:
        flash("代码已存在：%s" % code, "danger")
    return redirect(url_for("admin_learn"))


@app.route("/admin/learn/language/<int:lid>/edit", methods=["POST"])
@admin_required
def admin_learn_language_edit(lid):
    db = get_db()
    name = (request.form.get("name") or "").strip()
    tag = (request.form.get("tag") or "").strip()
    intro = request.form.get("intro") or ""
    roadmap_raw = request.form.get("roadmap") or ""
    roadmap = [ln.strip() for ln in roadmap_raw.splitlines() if ln.strip()]
    if not name:
        flash("名称不能为空", "danger")
        return redirect(url_for("admin_learn"))
    db.execute(
        "UPDATE learn_languages SET name=?, tag=?, intro=?, roadmap=? WHERE id=?",
        (name, tag, intro, json.dumps(roadmap, ensure_ascii=False), lid),
    )
    db.commit()
    flash("语言已更新", "success")
    return redirect(url_for("admin_learn"))


@app.route("/admin/learn/language/<int:lid>/delete", methods=["POST"])
@admin_required
def admin_learn_language_delete(lid):
    db = get_db()
    db.execute("DELETE FROM learn_languages WHERE id=?", (lid,))
    db.commit()
    flash("语言已删除（含其下视频）", "success")
    return redirect(url_for("admin_learn"))


@app.route("/admin/learn/video/add", methods=["POST"])
@admin_required
def admin_learn_video_add():
    db = get_db()
    lid = int(request.form.get("language_id") or 0)
    title = (request.form.get("title") or "").strip()
    author = (request.form.get("author") or "").strip()
    embed = (request.form.get("embed") or "").strip()
    if not lid or not title or not embed:
        flash("所属语言、标题、嵌入地址均必填", "danger")
        return redirect(url_for("admin_learn"))
    oi = db.execute(
        "SELECT COALESCE(MAX(order_index),0)+1 m FROM learn_videos WHERE language_id=?",
        (lid,),
    ).fetchone()["m"]
    db.execute(
        "INSERT INTO learn_videos (language_id, title, author, embed, order_index) VALUES (?,?,?,?,?)",
        (lid, title, author, embed, oi),
    )
    db.commit()
    flash("视频已添加", "success")
    return redirect(url_for("admin_learn"))


@app.route("/admin/learn/video/<int:vid>/edit", methods=["POST"])
@admin_required
def admin_learn_video_edit(vid):
    db = get_db()
    title = (request.form.get("title") or "").strip()
    author = (request.form.get("author") or "").strip()
    embed = (request.form.get("embed") or "").strip()
    if not title or not embed:
        flash("标题与嵌入地址必填", "danger")
        return redirect(url_for("admin_learn"))
    db.execute(
        "UPDATE learn_videos SET title=?, author=?, embed=? WHERE id=?",
        (title, author, embed, vid),
    )
    db.commit()
    flash("视频已更新", "success")
    return redirect(url_for("admin_learn"))


@app.route("/admin/learn/video/<int:vid>/delete", methods=["POST"])
@admin_required
def admin_learn_video_delete(vid):
    db = get_db()
    db.execute("DELETE FROM learn_videos WHERE id=?", (vid,))
    db.commit()
    flash("视频已删除", "success")
    return redirect(url_for("admin_learn"))


# ----------------------------- 启动 -----------------------------
# 生产环境（waitress / gunicorn 通过 `app:app` 导入）不会执行 __main__，
# 这里在模块导入时即初始化表结构与种子数据（幂等，可重复调用）。
import traceback as _tb

DB_INIT_ERROR = None
try:
    init_db()
    print("✅ init_db 成功")
except Exception:
    DB_INIT_ERROR = _tb.format_exc()
    # 关键：不要 re-raise，否则模块导入崩溃、waitress 起不来、Railway healthcheck
    # 失败并回滚。改为让应用照常启动，真实错误通过 /healthz 暴露，便于远程排障。
    print("❌ init_db 失败（应用仍启动，详见 /healthz）：")
    print(DB_INIT_ERROR)


def _git_commit():
    try:
        import subprocess

        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=os.path.dirname(os.path.abspath(__file__)),
            stderr=subprocess.DEVNULL,
        ).decode().strip()
    except Exception:
        return "unknown"


@app.route("/healthz")
def healthz():
    """诊断端点：暴露当前运行 commit、DB 类型与初始化错误，供远程排障。"""
    url = os.environ.get("DATABASE_URL", "")
    return {
        "commit": _git_commit(),
        "is_postgres": is_postgres(),
        "db_url_prefix": (url[:25] + "…") if url else None,
        "db_init_error": DB_INIT_ERROR,
        "status": "ok" if DB_INIT_ERROR is None else "db_init_failed",
    }

if __name__ == "__main__":
    avail = judge.detect_languages()
    print("可用语言:", {k: v for k, v in avail.items()})
    print("默认语言:", platform_default_language())
    app.run(host="0.0.0.0", port=5000, threaded=True, debug=False)
