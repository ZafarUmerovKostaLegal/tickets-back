# Импорт времени из Harvest (.xlsx)

Скрипт: `time_tracking/scripts/import_harvest_time_report.py`

## Что делает

1. Читает отчёт Harvest (лист **Harvest**).
2. Создаёт **клиентов** и **проекты** (если ещё нет в TT).
3. Добавляет **задачи** проекта (из колонки Task; стандартный набор задач тоже создаётся).
4. Создаёт **записи времени** для пользователей.
5. Выдаёт пользователям **доступ к проекту**.

## Подготовка

1. Пользователи должны быть в `time_tracking_users` (синхронизация из auth):

   ```bash
   cd time_tracking
   python scripts/restore_tt_users_from_auth_db.py --execute --auth-db-url "$AUTH_DATABASE_URL"
   ```

2. **display_name** в TT должен совпадать с Harvest `First Name + Last Name`  
   (например `Aliye Ablyalimova`). Иначе строки для этого человека будут пропущены.

3. Файл отчёта, например:  
   `harvest_time_report_from2023-01-23to2026-05-26.xlsx`

## Запуск

Проверка без записи:

```bash
cd time_tracking
python scripts/import_harvest_time_report.py \
  --file "/path/to/harvest_time_report.xlsx" \
  --dry-run
```

Импорт в БД:

```bash
python scripts/import_harvest_time_report.py \
  --file "/path/to/harvest_time_report.xlsx" \
  --execute
```

В Docker (пример):

```bash
docker compose exec time_tracking python scripts/import_harvest_time_report.py \
  --file /tmp/harvest.xlsx --execute
```

(файл предварительно скопировать в контейнер: `docker cp ...`)

## Ваш файл (пример)

| Параметр | Значение |
|----------|----------|
| Строк | 312 |
| Клиент | EVYAP INTERNATIONAL |
| Проект | Company Establishment |
| Пользователей | 12 |
| Валюта | EUR |

## Повторный импорт

Дубликаты не создаются: та же запись (пользователь + дата + проект + задача + часы + описание) пропускается.

## Если пользователь не находится

Dry-run выведет список имён без сопоставления. Исправьте `display_name` в auth/TT или добавьте пользователя в TT.
