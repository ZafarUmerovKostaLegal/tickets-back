# Сервис резервного копирования (backup)

Отдельный микросервис для автоматических снимков всех PostgreSQL-баз и общего каталога media.

## Что сохраняется

| Компонент | Содержимое |
|---|---|
| **11 баз данных** | auth (users), tickets, todos, notifications, inventory, attendance, time_tracking, expenses, projects, vacation, chat |
| **media** | `/app/media` — вложения тикетов, todos, расходов, инвентаря, attendance, vacation PDF и т.д. |

Каждый снимок — каталог `/backups/YYYYMMDDTHHMMSSZ/`:

```
20260531T030000Z/
  manifest.json
  databases/
    auth.dump
    tickets.dump
    ...
  media.tar.gz
```

## Быстрый старт (Portainer / docker compose)

### 1. Задайте токен в env стека

```bash
BACKUP_API_TOKEN=$(openssl rand -hex 32)
```

Рекомендуемые переменные:

| Переменная | По умолчанию | Описание |
|---|---|---|
| `BACKUP_SCHEDULE_CRON` | `0 3 * * *` | Расписание (UTC), каждый день в 03:00 |
| `BACKUP_RETENTION_DAYS` | `30` | Удалять снимки старше N дней |
| `BACKUP_RETENTION_MIN_COUNT` | `14` | Всегда хранить минимум N последних снимков |
| `BACKUP_MEDIA` | `true` | Архивировать media |
| `BACKUP_ON_START` | `false` | Сделать бэкап сразу при старте контейнера |

### 2. Запустите сервис

```bash
docker compose --profile backup up -d backup
```

### 3. Разовый снимок вручную

```bash
docker compose --profile backup run --rm backup python -m application.cli run-once
```

### 4. Проверка

```bash
docker compose --profile backup exec backup python -m application.cli list
curl http://localhost:1247/api/v1/backup/status
```

Ручной запуск через API (нужен токен):

```bash
curl -X POST http://localhost:1247/api/v1/backup/run \
  -H "X-Backup-Token: YOUR_BACKUP_API_TOKEN"
```

## Хранение снимков на диске хоста (важно!)

По умолчанию снимки лежат в Docker-томе `backup_storage`. При потере сервера том тоже пропадёт.

**Настройте bind-mount на отдельный диск / NAS / S3-sync папку:**

В `docker-compose.override.yml` на сервере:

```yaml
services:
  backup:
    volumes:
      - /mnt/backups/kosta:/backups
      - media_storage:/media:ro
```

Дополнительно настройте синхронизацию `/mnt/backups/kosta` на другой сервер или облако (rsync, rclone, Restic, Borg).

## Восстановление

> Остановите приложение или хотя бы сервис, чью БД восстанавливаете, чтобы избежать конфликтов.

**Все базы + media из снимка:**

```bash
docker compose --profile backup run --rm backup \
  python -m application.cli restore 20260531T030000Z --confirm RESTORE_BACKUP
```

**Одна база:**

```bash
docker compose --profile backup run --rm backup \
  python -m application.cli restore 20260531T030000Z --database tickets --confirm RESTORE_BACKUP
```

**Только media:**

```bash
docker compose --profile backup run --rm backup \
  python -m application.cli restore 20260531T030000Z --media-only --confirm RESTORE_BACKUP
```

После восстановления media перезапустите сервисы, использующие файлы (gateway, tickets, todos, expenses…).

## Мониторинг

- `GET /live` — жив ли контейнер
- `GET /api/v1/backup/status` — последний снимок, ошибки, расписание
- `GET /api/v1/backup/snapshots` — список снимков

Рекомендуется настроить внешний мониторинг (Uptime Kuma, cron + curl) на `status.last_status` ≠ `failed`.

## Порт

Сервис слушает **1247** (внутри Docker-сети). Наружу не публикуется — доступ через `docker exec` или проброс порта при необходимости.

## Стратегия «никогда не потерять данные»

1. **Ежедневные автоматические снимки** — сервис `backup` с профилем `backup`.
2. **Bind-mount на отдельный диск** — не только Docker volume.
3. **Off-site копия** — rsync/rclone на другой сервер или облако (раз в сутки после бэкапа).
4. **Периодическая проверка восстановления** — раз в месяц `restore` на тестовый стек.
5. **Экспорт env Portainer** — пароли БД и JWT храните отдельно от сервера.

## Файлы

- `backup/` — микросервис
- `backup/application/backup_runner.py` — логика pg_dump + tar
- `backup/application/cli.py` — CLI run-once / list / restore
- `docker-compose.yml` — сервис `backup` (profile `backup`)
