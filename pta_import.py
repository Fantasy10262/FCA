#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pta_import.py — 从 PTA（拼题 A, pintia.cn）题目集批量抓取题目，
生成「在线判题平台」可直接导入的 sample_problems.json。

────────────────────────────────────────────────────────────
用法
────────────────────────────────────────────────────────────
  pip install requests
  python pta_import.py --psid <题目集ID> --token <登录token> --out sample_problems.json

如何拿到 token（两种任选其一）：
  方式 A（推荐，最稳）：
    1. 浏览器登录 https://pintia.cn
    2. 打开题目集页 https://pintia.cn/problem-sets/<题目集ID>/problems
    3. F12 → Network，点任意一个请求，复制请求头里的
         Authorization: Bearer xxxx.yyyy.zzzz
       其中 xxxx.yyyy.zzzz 就是 token，传给 --token
  方式 B：
    把浏览器里整段 Cookie 复制下来，传给 --cookie，脚本会自动提取 token。

拿到题目集 ID：
  题目集页 URL 形如
    https://pintia.cn/problem-sets/994805046380707840/problems
  其中 994805046380707840 就是 psid。

────────────────────────────────────────────────────────────
重要说明
────────────────────────────────────────────────────────────
  * PTA 只公开「样例」输入/输出；隐藏测试点不对外提供。
    本脚本生成的题目仅含样例（is_sample=true），导入后请务必在
    管理后台「测试用例」里为每题补全隐藏测试点，否则判题只有样例点。
  * 题目描述会被清理为纯文本（去除 HTML 标签）。
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


def clean_html(s):
    """把 PTA 的 HTML 描述清理成纯文本。"""
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", "\n", s)
    s = html.unescape(s)
    return "\n".join(line.strip() for line in s.splitlines() if line.strip())


def get_token(arg_token, arg_cookie):
    if arg_token:
        return arg_token.replace("Bearer ", "").strip()
    if arg_cookie:
        m = re.search(r"(?:^|;\s*)token=([^;]+)", arg_cookie)
        if m:
            return m.group(1)
    return None


def make_headers(token):
    h = {
        "User-Agent": "Mozilla/5.0 (compatible; pta-import/1.0)",
        "Accept": "application/json",
    }
    if token:
        h["Authorization"] = "Bearer " + token
    return h


def fetch_list(psid, token):
    """获取题目集下的题目列表（含 problemId / title）。"""
    url = f"{API}/problem-sets/{psid}/problems"
    r = requests.get(
        url, headers=make_headers(token),
        params={"limit": 200, "offset": 0}, timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    # 兼容不同返回结构
    if isinstance(data, dict):
        return data.get("problems") or data.get("data") or data.get("items") or []
    return data


def fetch_detail(pid, token):
    """获取单题详情（title / description / samples）。"""
    url = f"{API}/problems/{pid}"
    r = requests.get(url, headers=make_headers(token), timeout=30)
    r.raise_for_status()
    return r.json()


def to_problem(item, token):
    pid = item.get("problemId") or item.get("id") or item.get("problem_id")
    try:
        d = fetch_detail(pid, token)
    except Exception as e:
        print(f"  ! 详情获取失败 {pid}: {e}")
        d = item
    title = d.get("title") or item.get("title") or "未命名题目"
    desc = clean_html(d.get("description") or item.get("description") or "")
    samples = d.get("samples") or []
    tests = []
    for s in samples:
        inp = s.get("input") or s.get("inputs") or ""
        out = s.get("output") or s.get("outputs") or ""
        # 样例统一为字符串
        tests.append({
            "input": inp if isinstance(inp, str) else json.dumps(inp, ensure_ascii=False),
            "expected": out if isinstance(out, str) else json.dumps(out, ensure_ascii=False),
            "is_sample": True,
        })
    return {
        "title": title,
        "description": desc,
        "difficulty": "简单",
        "time_limit_ms": 2000,
        "memory_limit_mb": 256,
        "allowed_languages": ["c", "cpp", "py"],
        "default_language": "c",
        "tests": tests,
    }


def main():
    ap = argparse.ArgumentParser(description="从 PTA 题目集抓取题目并生成导入 JSON")
    ap.add_argument("--psid", required=True,
                    help="题目集 ID（URL 中 problem-sets/ 后面的数字）")
    ap.add_argument("--token", help="登录 token（Bearer 后面的部分）")
    ap.add_argument("--cookie", help="整段 Cookie（脚本会尝试提取 token）")
    ap.add_argument("--out", default="sample_problems.json", help="输出文件名")
    args = ap.parse_args()

    token = get_token(args.token, args.cookie)
    if not token:
        sys.exit("缺少登录凭据：请用 --token 或 --cookie 提供。")

    print(f"读取题目集 {args.psid} …")
    items = fetch_list(args.psid, token)
    print(f"找到 {len(items)} 道题，开始抓取详情…")

    problems = []
    for i, it in enumerate(items, 1):
        p = to_problem(it, token)
        problems.append(p)
        print(f"  [{i}/{len(items)}] {p['title']}  （样例 {len(p['tests'])} 个）")

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(problems, f, ensure_ascii=False, indent=2)
    print(f"\n已生成 {args.out}，共 {len(problems)} 道题。")
    print("→ 在管理后台「导入题目」上传该文件即可（记得补全隐藏测试点）。")


if __name__ == "__main__":
    main()
