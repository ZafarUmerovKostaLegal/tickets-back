#!/usr/bin/env bash
# Диагностика 502 Bad Gateway после переноса Docker на новый IP.
set -euo pipefail

GATEWAY_HOST="${GATEWAY_HOST:-192.168.230.81}"
GATEWAY_PORT="${GATEWAY_PORT:-1234}"
DOMAIN="${DOMAIN:-ticketsback.kostalegal.com}"

http_code() {
  curl -sS -o /dev/null -w '%{http_code}' --connect-timeout 5 "$1" 2>/dev/null || echo "000"
}

echo "=== Kosta Legal: проверка API после миграции ==="
echo "Gateway: ${GATEWAY_HOST}:${GATEWAY_PORT}  Domain: ${DOMAIN}"
echo

direct="$(http_code "http://${GATEWAY_HOST}:${GATEWAY_PORT}/live")"
local_code="$(http_code "http://127.0.0.1:${GATEWAY_PORT}/live")"
domain_code="$(http_code "https://${DOMAIN}/health")"

printf "  %-45s %s\n" "http://${GATEWAY_HOST}:${GATEWAY_PORT}/live" "$direct"
printf "  %-45s %s\n" "http://127.0.0.1:${GATEWAY_PORT}/live" "$local_code"
printf "  %-45s %s\n" "https://${DOMAIN}/health" "$domain_code"
echo

if [[ "$direct" == "200" && "$local_code" != "200" ]]; then
  echo "ДИАГНОЗ: Docker gateway на ${GATEWAY_HOST}, nginx проксирует на 127.0.0.1 → 502."
  echo
  echo "ИСПРАВЛЕНИЕ на сервере с nginx для ${DOMAIN}:"
  echo "  sudo cp deploy/nginx/kosta-gateway-upstream.remote.conf.example /etc/nginx/conf.d/kosta-gateway-upstream.conf"
  echo "  sudo nginx -t && sudo systemctl reload nginx"
elif [[ "$direct" == "200" && "$domain_code" != "200" ]]; then
  echo "ДИАГНОЗ: gateway жив, домен не доходит (DNS / nginx / SSL)."
  echo "  См. deploy/MIGRATION.md"
elif [[ "$direct" != "200" ]]; then
  echo "ДИАГНОЗ: gateway не отвечает — Portainer → redeploy stack; docker compose logs gateway"
else
  echo "OK: gateway и домен отвечают."
fi
