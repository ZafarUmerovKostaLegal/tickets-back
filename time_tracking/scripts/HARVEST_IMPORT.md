# Импорт Harvest — time tracking

**Источник данных (1:1):** `harvest_time_report_from2023-01-23to2026-05-26.csv`  
(точный CSV-экспорт Harvest; xlsx с тем же именем — запасной вариант)

Каждая строка CSV = одна запись времени с теми же Hours и Billable?.

---

## Локально (Windows / без Docker и без SSH на сервер)

Нужен только **доступ к PostgreSQL** time tracking (и желательно auth) — с вашего ПК, через VPN или проброс порта. Docker и git на сервере не нужны.

**1. Зависимости (один раз):**

```powershell
cd "D:\work\Kosta Legal\V3\tickets-back"
pip install -r time_tracking/requirements.txt
```

**2. CSV уже в репозитории:**

`time_tracking/harvest_time_report_from2023-01-23to2026-05-26.csv` — указывать `--file` не обязательно.

**3. Проверка без записи в БД:**

```powershell
$env:TIME_TRACKING_DATABASE_URL = "postgresql://USER:PASS@HOST:5432/kosta_time_tracking"
$env:AUTH_DATABASE_URL = "postgresql://USER:PASS@HOST:5432/kosta_auth"

python scripts/import_harvest_time_report.py --dry-run
```

**4. Импорт:**

```powershell
python scripts/import_harvest_time_report.py --execute --replace
```

URL можно передать **одной строкой** (без `\` в конце — иначе shell воспримет следующую строку как отдельную команду):

```powershell
python scripts/import_harvest_time_report.py --execute --replace --database-url "postgresql://USER:PASS@HOST:5432/kosta_time_tracking" --auth-db-url "postgresql://USER:PASS@HOST:5432/kosta_auth"
```

`AUTH_DATABASE_URL` необязателен: без него уволенные сотрудники получат placeholder-пользователя, записи всё равно импортируются.

**Если нет доступа к БД с вашего ПК** — передайте админу сервера блок «На сервере (Docker)» ниже или попросите выдать VPN / `TIME_TRACKING_DATABASE_URL`.

### Частые ошибки в терминале

| Ошибка | Причина | Решение |
|--------|---------|---------|
| `git: not found`, `docker: not found` | Вы не на сервере / нет Docker | Запускайте **локально в PowerShell** на Windows, только `python ...` |
| `sh: --database-url: not found` | Перенос `\` — аргументы на новой строке | Вся команда **в одну строку** |
| `cd: can't cd to /path/to/tickets-back` | Placeholder из доки | `cd "D:\work\Kosta Legal\V3\tickets-back"` |
| `Задайте URL PostgreSQL` | Нет URL БД | `--database-url "postgresql://..."` |
| `socket.gaierror: Temporary failure in name resolution` | Неверный **host** в URL (часто буквально `HOST` из примера, или `time_tracking_db` вне Docker) | См. ниже |

### Ошибка `socket.gaierror` (name resolution)

Скрипт не может найти сервер PostgreSQL по имени хоста из `--database-url`.

**Если вы внутри контейнера `time_tracking` (Portainer, `docker exec`):**

Не передавайте `--database-url` — URL уже в переменных окружения контейнера:

```bash
python scripts/import_harvest_time_report.py --dry-run
python scripts/import_harvest_time_report.py --execute --replace
```

Внутри Docker-сети хост БД: `time_tracking_db`, auth: `users_db`.

**Если запускаете с Windows / вне Docker:**

- Нельзя использовать `time_tracking_db` — это имя только внутри docker-compose.
- Нужен реальный адрес: IP сервера, домен, `localhost` (если Postgres проброшен на порт).
- Замените `USER`, `PASS`, `HOST` на настоящие значения — **не копируйте placeholder из доки**.

Пример (одна строка):

```powershell
python scripts/import_harvest_time_report.py --execute --replace --database-url "postgresql://time_tracking:time_tracking@192.168.1.10:5432/kosta_time_tracking"
```

**Проверка хоста:** если `ping HOST` / `nslookup HOST` не работает — URL тоже не сработает.

---

## На сервере (Docker)

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

### Задачи проекта (как в Harvest)

Из CSV создаются все задачи с записями времени. Дополнительно добавляется **My mehnat registration** (billable, 0 ч — как в отчёте Harvest).

| Раздел Harvest | Задачи | Часы |
|----------------|--------|------|
| Billable tasks | Drafting, Document Review, Emails, Telephone calls, Research, Meetings, Document Submission, My mehnat registration | **370.84** |
| Non-billable tasks | Kosta Legal Internal | **8.71** |
| **Итого** | | **379.55** |

`billable_by_default` задачи выводится из CSV: non-billable только если все часы по задаче non-billable (как **Kosta Legal Internal**).

Дубликаты задач объединяются: записи переносятся на одну задачу, лишние удаляются.

Повторный `--execute` безопасен: строки помечаются `harvest-import:<имя-файла>:<номер-строки>`.

---

## Что нужно задеплоить на prod

Скрипт импорта **не меняет код сервиса в runtime** — достаточно:

1. Закоммитить и задеплоить обновлённый `time_tracking` (чтобы dashboard показывал задачи с 0 ч).
2. Один раз выполнить импорт с `--execute --replace` (локально или на сервере) против prod-БД.

Без шага 2 в UI останутся старые данные, даже если код уже на сервере.
