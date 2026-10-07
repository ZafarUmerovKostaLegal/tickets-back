FROM python:3.12-slim

WORKDIR /app

RUN useradd -r -u 10001 -g root appuser

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

COPY backup/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend_common ./backend_common
COPY backup/ ./

EXPOSE 1247

# По умолчанию — планировщик + API.
# Разовый бэкап: python -m application.cli run-once
# Список:       python -m application.cli list
# Восстановление: python -m application.cli restore 20260531T030000Z --confirm RESTORE_BACKUP

RUN chown -R appuser:root /app
USER appuser

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "1247", "--no-access-log", "--log-level", "info"]
