# Импорт Harvest → time tracking

## Ошибка «No such file or directory» / `/app/time_tracking/scripts/...`

Вы в **контейнере** (`/app`). Там нет каталога `time_tracking/` — скрипт лежит так:

```text
/app/scripts/import_harvest_time_report.py
```

**Неправильно:** `python time_tracking/scripts/import_harvest_time_report.py`  
**Правильно в контейнере:**

```bash
python scripts/import_harvest_time_report.py \
  --file timetrackinck/harvest_time_report_from2023-01-23to2026-05-26.xlsx \
  --dry-run
```

Сначала нужен **git pull** и пересборка образа (или скопировать скрипт на сервер вручную).

Не добавляйте в конец команды `> >` — это лишние символы.

---

## Без Docker (на хосте, из корня репозитория)

```bash
cd /path/to/tickets-back
git pull
pip install -r time_tracking/requirements.txt

export TIME_TRACKING_DATABASE_URL="postgresql://USER:PASS@HOST:5432/kosta_time_tracking"

python scripts/import_harvest_time_report.py \
  --file timetrackinck/harvest_time_report_from2023-01-23to2026-05-26.xlsx \
  --dry-run

python scripts/import_harvest_time_report.py \
  --file timetrackinck/harvest_time_report_from2023-01-23to2026-05-26.xlsx \
  --execute
```

Альтернатива (тот же скрипт):

```bash
python time_tracking/scripts/import_harvest_time_report.py --dry-run
```

---

## Проверка, что файл на месте

```bash
# на хосте
ls -la scripts/import_harvest_time_report.py
ls -la time_tracking/scripts/import_harvest_time_report.py
ls -la timetrackinck/harvest_time_report_from2023-01-23to2026-05-26.xlsx

# в контейнере time_tracking
ls -la /app/scripts/import_harvest_time_report.py
```

---

## URL базы

`TIME_TRACKING_DATABASE_URL` или `DATABASE_URL`, либо `--database-url postgresql://...`

Пользователи TT: `python time_tracking/scripts/restore_tt_users_from_auth_db.py --execute --auth-db-url "$AUTH_DATABASE_URL"`
