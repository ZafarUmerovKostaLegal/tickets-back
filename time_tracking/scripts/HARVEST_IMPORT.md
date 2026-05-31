# Импорт Harvest — контейнер time_tracking

Файл: **`harvest_time_report_from2023-01-23to2026-05-26.xlsx`**

## Быстрый старт

**1. На хосте** скопируйте xlsx в контainer (полное имя файла):

```bash
cd /path/to/tickets-back

docker cp timetrackinck/harvest_time_report_from2023-01-23to2026-05-26.xlsx \
  $(docker compose ps -q time_tracking):/tmp/harvest_time_report_from2023-01-23to2026-05-26.xlsx
```

**2. В контейнере** `time_tracking` — `--file` можно не указывать:

```bash
export AUTH_DATABASE_URL="postgresql://user:pass@host:5432/kosta_auth"

python scripts/import_harvest_time_report.py --dry-run
python scripts/import_harvest_time_report.py --execute
```

Уволенные: сначала TT (включая архив) и auth DB (`AUTH_DATABASE_URL`).
Если сотрудника нет нигде — создаётся архивный TT-пользователь Harvest; **все строки импортируются**.

Явно:

```bash
python scripts/import_harvest_time_report.py \
  --file /tmp/harvest_time_report_from2023-01-23to2026-05-26.xlsx \
  --dry-run
```

Без `> >` в конце команды.

---

## Где ищется файл по умолчанию

1. `/app/time_tracking/harvest_time_report_from2023-01-23to2026-05-26.xlsx`
2. `/tmp/harvest_time_report_from2023-01-23to2026-05-26.xlsx` (после docker cp)
3. `timetrackinck/harvest_time_report_from2023-01-23to2026-05-26.xlsx` (на хосте)

---

## Без Docker

```bash
cd /path/to/tickets-back
export TIME_TRACKING_DATABASE_URL="postgresql://..."
python scripts/import_harvest_time_report.py --dry-run
```
