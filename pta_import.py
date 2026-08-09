#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
pta_import.py — 从 PTA（拼题 A, pintia.cn）题目集批量抓取题目，
生成「在线判题平台」可直接导入的 sample_problems.json。

────────────────────────────────────────────────────────────
用法
────────────────────────────────────────────────────────────
  pip install requests
  python pta_import.py --psid <题目集ID> --token <登录token> --out sample_problems.json

如何拿到 token / Cookie（两种任选其一）：
  方式 A（推荐，最稳）：
    1. 浏览器登录 https://pintia.cn
    2. 打开题目集页 https://pintia.cn/problem-sets/<题目集ID>/problems
    3. 在页面上点一下「复制 PTA Cookie」书签（见下方说明），弹窗里复制整串
       传给 --cookie 即可（脚本会自动提取里面的 token）。
  方式 B：
    按 F12 → Console，输入 document.cookie 回车，复制整串传给 --cookie。

拿到题目集 ID：
  题目集页 URL 形如
    https://pintia.cn/problem-sets/994805046380707840/problems
  其中 994805046380707840 就是 psid（也可直接把整串链接粘进来，会自动提取）。

────────────────────────────────────────────────────────────
重要说明
────────────────────────────────────────────────────────────
  * PTA 只公开「样例」输入/输出；隐藏测试点不对外提供。
    本脚本生成的题目仅含样例（is_sample=true），导入后请务必在
    管理后台「测试用例」里为每题补全隐藏测试点，否则判题只有样例点。
  * 题目描述会被清理为纯文本（去除 HTML 标签）。
  * 脚本只读取你自己的题目集，请在遵守 PTA 使用条款的前提下使用。

────────────────────────────────────────────────────────────
一句话书签（复制到浏览器地址栏、拖到书签栏保存；在 pintia 页面点它即复制 Cookie）
────────────────────────────────────────────────────────────
  javascript:(function(){var c=document.cookie;if(!c){alert('请先登录 pintia.cn');return;}var t=c.match(/(?:^|;\s*)token=([^;]+)/);prompt('复制下面这串 Cookie，粘贴到后台「从 PTA 一键导入」：', t?t[1]:c);})();

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


def clean_html(s):
    """把 PTA 的 HTML 描述清理成纯文本。"""
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", "\n", s)
    s = html.unescape(s)
    return "\n".join(line.strip() for line in s.splitlines() if line.strip())


def resolve_token(auth):
    """从一段凭据里解析出登录 token。

    auth 可能是：
      - 一段 Cookie（含 token=... 或整段 document.cookie）
      - 一个 Bearer token（JWT，形如 xxx.yyy.zzz）
    返回 token 字符串，解析不到返回 None。
    """
    auth = (auth or "").strip()
    if not auth:
        return None
    # 看起来像 Cookie（含分隔符）
    if "token=" in auth or ";" in auth:
        m = re.search(r"(?:^|;\s*)token=([^;]+)", auth)
        return m.group(1) if m else None
    # 否则当作裸 token / Bearer token
    return auth.replace("Bearer ", "").strip()


def make_headers(token):
    h = {
        "User-Agent": BROWSER_UA,
        "Accept": "application/json",
        "Referer": "https://pintia.cn/",
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


def scrape_problem_set(psid, auth):
    """抓取整个题目集，返回题目 dict 列表（与 import_problems_json 兼容）。

    psid: 题目集 ID（纯数字）或题目集页面链接（会自动提取 ID）
    auth: 登录 token 或整段 Cookie
    抛错信息已本地化为中文，便于 Web 层直接 flash。
    """
    # 支持直接粘贴整条链接
    m = re.search(r"problem-sets/(\d+)", psid or "")
    psid = m.group(1) if m else re.sub(r"\D", "", psid or "")
    if not psid:
        raise ValueError("未识别到题目集 ID，请填写数字 ID 或题目集页面链接")

    token = resolve_token(auth)
    if not token:
        raise ValueError("无法从凭据中解析出登录 token，请确认粘贴的是完整 Cookie 或 Bearer token")

    try:
        items = fetch_list(psid, token)
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 401:
            raise ValueError("PTA 登录失效：Cookie 已过期，请重新在 PTA 页面点书签复制最新 Cookie")
        raise ValueError("读取题目集失败（HTTP 错误）：%s" % e)
    except requests.RequestException as e:
        raise ValueError("网络请求失败，请检查服务器能否访问 pintia.cn：%s" % e)

    if not items:
        raise ValueError("该题目集下没有题目，或题目集 ID 不正确 / 无权限访问")

    problems = []
    total = len(items)
    for i, it in enumerate(items, 1):
        try:
            p = to_problem(it, token)
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 401:
                raise ValueError("PTA 登录失效：Cookie 已过期，请重新复制最新 Cookie")
            print(f"  ! 第 {i} 题解析失败: {e}")
            continue
        problems.append(p)
        print(f"  [{i}/{total}] {p['title']}  （样例 {len(p['tests'])} 个）")
    return problems


def main():
    ap = argparse.ArgumentParser(description="从 PTA 题目集抓取题目并生成导入 JSON")
    ap.add_argument("--psid", required=True,
                    help="题目集 ID（URL 中 problem-sets/ 后面的数字，或整条链接）")
    ap.add_argument("--token", help="登录 token（Bearer 后面的部分）")
    ap.add_argument("--cookie", help="整段 Cookie（脚本会尝试提取 token）")
    ap.add_argument("--out", default="sample_problems.json", help="输出文件名")
    args = ap.parse_args()

    print(f"读取题目集 {args.psid} …")
    try:
        problems = scrape_problem_set(args.psid, args.token or args.cookie)
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
