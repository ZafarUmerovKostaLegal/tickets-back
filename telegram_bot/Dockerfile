FROM python:3.12-slim

WORKDIR /app

RUN useradd -r -u 10001 -g root appuser

COPY telegram_bot/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend_common ./backend_common
COPY telegram_bot/ ./

EXPOSE 1242

RUN chown -R appuser:root /app
USER appuser

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "1242", "--no-access-log", "--log-level", "info"]
