FROM python:3.12-slim

WORKDIR /app

RUN useradd -r -u 10001 -g root appuser

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    fonts-dejavu-core \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

COPY vacation/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend_common ./backend_common
COPY vacation/ ./

COPY vacation/docker-entrypoint.sh /docker-entrypoint.sh
RUN sed -i 's/\r$//' /docker-entrypoint.sh && chmod +x /docker-entrypoint.sh \
    && mkdir -p /app/media/vacation_leave_requests \
    && chown -R appuser:root /app

EXPOSE 1244

# Entrypoint starts as root to fix MEDIA_PATH ownership, then drops to appuser.
USER root
ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "1244", "--no-access-log", "--log-level", "info"]
