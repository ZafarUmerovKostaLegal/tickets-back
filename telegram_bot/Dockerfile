FROM python:3.12-slim

WORKDIR /app

COPY telegram_bot/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend_common ./backend_common
COPY telegram_bot/ ./

EXPOSE 1242

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "1242", "--no-access-log", "--log-level", "info"]
