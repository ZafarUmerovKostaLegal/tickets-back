FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY expenses/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend_common ./backend_common
COPY expenses/ ./

RUN sed -i 's/\r$//' docker-entrypoint.sh && chmod +x docker-entrypoint.sh

# Очистка заявок на расход (консоль контейнера / Portainer):
#   python scripts/purge_expenses_keep_reference.py --execute --confirm WIPE_EXPENSES_KEEP_REFERENCE
#   python scripts/delete_expenses_with_project.py --execute --confirm DELETE_EXPENSES_WITH_PROJECT

EXPOSE 1242

ENV LOG_LEVEL=info

CMD ["./docker-entrypoint.sh"]
