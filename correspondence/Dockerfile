FROM python:3.12-slim

WORKDIR /app

RUN useradd -r -u 10001 -g root appuser

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    curl \
    libreoffice-writer \
    fonts-dejavu-core \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

COPY correspondence/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend_common ./backend_common
COPY correspondence/ ./

RUN sed -i 's/\r$//' docker-entrypoint.sh && chmod +x docker-entrypoint.sh

EXPOSE 1249

ENV LOG_LEVEL=info
ENV HOME=/tmp

RUN chown -R appuser:root /app
USER appuser

CMD ["./docker-entrypoint.sh"]
