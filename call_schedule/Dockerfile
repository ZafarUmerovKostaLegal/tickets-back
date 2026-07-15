FROM python:3.12-slim

WORKDIR /app

RUN useradd -r -u 10001 -g root appuser

COPY call_schedule/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY call_schedule/ ./

EXPOSE 1245

RUN chown -R appuser:root /app
USER appuser

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "1245", "--no-access-log", "--log-level", "info"]
