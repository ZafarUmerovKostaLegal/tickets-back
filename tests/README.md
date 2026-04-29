# Тесты

## Установка

```bash
pip install -r gateway/requirements.txt
pip install -r auth/requirements.txt
pip install -r tickets/requirements.txt

pip install -r requirements-dev.txt
```

## Запуск

```bash
pytest tests/ -v
```

## Результаты

- **Gateway**: health, media auth, ws-url, admin login, tickets 401
- **File storage**: валидация расширений (tickets, inventory, notifications)
- **Auth**: admin login invalid (401)
- **Остальные**: требуют PostgreSQL (skipped)

## Тесты с PostgreSQL

Для полного прогона нужна база PostgreSQL:

```bash
docker run -d -e POSTGRES_PASSWORD=postgres -p 5432:5432 postgres:16-alpine
```

Установите `DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/postgres`.
