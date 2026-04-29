FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY tickets/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend_common ./backend_common
COPY tickets/ ./

EXPOSE 1235

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "1235", "--no-access-log", "--log-level", "info"]
