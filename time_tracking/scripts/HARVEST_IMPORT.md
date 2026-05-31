# Импорт Harvest — контейнер time_tracking

## Быстрый старт

**1. На хосте** (не в контейнере) скопируйте xlsx в контейнер:

```bash
cd /path/to/tickets-back

docker cp timetrackinck/harvest_time_report_from2023-01-23to2026-05-26.xlsx \
  $(docker compose ps -q time_tracking):/tmp/harvest.xlsx
```

**2. В контейнере** `time_tracking`:

```bash
python scripts/import_harvest_time_report.py --file /tmp/harvest.xlsx --dry-run
python scripts/import_harvest_time_report.py --file /tmp/harvest.xlsx --execute
```

Команда **без** `> >` в конце — иначе `--dry-run` не распознается.

---

## Пути внутри контейнера

| Что | Путь |
|-----|------|
| Скрипт | `/app/scripts/import_harvest_time_report.py` |
| Рабочая папка | `/app` |
| xlsx после docker cp | `/tmp/harvest.xlsx` |

**Неправильно:** `time_tracking/scripts/...` (такого каталога в контейнере нет)  
**Неправильно:** `timetrackinck/...` (папка на хосте, из контейнера не видна)

---

## БД

В контейнере уже заданы `DATABASE_URL` / `TIME_TRACKING_DATABASE_URL` из docker-compose — `--database-url` обычно не нужен.

---

## Скрипта нет на сервере

```bash
git pull
docker compose up -d --build time_tracking
docker compose exec time_tracking ls -la scripts/import_harvest_time_report.py
```

---

## Без Docker (на хосте)

```bash
cd /path/to/tickets-back
export TIME_TRACKING_DATABASE_URL="postgresql://..."
python scripts/import_harvest_time_report.py \
  --file timetrackinck/harvest_time_report_from2023-01-23to2026-05-26.xlsx \
  --dry-run
```
