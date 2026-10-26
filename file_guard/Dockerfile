FROM python:3.12-slim

WORKDIR /app

RUN useradd -r -u 10001 -g root appuser

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY file_guard/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend_common ./backend_common
COPY file_guard/ ./

RUN mkdir -p /app/data/files && chown -R appuser:root /app/data

EXPOSE 1253

RUN chown -R appuser:root /app
USER appuser

CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "1253", "--no-access-log", "--log-level", "info"]
