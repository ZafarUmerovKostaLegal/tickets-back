# Миграция после смены IP сервера (502 Bad Gateway на ticketsback.kostalegal.com)

## Что проверено

При **502 Bad Gateway** nginx отвечает, но **не может достучаться до gateway**.

Типичная ситуация после переноса:

| Компонент | Где |
|-----------|-----|
| Docker + gateway | новый IP, например `192.168.230.81:1234` ✅ |
| nginx + SSL для `ticketsback.kostalegal.com` | **старый** сервер, `proxy_pass http://127.0.0.1:1234` ❌ |

Проверка с любой машины в LAN:

```bash
curl http://192.168.230.81:1234/live          # → 200 (gateway жив)
curl https://ticketsback.kostalegal.com/health # → 502 (nginx не видит gateway)
```

Auth через прямой IP работает:

```bash
curl -L -o /dev/null -w "%{http_code}\n" http://192.168.230.81:1234/api/v1/auth/azure/login
# → 200, редирект на login.microsoftonline.com
```

---

## Быстрое исправление (без смены DNS)

На сервере, где **nginx** обслуживает `ticketsback.kostalegal.com`:

```bash
cd /path/to/tickets-back

sudo cp deploy/nginx/kosta-gateway-upstream.remote.conf.example \
  /etc/nginx/conf.d/kosta-gateway-upstream.conf

# Если IP gateway другой — отредактируйте server в файле:
#   server 192.168.230.81:1234;

sudo nginx -t && sudo systemctl reload nginx
curl -I https://ticketsback.kostalegal.com/health   # ожидается 200
```

Скрипт диагностики:

```bash
bash deploy/post-ip-migration.sh
```

---

## Полная миграция (nginx на новом сервере)

Если DNS можно перенести на новый IP (`192.168.230.81`), порт **443** на нём свободен:

1. **DNS:** A-запись `ticketsback.kostalegal.com` → `192.168.230.81`
2. **Сертификат** на новом хосте:
   ```bash
   sudo certbot certonly --standalone -d ticketsback.kostalegal.com
   ```
3. **Edge nginx в Docker:**
   ```bash
   docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile edge up -d edge
   ```
4. Отключить nginx на старом сервере (чтобы не было конфликта DNS/SSL).

---

## Env backend (не менять при смене только IP)

| Переменная | Значение |
|------------|----------|
| `GATEWAY_BASE_URL` | `https://ticketsback.kostalegal.com` |
| `FRONTEND_URL` | `https://tickets.kostalegal.com` |
| `AUTH_REDIRECT_URI` | `https://ticketsback.kostalegal.com/api/v1/auth/azure/callback` |

Azure AD redirect URI — тот же HTTPS callback. Менять **не нужно**, если домен не менялся.

---

## Dev: фронт на Vite `:5173`

| Переменная | Значение |
|------------|----------|
| `FRONTEND_URL` | `http://192.168.230.81:5173` |
| `VITE_API_BASE_URL` (фронт) | `https://ticketsback.kostalegal.com` после fix nginx **или** `http://192.168.230.81:1234` для LAN без TLS |

При API по `http://192.168.230.81:1234` нужен отдельный redirect URI в Azure:
`http://192.168.230.81:1234/api/v1/auth/azure/callback`

---

## Файлы конфигурации

| Файл | Назначение |
|------|------------|
| `deploy/nginx/kosta-gateway-upstream.conf` | upstream `127.0.0.1:1234` (nginx и Docker на одном хосте) |
| `deploy/nginx/kosta-gateway-upstream.remote.conf.example` | upstream на новый IP после миграции |
| `deploy/nginx/ticketsback.kostalegal.com.conf` | nginx на хосте (include upstream) |
| `deploy/nginx/ticketsback.docker.conf` | nginx в Docker (`--profile edge`) |
