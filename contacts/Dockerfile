FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY contacts/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend_common ./backend_common
COPY contacts/ ./

EXPOSE 1248

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "1248", "--no-access-log", "--log-level", "info"]
