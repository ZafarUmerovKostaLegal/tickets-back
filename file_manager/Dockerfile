FROM python:3.12-slim

WORKDIR /app

RUN useradd -r -u 10001 -g root appuser

COPY file_manager/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend_common ./backend_common
COPY file_manager/ ./

RUN mkdir -p /app/data && chown -R appuser:root /app /app/data

EXPOSE 1255

USER appuser

CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "1255", "--no-access-log", "--log-level", "info"]
