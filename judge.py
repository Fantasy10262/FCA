"""
多语言判题引擎
支持 C / C++ / Python，按测试用例比对标准输出，给出 AC/WA/CE/RE/TLE 等结果。
默认语言与可用语言由 app.py 根据探测到的编译器动态决定。
"""
import os
import sys
import time
import shutil
import tempfile
import subprocess

# 运行判题服务的解释器（即启动 Flask 的 Python，用于运行 Python 提交）
PYTHON_EXE = sys.executable

# 语言配置。compile / run 中的 {src} {exe} 会被替换。
# 注意：-std 标准不写死，运行时按编译器版本自适应（见 _compiler_std）。
LANGUAGES = {
    "c": {
        "name": "C",
        "ext": "c",
        "lang": "c",
        "compile": ["gcc", "-O2", "-o", "{exe}", "{src}"],
        "run": ["{exe}"],
        "needs_compile": True,
    },
    "cpp": {
        "name": "C++",
        "ext": "cpp",
        "lang": "c++",
        "compile": ["g++", "-O2", "-o", "{exe}", "{src}"],
        "run": ["{exe}"],
        "needs_compile": True,
    },
    "py": {
        "name": "Python",
        "ext": "py",
        "compile": None,
        "run": [PYTHON_EXE, "{src}"],
        "needs_compile": False,
    },
}

# 编译器版本探测缓存：compiler 路径 -> (major, minor)
_VERSION_CACHE = {}


def _compiler_version(compiler_path):
    """获取编译器主、次版本号，如 (4, 9) / (13, 2)。失败返回 (0, 0)。"""
    if compiler_path in _VERSION_CACHE:
        return _VERSION_CACHE[compiler_path]
    ver = (0, 0)
    try:
        out = subprocess.run(
            [compiler_path, "-dumpversion"],
            capture_output=True, text=True, timeout=10,
        )
        nums = (out.stdout or "").strip().split(".")
        maj = int(nums[0]) if len(nums) > 0 and nums[0].isdigit() else 0
        min_ = int(nums[1]) if len(nums) > 1 and nums[1].isdigit() else 0
        ver = (maj, min_)
    except Exception:
        ver = (0, 0)
    _VERSION_CACHE[compiler_path] = ver
    return ver


def _compiler_std(compiler_path, lang):
    """根据编译器版本返回合适的 -std 参数（老 gcc 不支持新标准）。"""
    maj, min_ = _compiler_version(compiler_path)
    # 版本太旧无法判断时，给一个尽量兼容的默认值
    if maj == 0 and min_ == 0:
        return "-std=c++11" if lang == "c++" else "-std=c11"
    if lang == "c":
        # gcc >= 4.7 支持 c11；更早回退 gnu99
        return "-std=c11" if (maj, min_) >= (4, 7) else "-std=gnu99"
    # C++
    if (maj, min_) >= (11, 0):
        return "-std=c++17"
    if (maj, min_) >= (5, 0):
        return "-std=c++14"
    if (maj, min_) >= (4, 7):
        return "-std=c++11"
    return "-std=c++98"

STATUS_TEXT = {
    "AC": "通过 (Accepted)",
    "WA": "答案错误 (Wrong Answer)",
    "CE": "编译错误 (Compile Error)",
    "RE": "运行时错误 (Runtime Error)",
    "TLE": "超时 (Time Limit Exceeded)",
    "UE": "配置错误 (Unsupported)",
}


# 常见 MinGW-w64 安装目录（用于在本机未把 gcc 加入 PATH 时也能探测到）
_COMPILER_SEARCH_DIRS = [
    "C:/mingw64/bin",
    "C:/WinLibs/bin",
    "C:/Program Files/WinLibs/bin",
    "C:/Program Files/mingw-w64/x86_64-*/bin",
    "C:/Program Files (x86)/mingw-w64/i686-*/bin",
    "C:/Program Files/WinLibs/*/bin",
    "C:/WinLibs/*/bin",
    "C:/Program Files (x86)/Dev-Cpp/MinGW64/bin",
    "C:/Program Files/Dev-Cpp/MinGW64/bin",
    "C:/Program Files/CodeBlocks/MinGW/bin",
    "C:/Program Files (x86)/CodeBlocks/MinGW/bin",
    "C:/msys64/mingw64/bin",
    "C:/msys64/ucrt64/bin",
    "C:/cygwin64/bin",
]


def _find_compiler(name):
    """在 PATH 及常见安装目录中查找编译器可执行文件，返回其完整路径或 None。"""
    found = shutil.which(name)
    if found:
        return found
    import glob
    for pattern in _COMPILER_SEARCH_DIRS:
        for d in glob.glob(pattern):
            cand = os.path.join(d, name + ".exe") if os.name == "nt" else os.path.join(d, name)
            if os.path.isfile(cand):
                return cand
    return None


def detect_languages():
    """探测每种语言是否可用（编译器是否在 PATH 或常见安装目录中）。"""
    available = {}
    for key, cfg in LANGUAGES.items():
        if cfg["needs_compile"]:
            available[key] = _find_compiler(cfg["compile"][0]) is not None
        else:
            available[key] = True
    return available


def _normalize(text):
    """标准化输出用于比对：统一换行符、去掉每行首尾空白、去掉结尾多余空行。"""
    if text is None:
        text = ""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    lines = [ln.rstrip() for ln in lines]
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def _run_one(cfg, src, exe, input_text, expected, time_limit_ms, workdir, env=None):
    cmd = [c.format(exe=exe, src=src) for c in cfg["run"]]
    flags = 0
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    start = time.time()
    try:
        proc = subprocess.run(
            cmd,
            cwd=workdir,
            input=input_text,
            capture_output=True,
            text=True,
            timeout=time_limit_ms / 1000.0 + 1.0,
            creationflags=flags,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return "TLE", None, "运行超过时间限制"
    runtime = int((time.time() - start) * 1000)
    if proc.returncode != 0:
        err = (proc.stderr or "").strip()[:400]
        return "RE", runtime, err or "程序异常退出 (returncode=%s)" % proc.returncode
    if _normalize(proc.stdout) == _normalize(expected):
        return "AC", runtime, ""
    return "WA", runtime, ""


def _build_points(points, cases):
    """把测试用例按测试点分组，返回有序的测试点列表（每个含 cases）。"""
    pmap = {}
    order = []
    for p in (points or []):
        pid = p.get("id")
        pmap[pid] = {
            "id": pid,
            "name": p.get("name") or "测试点",
            "score": int(p.get("score") or 1),
            "cases": [],
        }
        order.append(pid)
    for c in (cases or []):
        pid = c.get("point_id")
        # 兼容：用例没有归属测试点，或归属的测试点未定义 → 自动生成一个独立测试点
        if pid is None or pid not in pmap:
            pid = pid if pid is not None else ("c%d" % c.get("id"))
            if pid not in pmap:
                pmap[pid] = {"id": pid, "name": "测试点", "score": 1, "cases": []}
                order.append(pid)
        pmap[pid]["cases"].append(c)
    return [pmap[k] for k in order]


def judge_submission(
    code,
    language,
    points,
    cases,
    time_limit_ms=2000,
    memory_limit_mb=256,
    output_limit_kb=2048,
):
    """
    判题主函数（测试点模型）。
    - points: [{"id","name","score"}, ...]  测试点（计分单元）定义
    - cases:  [{"point_id","input","expected","is_sample"}, ...]  测试用例
    一个测试点包含多个测试用例，只有该点下“全部用例通过”才算拿下该点并计分。
    返回: {"status","message","passed","total","score","max_score","points":[...],"compile_error"}
    """
    built = _build_points(points, cases)
    total_points = len(built)
    max_score = sum(int(p.get("score") or 1) for p in built)

    def _base(status, message, ce=""):
        return {
            "status": status,
            "message": message,
            "passed": 0,
            "total": total_points,
            "score": 0,
            "max_score": max_score,
            "points": [],
            "compile_error": ce,
        }

    cfg = LANGUAGES.get(language)
    if cfg is None:
        return _base("UE", "不支持的语言: %s" % language)
    compiler_path = None
    if cfg["needs_compile"]:
        compiler_path = _find_compiler(cfg["compile"][0])
    if cfg["needs_compile"] and compiler_path is None:
        return _base(
            "CE",
            "未找到编译器 %s，请先安装 MinGW-w64(gcc/g++)。" % cfg["compile"][0],
            ce="compiler %s not found on PATH" % cfg["compile"][0],
        )

    workdir = tempfile.mkdtemp(prefix="oj_")
    src = os.path.join(workdir, "solution." + cfg["ext"])
    try:
        with open(src, "w", encoding="utf-8", newline="") as f:
            f.write(code)
    except Exception as e:
        shutil.rmtree(workdir, ignore_errors=True)
        return _base("UE", "写入源码失败: %s" % e)

    # 编译
    if cfg["needs_compile"]:
        exe = os.path.join(workdir, "solution.exe" if os.name == "nt" else "solution")
        cmd = [c.format(exe=exe, src=src) for c in cfg["compile"]]
        # 用找到的完整编译器路径替换命令首元素（gcc / g++）
        cmd[0] = compiler_path
        # 按编译器版本插入合适的 -std 标准（老 gcc 不支持 c++17 等）
        cmd.insert(1, _compiler_std(compiler_path, cfg["lang"]))
        # 运行编译出的程序时，把编译器所在目录加入 PATH，
        # 确保 libgcc_s_seh-1.dll / libstdc++-6.dll 等运行时库能被找到。
        compiler_dir = os.path.dirname(compiler_path)
        run_env = dict(os.environ)
        run_env["PATH"] = compiler_dir + os.pathsep + run_env.get("PATH", "")
        try:
            proc = subprocess.run(
                cmd, cwd=workdir, capture_output=True, text=True, timeout=30
            )
        except subprocess.TimeoutExpired:
            shutil.rmtree(workdir, ignore_errors=True)
            return _base("CE", "编译超时（超过30秒）", ce="compile timeout")
        if proc.returncode != 0:
            shutil.rmtree(workdir, ignore_errors=True)
            return _base("CE", "编译错误", ce=(proc.stderr or "").strip()[:3000])
        exe_path = exe
    else:
        run_env = None
        exe_path = None

    # 逐测试点对下属用例运行；只有该点“全部用例 AC”才算通过并计分
    limit_order = ["CE", "UE", "RE", "TLE", "WA", "AC"]
    points_detail = []
    passed_points = 0
    earned_score = 0
    overall = "AC"
    for pt in built:
        pt_status = "AC"
        pt_passed = 0
        pt_runtime = None
        pt_error = ""
        for c in pt["cases"]:
            inp = c.get("input")
            if inp is None:
                inp = c.get("input_text", "")
            exp = c.get("expected")
            if exp is None:
                exp = c.get("expected_text", "")
            status, runtime, err = _run_one(
                cfg, src, exe_path, inp, exp,
                time_limit_ms, workdir, run_env,
            )
            if runtime is not None:
                pt_runtime = runtime if pt_runtime is None else max(pt_runtime, runtime)
            if status == "AC":
                pt_passed += 1
            else:
                if limit_order.index(status) < limit_order.index(pt_status):
                    pt_status = status
                    pt_error = err
        points_detail.append(
            {
                "id": pt["id"],
                "name": pt["name"],
                "score": pt["score"],
                "status": pt_status,
                "passed_cases": pt_passed,
                "total_cases": len(pt["cases"]),
                "runtime_ms": pt_runtime,
                "error": (pt_error or "")[:500],
            }
        )
        if pt_status == "AC":
            passed_points += 1
            earned_score += int(pt.get("score") or 1)
        if limit_order.index(pt_status) < limit_order.index(overall):
            overall = pt_status

    shutil.rmtree(workdir, ignore_errors=True)
    return {
        "status": overall,
        "message": STATUS_TEXT.get(overall, overall),
        "passed": passed_points,
        "total": total_points,
        "score": earned_score,
        "max_score": max_score,
        "points": points_detail,
        "compile_error": "",
    }


if __name__ == "__main__":
    print("可用语言探测:", detect_languages())
