# 部署到 Render（免费版）

本项目是 **Flask + SQLite + 本地子进程判题（gcc / g++ / python）**。
Vercel 等无服务器平台跑不了（缺编译器、SQLite 不持久、无常驻进程），
因此选 **Render 免费版**：它给真实容器、能在 Docker 里装 gcc/g++，判题正常，
代价是**磁盘临时**——适合演示，不适合正式考试存数据。

---

## 一、准备仓库（关键：别把密钥/数据库提交上去）

项目根已配好 `.gitignore` 和 `.dockerignore`，会自动排除 `.workbuddy/`（本地记忆，可能含明文密码）
和 `data/`（SQLite 数据库）。请确认推送前这些不会被提交。

```bash
# 若还没有 Git 仓库
git init
git add -A                      # .gitignore 会自动跳过 .workbuddy 和 data/
git commit -m "feat: ready for Render deploy"
git branch -M main
git remote add origin <你的 GitHub 仓库地址>
git push -u origin main
```

---

## 二、在 Render 上创建服务

1. 打开 https://render.com ，用 GitHub 登录（免费，无需绑卡）。
2. 控制台 → **New** → **Blueprint**（或 **Web Service**）。
3. 连接你的 GitHub 仓库，选择本项目。
4. 如果用 Blueprint：Render 会读取仓库里的 `render.yaml`，自动建一个 **Free** 计划的 Web Service。
   - 若手动建 Web Service：Environment 选 **Docker**，Plan 选 **Free**，`dockerfilePath` 默认 `./Dockerfile`。
5. 在 Environment 里确认：
   - `OJ_SECRET` 由 Render **自动生成随机值**（会话密钥，不要手填明文）。
   - `PYTHONUNBUFFERED=1`（日志实时）。
6. 点 **Deploy**。首次构建会拉 `python:3.11-slim` + 装 gcc/g++，约 1–3 分钟。

部署完成后，Render 分配一个 `https://<服务名>.onrender.com` 域名（已自带 HTTPS）。

---

## 三、启动后会自动初始化

容器启动命令是 `waitress-serve --port=$PORT app:app`，导入 `app` 时即执行 `init_db()`：

- 自动建所有表；
- **空库时自动播种**：管理员账号、示例学生、一道「A+B」示例题、学习中心（B 站宝藏教程）；
- 判题引擎需要的 gcc/g++ 已在镜像里装好，C/C++/Python 提交都能编译运行。

> 管理员登录（种子账号）：
> 学号 `2025081034` / 姓名 `史稳祺` / 密码 `Ss15855484912`
> 可在管理后台改密码、用 CSV 批量导入学生、用 `pta_import.py` 批量导入更多题目。

---

## 四、免费版重要限制（请一定知悉）

1. **磁盘是临时的**：容器空闲 15 分钟后会休眠，被唤醒或重新部署时，本地磁盘会重置为镜像初始状态
   ——也就是数据库会**回到初始种子**（新增的学生、提交记录会丢）。期间活跃时的数据保存在当前容器里，没问题。
   → 演示足够；**正式考试/长期使用请升级 Render 付费版（自带持久磁盘）**，或改用 **Fly.io**（免费额度含 3GB 持久卷）。
2. **休眠冷启动**：首次访问休眠中的服务会有几秒延迟（容器被唤醒）。
3. **自定义域名**：在 Render 控制台 → 该服务 → **Settings → Custom Domains** 绑定你自己的域名（需自行配 DNS）。

---

## 五、验证是否真的能判题

部署后登录，进任意题目提交一段 C/C++/Python 的 A+B 代码，看是否返回「通过 / 测试点明细」。
若判题失败，去 Render 控制台 → 该服务 → **Logs** 看 `可用语言:` 输出，确认 `c / cpp / py` 都为 True
（说明 gcc/g++/python 探测成功）。

---

## 六、想换平台？

- **Fly.io（推荐长期免费）**：真实 VM + 免费 3GB 持久卷，数据不丢。把 `Dockerfile` 直接用于 `fly launch` 即可，
  再挂一个 Volume 到 `/app/data` 即可持久化 SQLite。
- **云服务器（ECS / 轻量应用服务器）**：按 `部署指南.md` 方式二/三，用 `waitress-serve` + Nginx + HTTPS，最稳。
