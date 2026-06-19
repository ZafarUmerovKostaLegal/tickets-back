# Импорт Harvest — time tracking

**Источник данных:** любой CSV-экспорт Harvest time report (передаётся через `--file`).
Каждая строка CSV = одна запись времени с теми же Hours и Billable?.

---

## Режим дифа (по умолчанию, недеструктивный)

С 2026‑06 импорт работает как **диф по содержимому**, а не «снести и перезалить»:

- **Добавляются только те строки, которых ещё нет в БД.** Существующие записи
  **не удаляются и не меняются** (id, даты, ручные правки сохраняются).
- Совпадение определяется **контентным ключом**:
  `дата + пользователь + проект + имя задачи + часы + billable + заметка`.
  Ключ **не зависит** от имени файла отчёта и номера строки — поэтому можно
  заливать новый экспорт с любым именем (`harvest_time_report (1).csv` и т.п.),
  дублей не будет.
- **Повторы внутри Harvest сохраняются** (мультимножество): если в отчёте 3
  одинаковые строки, а в БД уже 1 — добавятся недостающие 2.
- **Пользователи заводятся всегда**, даже если ставка = 0. Нет в auth →
  архивный placeholder `harvest.*@import.kostalegal.local`.
- **Ставки** проставляются из колонок Billable/Cost Rate (по валюте и интервалам
  дат) — чтобы суммы считались сразу, без захода в проект и «Сохранить».
  Глобальные harvest‑ставки пересоздаются из CSV; ручные/проектные ставки не трогаются.

В диф‑режиме расхождения «БД ≠ суммы файла» выводятся как **предупреждения** (в БД
легитимно может быть больше: ручные записи, прежние импорты) и **не** откатывают импорт.

### Команды

Файл лежит в папке сервиса: `time_tracking/harvest_time_report (1).csv`.
Скрипт сам находит **самый свежий** `harvest_time_report*.csv` в этой папке —
`--file` указывать **не нужно** (URL БД берутся из окружения контейнера).

В контейнере `time_tracking` (Portainer / `docker exec`):

```bash
# 1) Холостой прогон — покажет «добавит N, пропустит M (уже в БД)»:
python scripts/import_harvest_time_report.py --dry-run

# 2) Боевой импорт (добавит только недостающее, существующее не трогает):
python scripts/import_harvest_time_report.py --execute
```

Локально с Windows (если БД проброшена): добавьте `--database-url` и `--auth-db-url`
одной строкой; `--file` всё равно не нужен, если файл в `time_tracking/`.

> ⚠️ **`--replace`** — деструктивный режим: удаляет все записи проектов из файла и
> заливает заново 1:1. Использовать только осознанно. Для обычного дозалива он **не нужен**.
> Перед первым боевым прогоном сделайте бэкап БД.

---

## Локально (Windows / без Docker и без SSH на сервер)

Нужен только **доступ к PostgreSQL** time tracking (и желательно auth) — с вашего ПК, через VPN или проброс порта. Docker и git на сервере не нужны.

**1. Зависимости (один раз):**

```powershell
cd "D:\work\Kosta Legal\V3\tickets-back"
pip install -r time_tracking/requirements.txt
```

**2. CSV уже в репозитории:**

`time_tracking/harvest_time_report_from2023-01-23to2026-06-02.csv` — указывать `--file` не обязательно.

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

docker cp timetrackinck/harvest_time_report_from2023-01-23to2026-06-02.csv \
  $(docker compose ps -q time_tracking):/tmp/harvest_time_report_from2023-01-23to2026-06-02.csv
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
| `time_tracking/harvest_time_report_from2023-01-23to2026-06-02.csv` | в репозитории (основной) |
| `/tmp/harvest_time_report_from2023-01-23to2026-06-02.csv` | после `docker cp` |
| `timetrackinck/harvest_time_report_from2023-01-23to2026-06-02.csv` | на хосте сервера |

---

## Что импортируется 1:1 из CSV

- **Client** → клиент
- **Project** / **Project Code** → проект
- **Task**, **Notes** → задача и описание
- **Hours** → часы (как в файле, 2 знака)
- **Billable?** (`Yes`/`No`) → billable / non-billable
- **First Name** + **Last Name** → пользователь в TT (**в архиве**), доступ к проекту, все строки CSV
- **Currency** → валюта клиента/проекта
- **Billable Rate** / **Cost Rate** → почасовые ставки для архивных/уволенных сотрудников (колонки 15 и 17 CSV)

### Пользователи (архив + команда проекта)

- Все **12** имён из CSV создаются/обновляются в `time_tracking_users` с **`is_archived=true`**
- Если сотрудника нет в auth — placeholder `harvest.*@import.kostalegal.local` (попадает в общий список TT)
- **При создании проекта** сразу добавляется **вся команда из CSV**: доступ к проекту + **billable-ставки** (колонка Billable Rate) + затем записи времени
- Ставки — по интервалам дат работ; при смене тарифа несколько интервалов (напр. 120 → 180 EUR)
- **312 строк = 312 записей времени** — скрипт откатывает импорт, если хоть одна строка не попала в БД
- Сверка по каждому пользователю: число строк и часы (total / billable / non-billable) как в CSV

### Задачи проекта (как в Harvest)

Из CSV создаются все задачи с записями времени. Дополнительно добавляется **My mehnat registration** (billable, 0 ч — как в отчёте Harvest).

| Раздел Harvest | Задачи | Часы |
|----------------|--------|------|
| Billable tasks | Drafting, Document Review, Emails, Telephone calls, Research, Meetings, Document Submission, My mehnat registration | **376.20** |
| Non-billable tasks | Kosta Legal Internal | **8.99** |
| **Итого** | | **385.19** |

Сумма billable из отчёта Harvest (Billable Amount): **€53 848.00** — совпадает с расчётом по индивидуальным ставкам.

`billable_by_default` задачи выводится из CSV: non-billable только если все часы по задаче non-billable (как **Kosta Legal Internal**).

Дубликаты задач объединяются: записи переносятся на одну задачу, лишние удаляются.

Повторный `--execute` безопасен: строки помечаются `harvest-import:<имя-файла>:<номер-строки>`.

---

## Что нужно задеплоить на prod

Скрипт импорта **не меняет код сервиса в runtime** — достаточно:

1. Закоммитить и задеплоить обновлённый `time_tracking` (чтобы dashboard показывал задачи с 0 ч).
2. Один раз выполнить импорт с `--execute --replace` (локально или на сервере) против prod-БД.

Без шага 2 в UI останутся старые данные, даже если код уже на сервере.
