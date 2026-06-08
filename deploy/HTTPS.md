# HTTPS для tickets.kostalegal.com ↔ ticketsback.kostalegal.com

## Ошибка в браузере

```
Mixed Content: page at https://tickets.kostalegal.com/... requested insecure
http://ticketsback.kostalegal.com/api/v1/...
```

Страница открыта по **HTTPS**, а API вызывается по **HTTP** — браузер блокирует запрос.

---

## Что исправить (два места)

### 1. Фронтенд (обязательно)

В сборке SPA (`tickets` / frontend) URL API должен быть **https**:

```env
VITE_API_BASE_URL=https://ticketsback.kostalegal.com
```

или аналог (`REACT_APP_API_URL`, `NEXT_PUBLIC_API_URL` — как у вас в проекте).

**Нельзя:** `http://ticketsback.kostalegal.com`

После смены — **пересобрать и задеплоить** фронт.

### 2. Сервер API (nginx + SSL)

Gateway слушает `1234` внутри Docker. Снаружи нужен nginx с TLS:

- Пример конфига: [`deploy/nginx/ticketsback.kostalegal.com.conf`](nginx/ticketsback.kostalegal.com.conf)
- Сертификат Let's Encrypt для `ticketsback.kostalegal.com`
- HTTP → редирект на HTTPS (301)

Проверка:

```bash
curl -I https://ticketsback.kostalegal.com/health
```

---

## Env backend (docker-compose.prod.yml)

При деплое используйте prod overlay:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

Ключевые переменные (уже заданы дефолты в `docker-compose.prod.yml`):

| Переменная | Значение |
|------------|----------|
| `GATEWAY_BASE_URL` | `https://ticketsback.kostalegal.com` |
| `FRONTEND_URL` | `https://tickets.kostalegal.com` |
| `AUTH_REDIRECT_URI` | `https://ticketsback.kostalegal.com/api/v1/auth/azure/callback` |
| `SECURITY_HSTS_ENABLED` | `true` |

В Azure AD redirect URI тоже должен быть **https** (тот же callback).

---

## Быстрая диагностика

| Проверка | Ожидание |
|----------|----------|
| DevTools → Network → URL запроса | `https://ticketsback.kostalegal.com/...` |
| `curl https://ticketsback.kostalegal.com/health` | 200 |
| `curl http://ticketsback.kostalegal.com/health` | 301 → https |

Внутренние URL микросервисов (`http://correspondence:1249`) менять **не нужно** — это Docker-сеть.

---

## 502 Bad Gateway после смены IP сервера

См. **[deploy/MIGRATION.md](MIGRATION.md)** — gateway часто переезжает, а nginx на старом хосте остаётся с `proxy_pass http://127.0.0.1:1234`.

Кратко: на сервере с nginx замените upstream на IP нового хоста (`192.168.230.81:1234`) и `sudo systemctl reload nginx`.
