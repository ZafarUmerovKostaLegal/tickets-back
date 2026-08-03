FROM python:3.12-slim

WORKDIR /app

RUN useradd -r -u 10001 -g root appuser

COPY call_schedule/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend_common ./backend_common
COPY call_schedule/ ./

RUN mkdir -p /app/media && chown -R appuser:root /app /app/media

EXPOSE 1245

USER appuser

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "1245", "--no-access-log", "--log-level", "info"]
