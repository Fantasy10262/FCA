# Fantasy Coding Arena —— 用于 Render / 任意支持 Docker 的平台
# 基础镜像：Debian-slim Python（自带 python3）
FROM python:3.11-slim

# 安装 C/C++ 编译器（判题必须）并清理 apt 缓存以减小镜像体积
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc g++ \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先装依赖（利用 Docker 层缓存）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码（.dockerignore 已排除 data/、.workbuddy 等，避免提交数据库与密钥）
COPY . .

# Render / 多数平台会注入 PORT 环境变量；本地运行回退到 5000
EXPOSE 10000
CMD ["sh", "-c", "waitress-serve --port=${PORT:-5000} --threads=8 app:app"]
