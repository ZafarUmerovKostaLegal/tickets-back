FROM python:3.12-slim

WORKDIR /app

RUN useradd -r -u 10001 -g root appuser

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY chat/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
# The pip launcher can fail with "exec format error". Replace it with a plain Python script.
RUN python -c "from pathlib import Path; p=Path('/usr/local/bin/uvicorn'); p.write_text('#!/usr/local/bin/python3\nimport sys\nfrom uvicorn.main import main\nif __name__ == \"__main__\":\n    raise SystemExit(main())\n'); p.chmod(0o755)"

COPY backend_common ./backend_common
COPY chat/ ./

EXPOSE 1252

RUN chown -R appuser:root /app
USER appuser

CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "1252", "--no-access-log", "--log-level", "info"]
