# 免费持久化部署：Supabase(Postgres) + 免费应用托管 + 在线监控保活

目标：把数据库从「容器临时盘」换成 **Supabase 免费 Postgres**（永久不删），
应用托管用 **Render 免费档**（或留在 Railway 试用），再用 **UptimeRobot** 每 5 分钟
ping 一次防止免费档休眠（冷启动几十秒的问题彻底消失）。整条链路 **每月 ¥0**。

代码已支持双引擎：设了 `DATABASE_URL` 就自动用 Postgres，没设就回退 SQLite。
本仓库 `app.py` / `db.py` 已就绪，无需改代码。

---

## 第 1 步：建 Supabase 免费库

1. 打开 https://supabase.com → **Start your project** → 用 GitHub 登录（同上 OAuth）。
2. **New project**：
   - Name：`fca-oj`（随便取）
   - Database Password：记下来（后面 URI 里要用）
   - Region：选 **Northeast Asia (Tokyo)** 或离你近的，延迟低
   - Pricing Plan：**Free**（500MB，永久不删）
3. 等个 1–2 分钟建好，进项目 → 左侧 **Project Settings → Database**。
4. 在 **Connection string** 里选 **URI**，复制那一行，形如：
   ```
   postgresql://postgres:YOUR_PASSWORD@db.XXXX.supabase.co:5432/postgres
   ```
   - 把 `YOUR_PASSWORD` 换成你刚才设的密码。
   - **末尾加上 `?sslmode=require`**（Supabase 强制 SSL，不加连不上）：
   ```
   postgresql://postgres:你的密码@db.XXXX.supabase.co:5432/postgres?sslmode=require
   ```
   这就是你的 `DATABASE_URL`。

---

## 第 2 步：把 DATABASE_URL 填到托管平台

### 方案 A：留在 Railway（当前已在用，试用期内）
1. railway.app → 打开 `FCA` 服务 → **Variables** → **New Variable**。
2. Key：`DATABASE_URL`，Value：上面那串 URI。
3. 保存后 Railway 自动重新部署；日志里看到 `CREATE TABLE` / seed 成功即 OK。

### 方案 B：迁到 Render 免费档（Railway 试用到期后用）
1. render.com → **New → Blueprint** → 连 `Fantasy10262/FCA`（读仓库里的 `render.yaml`）。
2. 建好后进服务 → **Environment** → Add `DATABASE_URL` = 上面的 URI（render.yaml 里已注释好，直接图形界面加）。
3. **Manual Deploy → Deploy latest commit**。

> 切换 Postgres 是**全新空库**，应用启动会自动播种：
> 管理员（学号 `2025081034` / 姓名 `史稳祺` / 密码 `Ss15855484912`）、
> 示例题 A+B、学习中心 B 站教程。原 Railway 上的临时 SQLite 数据不迁移（本来就是临时的）。

---

## 第 3 步：UptimeRobot 保活（消除冷启动等待）

1. 打开 https://uptimerobot.com → 免费注册。
2. **Add New Monitor**：
   - Monitor Type：**HTTP(s)**
   - Friendly Name：`fca-oj`
   - URL：你的站点地址，例如 `https://fca-production-b425.up.railway.app/login`
     （用 `/login` 而非 `/`，因为 `/` 可能重定向，监控判 200 更稳）
   - Monitoring Interval：**Every 5 minutes**
3. 保存。

效果：免费档规则是「15 分钟无流量才休眠」，每 5 分钟被 ping 一次就**永远醒着**
→ 任何时间打开都秒开，且 24h 不睡 ≈ 720 小时/月，低于免费档 750 小时上限，**仍免费**。

> 顺手也给 Supabase 保个活（可选）：再加一个 monitor 指向
> `https://你的域名/login` 即可（访问会触发一次 DB 查询，防止 Supabase 空闲回收）。

---

## 第 4 步：验收（部署完让我跑，或你自己看）

- 站点能打开、管理员能登录、题目列表/提交/学习中心后台正常。
- 故意「冷场」半小时后第一个打开也不再慢（因为有 ping 保活）。

---

## 安全收尾（重要）

- 把之前给的 GitHub **classic token（`ghp_...`）去 GitHub → Settings → Developer
  settings → PAT → Revoke 掉**，避免泄露。
- `OJ_SECRET` 由托管平台自动生成，不用管。
- `DATABASE_URL` 只在平台环境变量里，绝不要写进代码或提交。

---

## 故障排查

| 现象 | 原因 / 处理 |
|---|---|
| 部署后 500 / 日志 `SSL` 相关 | URI 没加 `?sslmode=require`，补上重部署 |
| 日志 `relation "users" does not exist` | 部署前没连上 Supabase，或 DATABASE_URL 填错；确认 URI 能连通 |
| 首次连 Supabase 慢/超时 | Supabase 新库偶尔冷启，等 1 分钟重试；或确认 Region 选对 |
| Railway 试用到期停服 | 按方案 B 迁 Render 免费档，或升级付费 |
| psycopg2 导入报错 | 已写进 `requirements.txt` 并装进 Docker 镜像，本地 SQLite 不受影响 |
