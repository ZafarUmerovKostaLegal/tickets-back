FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY time_tracking/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend_common ./backend_common
COPY time_tracking/ ./

EXPOSE 1241

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "1241", "--no-access-log", "--log-level", "info"]
