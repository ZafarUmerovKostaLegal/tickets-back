FROM python:3.12-slim

WORKDIR /app

RUN useradd -r -u 10001 -g root appuser

COPY kosta_daily/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend_common ./backend_common
COPY kosta_daily/ ./

RUN chown -R appuser:root /app

EXPOSE 1251

USER appuser

CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "1251", "--no-access-log", "--log-level", "info"]
