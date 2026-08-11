#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
pta_import.py — 从 PTA（拼题 A, pintia.cn）题目集批量抓取题目，
生成「在线判题平台」可直接导入的 sample_problems.json。

────────────────────────────────────────────────────────────
用法
────────────────────────────────────────────────────────────
  pip install requests
  python pta_import.py --psid <题目集ID> --cookie <整段Cookie> --out sample_problems.json

如何拿到 Cookie（最稳，推荐）：
  1. 浏览器登录 https://pintia.cn，打开你的题目集 / 考试页。
  2. F12 → Network → 随便点一条 pintia.cn/api 开头的 XHR 请求
     （例如 problem-status）。
  3. 右键该请求 → 复制 → 复制为 cURL（bash）。
  4. 把整段 cURL 粘到后台「从 PTA 一键导入」的 Cookie 框里即可。
     （脚本会自动从 cURL 里抠出 Cookie、题目集 ID、考试 ID、校验头）

说明：
  * PTA 的登录令牌在 HttpOnly Cookie 里（PTASession / JSESSIONID），
    浏览器 Console 的 document.cookie 读不到，必须从 Network 复制 cURL。
  * 考试类题目集（URL 带 /exam/）走专门的 exam 接口；普通题目集走
    problem-sets 接口。脚本会自动判断。

────────────────────────────────────────────────────────────
重要说明
────────────────────────────────────────────────────────────
  * PTA 只公开「样例」输入/输出（exampleTestDatas）；隐藏测试点不对外提供。
    本脚本生成的题目仅含样例（is_sample=true），导入后请务必在
    管理后台「测试用例」里为每题补全隐藏测试点，否则判题只有样例点。
  * 题目描述保留原始文本（去除 HTML 标签、反转义实体）。
  * 脚本只读取你自己的题目集，请在遵守 PTA 使用条款的前提下使用。

依赖：requests  （pip install requests）
"""
import argparse
import html
import json
import re
import sys

try:
    import requests
except ImportError:
    sys.exit("请先安装 requests：pip install requests")

API = "https://pintia.cn/api"

# 使用更接近浏览器的 UA，降低被风控拦截的概率
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def extract_content_samples(content):
    """从题目描述里抠「输入样例 / 输出样例」的 fenced 代码块。

    PTA 很多题目的样例只嵌在正文里（```in / ```out 或纯 ```），
    exampleTestDatas 为空。这里兜底解析，保证导入后有测试点。
    """
    if not content:
        return []
    inp = re.findall(r"输入样例\d*[：:]\s*```(?:in)?\s*\n(.*?)```", content, re.S)
    out = re.findall(r"输出样例\d*[：:]\s*```(?:out)?\s*\n(.*?)```", content, re.S)
    samples = []
    for i in range(min(len(inp), len(out))):
        samples.append({
            "input": (inp[i].strip() + "\n") if inp[i].strip() else "",
            "expected": (out[i].strip() + "\n") if out[i].strip() else "",
            "is_sample": True,
        })
    return samples


def clean_html(s):
    """把 PTA 的 HTML/Markdown 描述清理成纯文本（保留换行）。"""
    if not s:
        return ""
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "\n", s)
    s = html.unescape(s)
    return "\n".join(line.strip() for line in s.splitlines() if line.strip())


def resolve_token(auth):
    """从一段凭据里解析出登录 token（Bearer / Cookie 里的 token=）。"""
    auth = (auth or "").strip()
    if not auth:
        return None
    if "token=" in auth or ";" in auth:
        m = re.search(r"(?:^|;\s*)token=([^;]+)", auth)
        return m.group(1) if m else None
    return auth.replace("Bearer ", "").strip()


def parse_curl_auth(auth):
    """若 auth 是一整段 curl 命令（来自 Network「复制为 cURL」），
    提取其中的 Cookie、Authorization 头、x-lollipop 校验头，以及
    URL 里隐含的 exam / psid。返回 (cookie, token, lollipop, exam, psid)。
    """
    auth = (auth or "").strip()
    cookie = token = lollipop = exam = psid = None
    if not auth.startswith("curl "):
        return cookie, token, lollipop, exam, psid
    for m in re.finditer(r"-H\s+['\"]([^'\"]*)['\"]", auth):
        h = m.group(1)
        if h.lower().startswith("cookie:"):
            cookie = h.split(":", 1)[1].strip()
        elif h.lower().startswith("authorization:"):
            token = h.split(":", 1)[1].strip()
        elif h.lower().startswith("x-lollipop:"):
            lollipop = h.split(":", 1)[1].strip()
    if cookie is None:
        m = re.search(r"--cookie\s+['\"]([^'\"]*)['\"]", auth)
        if m:
            cookie = m.group(1)
    m = re.search(r"--url\s+['\"]([^'\"]+)['\"]", auth)
    if not m:
        m = re.search(r"curl\s+['\"]?(https?://[^'\"]+)['\"]?", auth)
    if m:
        url = m.group(1)
        em = re.search(r"/exams/(\d+)/problem-sets/(\d+)", url)
        if em:
            exam, psid = em.group(1), em.group(2)
        else:
            pm = re.search(r"/problem-sets/(\d+)", url)
            if pm:
                psid = pm.group(1)
    return cookie, token, lollipop, exam, psid


def make_headers(token, cookie=None, lollipop=None):
    h = {
        "User-Agent": BROWSER_UA,
        "Accept": "application/json;charset=UTF-8",
        "Referer": "https://pintia.cn/",
    }
    if cookie:
        h["Cookie"] = cookie
    if token:
        h["Authorization"] = "Bearer " + token
    if lollipop:
        h["x-lollipop"] = lollipop
    return h


def fetch_list(psid, token, cookie=None, lollipop=None, exam=None):
    """获取题目集下的题目列表。
    考试类题目集走 /exams/{exam}/problem-sets/{psid}/problem-status；
    普通题目集走 /problem-sets/{psid}/problems。
    返回 list，每项至少含 id。
    """
    if exam:
        url = f"{API}/exams/{exam}/problem-sets/{psid}/problem-status"
        r = requests.get(url, headers=make_headers(token, cookie, lollipop), timeout=30)
        r.raise_for_status()
        return r.json().get("problemStatus", [])
    url = f"{API}/problem-sets/{psid}/problems"
    r = requests.get(url, headers=make_headers(token, cookie, lollipop),
                     params={"limit": 200, "offset": 0}, timeout=30)
    r.raise_for_status()
    data = r.json()
    if isinstance(data, dict):
        return data.get("problems") or data.get("data") or data.get("items") or []
    return data


def fetch_detail(pid, token, cookie=None, lollipop=None, exam=None, psid=None):
    """获取单题详情。
    考试类：/problem-sets/{psid}/exam-problems/{pid} → problemSetProblem
    普通类：/problem-sets/{psid}/problems/{pid}
    """
    if exam:
        url = f"{API}/problem-sets/{psid}/exam-problems/{pid}"
    else:
        url = f"{API}/problem-sets/{psid}/problems/{pid}"
    r = requests.get(url, headers=make_headers(token, cookie, lollipop), timeout=30)
    r.raise_for_status()
    return r.json()


def to_problem(item, token, cookie=None, lollipop=None, exam=None, psid=None):
    """把一条题目解析成 FCA 导入格式。"""
    pid = item.get("id") or item.get("problemId") or item.get("problem_id")
    try:
        d = fetch_detail(pid, token, cookie, lollipop, exam, psid)
    except Exception as e:
        print(f"  ! 详情获取失败 {pid}: {e}")
        d = {}
    # 考试类详情在 problemSetProblem 嵌套层
    psp = d.get("problemSetProblem", d)
    title = psp.get("title") or item.get("title") or "未命名题目"
    desc = clean_html(psp.get("content") or item.get("content") or item.get("description") or "")

    cfg = {}
    pc = psp.get("problemConfig") or {}
    if isinstance(pc, dict):
        cfg = pc.get("programmingProblemConfig") or {}
    time_limit_ms = int(cfg.get("timeLimit", 2000)) if cfg.get("timeLimit") else 2000
    # PTA memoryLimit 单位为 KB，转 MB
    mem_kb = cfg.get("memoryLimit")
    memory_limit_mb = int(mem_kb / 1024) if mem_kb else 256
    if memory_limit_mb < 1:
        memory_limit_mb = 256

    examples = cfg.get("exampleTestDatas") or []
    tests = []
    for ex in examples:
        tests.append({
            "input": ex.get("input") or "",
            "expected": ex.get("output") or "",
            "is_sample": True,
        })
    # 兜底：部分题目样例只嵌在正文里（exampleTestDatas 为空），从 content 抠
    if not tests:
        tests = extract_content_samples(desc)
    return {
        "title": title,
        "description": desc,
        "difficulty": "简单",
        "time_limit_ms": time_limit_ms,
        "memory_limit_mb": memory_limit_mb,
        "allowed_languages": ["c", "cpp", "py"],
        "default_language": "c",
        "tests": tests,
    }


def scrape_problem_set(psid, auth, exam=None):
    """抓取整个题目集，返回题目 dict 列表（与 import_problems_json 兼容）。

    psid: 题目集 ID（纯数字）或题目集/考试页面链接（会自动提取 ID）
    auth: 登录 Cookie，或整段 curl 命令（推荐，可自动抠出 exam/psid）
    exam: 考试 ID（可选，普通题目集留空；从 curl URL 自动识别）
    抛错信息已本地化为中文，便于 Web 层直接 flash。
    """
    # 兼容：整段 curl 命令
    cookie_h, token_h, lollipop_h, exam_h, psid_h = parse_curl_auth(auth)
    if psid_h and re.fullmatch(r"\d+", str(psid_h)):
        psid = psid_h
    if exam_h:
        exam = exam_h

    # 支持直接粘贴整条链接作为 psid
    m = re.search(r"problem-sets/(\d+)", str(psid or ""))
    psid = m.group(1) if m else re.sub(r"\D", "", str(psid or ""))
    if not psid:
        raise ValueError("未识别到题目集 ID，请填写数字 ID 或题目集/考试页面链接")

    if cookie_h:
        cookie = cookie_h
    else:
        cookie = auth if (";" in auth or "token=" in auth) else None
    token = resolve_token(token_h or auth)
    lollipop = lollipop_h

    if not token and not cookie:
        raise ValueError("无法解析出登录凭据，请确认粘贴的是 Network 里某条 pintia.cn/api 请求的「复制为 cURL」")

    try:
        items = fetch_list(psid, token, cookie, lollipop, exam)
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 401:
            raise ValueError("PTA 登录失效：Cookie 已过期，请重新从 Network 复制最新 Cookie")
        if e.response is not None and e.response.status_code in (403, 406):
            raise ValueError("PTA 拒绝访问：可能是题目集 ID 不正确、或无权限；考试类题目集请确保粘贴的是考试页的 cURL")
        raise ValueError("读取题目集失败（HTTP 错误）：%s" % e)
    except requests.RequestException as e:
        raise ValueError("网络请求失败，请检查服务器能否访问 pintia.cn：%s" % e)

    if not items:
        raise ValueError("该题目集下没有题目，或题目集 ID 不正确 / 无权限访问")

    problems = []
    total = len(items)
    for i, it in enumerate(items, 1):
        try:
            p = to_problem(it, token, cookie, lollipop, exam, psid)
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 401:
                raise ValueError("PTA 登录失效：Cookie 已过期，请重新从 Network 复制最新 Cookie")
            print(f"  ! 第 {i} 题解析失败: {e}")
            continue
        problems.append(p)
        print(f"  [{i}/{total}] {p['title']}  （样例 {len(p['tests'])} 个）")
    return problems


def main():
    ap = argparse.ArgumentParser(description="从 PTA 题目集抓取题目并生成导入 JSON")
    ap.add_argument("--psid", required=True,
                    help="题目集 ID（URL 中 problem-sets/ 后面的数字，或整条链接）")
    ap.add_argument("--exam", help="考试 ID（考试类题目集需要；普通集留空）")
    ap.add_argument("--token", help="登录 token（Bearer 后面的部分）")
    ap.add_argument("--cookie", help="整段 Cookie 或整段 cURL（推荐）")
    ap.add_argument("--out", default="sample_problems.json", help="输出文件名")
    args = ap.parse_args()

    print(f"读取题目集 {args.psid} …")
    try:
        problems = scrape_problem_set(args.psid, args.cookie or args.token, args.exam)
    except ValueError as e:
        sys.exit(str(e))
    except Exception as e:
        sys.exit("抓取失败：" + str(e))

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(problems, f, ensure_ascii=False, indent=2)
    print(f"\n已生成 {args.out}，共 {len(problems)} 道题。")
    print("→ 在管理后台「导入题目」上传该文件即可（记得补全隐藏测试点）。")


if __name__ == "__main__":
    main()
