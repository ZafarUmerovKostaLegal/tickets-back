# Импорт Harvest → time tracking (запуск без Docker)

```bash
cd /path/to/tickets-back
pip install -r time_tracking/requirements.txt

export TIME_TRACKING_DATABASE_URL="postgresql://USER:PASS@HOST:5432/kosta_time_tracking"
```

Положите xlsx в `time_tracking/` (или укажите `--file`):

```bash
# проверка
python time_tracking/scripts/import_harvest_time_report.py --dry-run

# импорт
python time_tracking/scripts/import_harvest_time_report.py --execute
```

Если файл в другой папке на сервере:

```bash
python time_tracking/scripts/import_harvest_time_report.py \
  --file /path/to/timetrackinck/harvest_time_report_from2023-01-23to2026-05-26.xlsx \
  --database-url "$TIME_TRACKING_DATABASE_URL" \
  --execute
```

URL БД можно не дублировать в `--database-url`, если задан `TIME_TRACKING_DATABASE_URL`.

Перед импортом пользователи должны быть в `time_tracking_users`:

```bash
python time_tracking/scripts/restore_tt_users_from_auth_db.py \
  --execute --auth-db-url "$AUTH_DATABASE_URL"
```
