#!/usr/bin/env bash
# FCA 安全推送脚本（供"改代码后更新网站"反复使用）
# 用法:  TOKEN=ghp_你的token ./push.sh "本次改动说明"
# 特性:
#   - 绕过 Windows Git Bash 的 schannel 证书吊销问题
#   - token 只从环境变量读取，绝不写入 .git/config / 不落盘（不加 -u）
#   - push 到 master，Railway 开 Auto deploy 会自动重新部署
set -e

TOKEN="${TOKEN:-}"
if [ -z "$TOKEN" ]; then
  echo "❌ 请先设置 GitHub token，例如："
  echo "   TOKEN=ghp_xxx ./push.sh \"更新说明\""
  exit 1
fi

REPO="Fantasy10262/FCA"
BRANCH="master"
MSG="${1:-update}"

# 切到脚本所在目录（项目根）
cd "$(dirname "$0")"

git add -A
git commit -m "$MSG" || echo "（无新改动，跳过 commit）"

GIT_SSL_NO_VERIFY=true git -c http.schannelCheckRevoke=false \
  push "https://${TOKEN}@github.com/${REPO}.git" "$BRANCH"

echo "✅ 已推送到 GitHub，Railway 会自动部署（去控制台 Deployments 看新 commit）"
