#!/usr/bin/env bash
# 证书续期脚本（HTTPS 升级后用）。先把下面 DOMAIN 改成你的域名。
set -e
DOMAIN=your.domain.com
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# 自动识别当前跑的是哪套 compose
if docker ps --format '{{.Names}}' | grep -q fca-db; then
  COMPOSE_FILE=deploy/docker-compose.postgres.yml
else
  COMPOSE_FILE=deploy/docker-compose.supabase.yml
fi

docker run --rm \
  -v "$ROOT/deploy/certs:/etc/letsencrypt" \
  -v "$ROOT/deploy/www:/var/www/certbot" \
  certbot/certbot renew --webroot -w /var/www/certbot

cp "$ROOT/deploy/certs/live/$DOMAIN/fullchain.pem" "$ROOT/deploy/certs/fullchain.pem"
cp "$ROOT/deploy/certs/live/$DOMAIN/privkey.pem"   "$ROOT/deploy/certs/privkey.pem"

docker compose -f "$COMPOSE_FILE" exec nginx nginx -s reload
echo "证书已续期并 reload nginx"
