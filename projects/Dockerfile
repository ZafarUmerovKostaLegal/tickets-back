FROM python:3.12-slim

WORKDIR /app

COPY projects/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend_common ./backend_common
COPY projects/ ./

EXPOSE 1243

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "1243", "--no-access-log", "--log-level", "info"]
