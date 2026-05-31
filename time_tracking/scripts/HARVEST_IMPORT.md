# Импорт Harvest — контейнер time_tracking

**Источник данных (1:1):** `harvest_time_report_from2023-01-23to2026-05-26.csv`  
(точный CSV-экспорт Harvest; xlsx с тем же именем — запасной вариант)

Каждая строка CSV = одна запись времени с теми же Hours и Billable?.

## Быстрый старт

**1. На хосте** скопируйте CSV в контейнер:

```bash
cd /path/to/tickets-back

docker cp timetrackinck/harvest_time_report_from2023-01-23to2026-05-26.csv \
  $(docker compose ps -q time_tracking):/tmp/harvest_time_report_from2023-01-23to2026-05-26.csv
```

**2. В контейнере** `time_tracking`:

```bash
export AUTH_DATABASE_URL="postgresql://user:pass@host:5432/kosta_auth"

python scripts/import_harvest_time_report.py --dry-run
python scripts/import_harvest_time_report.py --execute --replace
```

`--file` можно не указывать — скрипт ищет CSV, затем xlsx.

---

## Где лежит файл

| Путь | Описание |
|------|----------|
| `time_tracking/harvest_time_report_from2023-01-23to2026-05-26.csv` | в репозитории (основной) |
| `/tmp/harvest_time_report_from2023-01-23to2026-05-26.csv` | после `docker cp` |
| `timetrackinck/harvest_time_report_from2023-01-23to2026-05-26.csv` | на хосте сервера |

---

## Что импортируется 1:1 из CSV

- **Client** → клиент
- **Project** / **Project Code** → проект
- **Task**, **Notes** → задача и описание
- **Hours** → часы (как в файле, 2 знака)
- **Billable?** (`Yes`/`No`) → billable / non-billable
- **First Name** + **Last Name** → пользователь (создаётся placeholder, если нет в системе)
- **Currency** → валюта клиента/проекта

### Задачи проекта (обязательные)

Из CSV создаются все задачи с записями времени. Дополнительно в проект всегда добавляются **non-billable** задачи (даже если в Harvest 0 ч):

- **Meetings**
- **My mehnat registration**

Дубликаты задач (например после старого `seed_default_common_tasks`) объединяются: записи переносятся на одну задачу, лишние удаляются.

Повторный `--execute` безопасен: строки помечаются `harvest-import:<имя-файла>:<номер-строки>`.

---

## Без Docker

```bash
cd /path/to/tickets-back
export TIME_TRACKING_DATABASE_URL="postgresql://..."
python scripts/import_harvest_time_report.py --execute --replace
```
