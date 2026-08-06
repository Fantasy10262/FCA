# FCA 迁移到自有云服务器 · 部署指南

> 适用场景：Railway 一个月试用到期后，把 FCA 迁到自有 VPS（云服务器/学生机）。
> 前置：仓库已有 `Dockerfile`（含 gcc/g++ + waitress）与双引擎 `db.py`，本迁移**零代码改动**。

---

## 一、准备一台 VPS
| 路线 | 配置 | 参考年成本 | 说明 |
|------|------|------------|------|
| **A：复用 Supabase** | 2核2G 轻量 | 阿里云秒杀38元 / 腾讯云校园~104元 / 雨云2核2G~25元/月 | 数据库继续用 Supabase 免费 Postgres，**零迁移** |
| **B：整机自托管** | 2核4G | 阿里云199元 / 雨云~35元/月（不限流量） | app + Postgres 同机 docker-compose，数据 100% 自控 |

- 系统选 **Ubuntu 22.04 / 24.04**。
- 国内节点绑自定义域名需 **ICP 备案**（约 1–2 周）；用服务器 **IP 直接访问无需备案**。

---

## 二、VPS 初始化（SSH 登录后）
```bash
# 安装 Docker（官方）
curl -fsSL https://get.docker.com | sh
# 国内可用镜像加速（二选一）：
# curl -fsSL https://get.daocloud.io/docker | sh
systemctl enable --now docker

# 放行端口（云厂商安全组也要放通 80/443）
ufw allow 22,80,443/tcp
ufw enable
```

---

## 三、拉取代码
```bash
git clone https://github.com/Fantasy10262/FCA.git
cd FCA
```

---

## 四、配置环境变量
```bash
cp deploy/.env.example deploy/.env
nano deploy/.env
```
- `OJ_SECRET`：改成随机串 → `openssl rand -hex 32`
- **路线 A**：填 `DATABASE_URL`（Supabase 控制台 Connect → Direct → Transaction pooler 复制，末尾加 `?sslmode=require`）
- **路线 B**：填 `POSTGRES_PASSWORD`（→ `openssl rand -hex 16`），`DATABASE_URL` 留空

---

## 五、启动
```bash
# 路线 A
docker compose -f deploy/docker-compose.supabase.yml --env-file deploy/.env up -d --build

# 路线 B
docker compose -f deploy/docker-compose.postgres.yml --env-file deploy/.env up -d --build
```

---

## 六、验证
```bash
docker compose -f deploy/docker-compose.supabase.yml ps
curl http://localhost:5000/healthz
# 浏览器打开 http://<服务器IP>/login
```
预期：`/healthz` 返回 `is_postgres:true`、`db_init_error:null`。
> 路线 B 首次启动：app 会等 Postgres 健康后再 init_db 建表，耐心等 1 分钟左右再 curl。

---

## 七、（可选）上 HTTPS
1. 域名 A 记录指向服务器 IP（国内需先完成 ICP 备案）。
2. 当前 nginx 已是 HTTP 反代，先用 IP 验证可通。
3. 签发证书（certbot）：
   ```bash
   docker run --rm \
     -v $PWD/deploy/certs:/etc/letsencrypt \
     -v $PWD/deploy/www:/var/www/certbot \
     certbot/certbot certonly --webroot -w /var/www/certbot \
     -d your.domain.com --email you@example.com --agree-tos

   # 取出 nginx 需要的两份文件
   cp deploy/certs/live/your.domain.com/fullchain.pem deploy/certs/fullchain.pem
   cp deploy/certs/live/your.domain.com/privkey.pem   deploy/certs/privkey.pem
   ```
4. 把 `deploy/nginx/app.ssl.conf` 内容覆盖到 `deploy/nginx/app.conf`，reload：
   ```bash
   docker compose -f deploy/docker-compose.supabase.yml exec nginx nginx -s reload
   ```
5. 续期：`./deploy/renew.sh`（先改里面 `DOMAIN` 变量）。
6. 小提示：上 HTTPS 后建议在 `app.py` 加 `ProxyFix` 并启用 `SESSION_COOKIE_SECURE`（见文末附）。

---

## 八、数据迁移说明（可选）
- **路线 A → 换服务器仍用 Supabase**：什么都不用迁，`DATABASE_URL` 不变，数据一直在 Supabase。
- **路线 A → 路线 B（搬到自托管 Postgres）**：需从 Supabase 导出再导入新 Postgres（`pg_dump` / `pg_restore` 或逻辑导出）。需要时可让我补专门脚本。

---

## 九、与 Railway 的衔接（零停机）
- 迁移期间 **Railway 站点继续跑**，不影响使用。
- 切流量：把域名解析指向新服务器 IP，或通知用户新地址；Railway 可保留做备份/回滚。
- 顺序：先在新服务器起好并验证 `/healthz`，再切 DNS。

---

## 十、日常运维
```bash
# 看日志
docker compose -f deploy/docker-compose.supabase.yml logs -f app
# 重启 app
docker compose -f deploy/docker-compose.supabase.yml restart app
# 升级代码（拉新后重建）
git pull && docker compose -f deploy/docker-compose.supabase.yml --env-file deploy/.env up -d --build
# 路线 B 备份数据库
docker exec fca-db pg_dump -U postgres oj > oj_$(date +%F).sql
```

---

## 附：HTTPS 后让 Flask 正确识别（可选代码改动）
在 `app.py` 创建 `app` 后加：
```python
from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
# 仅 HTTPS 时启用：app.config['SESSION_COOKIE_SECURE'] = True
```
这步非必须，HTTP 阶段可跳过。
