# 部署到 Railway（免费档，真实容器 + 可装 gcc）

> 适用：Render 的 GitHub 登录抽风、或想换条更顺的部署路时使用。
> 代码已推到 `https://github.com/Fantasy10262/FCA`，本仓库含 `Dockerfile` + `railway.json`，Railway 拉去即部署。

## 一、Railway 账号（用 GitHub 登录）
1. 打开 https://railway.app → 右上角 **Login** → **GitHub**（无痕窗口 + 关广告插件，避免 OAuth 回跳被拦）。
2. 授权 Railway 访问你的 GitHub。
3. 新账号有 **$5 试用额度**（多数情况**无需绑卡**即可部署）；额度用完或要持久磁盘才需绑卡。
   - 免费档是真实容器，能装 gcc/g++，**判题（C/C++/Python）正常**。

## 二、一键部署
1. 控制台 **New Project** → **Deploy from GitHub repo**。
2. 选 **Fantasy10262/FCA** → Railway 读 `railway.json`（builder=DOCKERFILE）→ 自动构建镜像并部署。
3. 构建约 1–3 分钟（装 gcc/g++ + waitress）。完成后 Railway 给一个 `*.railway.app` 域名，自带 HTTPS。
4. 在 **Settings → Domains** 可看到正式地址，也可绑定自定义域名。

## 三、环境变量（可选）
- `OJ_SECRET`：会话签名密钥。Railway 不会自动生成，建议在 **Variables** 里手动加一个随机值（例如 `openssl rand -hex 32` 的结果）。不填也能跑，只是每次重启会话失效。
- 其余无需配置；`PORT` 由 Railway 自动注入，Dockerfile 已读。

## 四、上线后
- 登录：管理员 学号 `2025081034` / 姓名 `史稳祺` / 密码 `Ss15855484912`。
- 空库自动 seed：管理员 + 示例学生 + A+B 题 + 学习中心（B 站宝藏教程）。
- 判题：C/C++/Python 在容器里编译运行，正常。

## 五、免费档限制（功能层面，不是钱）
- 容器休眠（无流量一段时间后）+ 磁盘临时：重新部署/唤醒后数据库重置回初始种子（新增学生/提交会丢）。
- 演示 / 给同学看够用；正式考试请升级 Railway 持久卷或换 Fly.io（免费额度含 3GB 持久卷）。

## 六、与 Render 的差异
| 项 | Railway | Render |
|---|---|---|
| 构建 | Dockerfile（railway.json） | Dockerfile（render.yaml） |
| GitHub 登录 | 同样需要（OAuth） | 同样需要（OAuth） |
| 免费 | $5 试用额度，多无需绑卡 | $0/月，无需绑卡 |
| 判题 | ✅ gcc/g++ 可装 | ✅ gcc/g++ 可装 |
| 磁盘 | 临时 | 临时 |

> 两个平台的 GitHub OAuth 登录是同一套机制；若一方被浏览器/网络拦截，另一方通常也拦。真被拦时优先用**无痕窗口**或**换网络（手机热点）**解决，而不是换平台。
