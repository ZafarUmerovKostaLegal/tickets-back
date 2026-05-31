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

# Скрипт очистки БД для запуска из консоли контейнера (Portainer и т.п.):
#   python /app/wipe_repo/scripts/wipe_all_keep_tt_users.py --dry-run
#   python /app/wipe_repo/scripts/purge_time_tracking_keep_users.py --dry-run
#   python /app/wipe_repo/scripts/purge_expenses_keep_reference.py --dry-run
#   python /app/wipe_repo/scripts/delete_expenses_with_project.py --dry-run
#   python /app/wipe_repo/scripts/purge_tickets_and_todos.py --dry-run
#   python /app/scripts/import_harvest_time_report.py --dry-run
COPY scripts/requirements-wipe.txt /app/wipe_repo/scripts/requirements-wipe.txt
COPY scripts/wipe_all_keep_tt_users.py /app/wipe_repo/scripts/wipe_all_keep_tt_users.py
COPY scripts/purge_time_tracking_keep_users.py /app/wipe_repo/scripts/purge_time_tracking_keep_users.py
COPY scripts/purge_expenses_keep_reference.py /app/wipe_repo/scripts/purge_expenses_keep_reference.py
COPY scripts/delete_expenses_with_project.py /app/wipe_repo/scripts/delete_expenses_with_project.py
COPY scripts/purge_tickets_and_todos.py /app/wipe_repo/scripts/purge_tickets_and_todos.py
COPY scripts/purge_all_tickets.py /app/wipe_repo/scripts/purge_all_tickets.py
COPY scripts/purge_all_todos.py /app/wipe_repo/scripts/purge_all_todos.py
RUN pip install --no-cache-dir -r /app/wipe_repo/scripts/requirements-wipe.txt

COPY auth /app/wipe_repo/auth
COPY tickets /app/wipe_repo/tickets
COPY todos /app/wipe_repo/todos
COPY notifications /app/wipe_repo/notifications
COPY inventory /app/wipe_repo/inventory
COPY attendance /app/wipe_repo/attendance
COPY expenses /app/wipe_repo/expenses
COPY vacation /app/wipe_repo/vacation
COPY time_tracking /app/wipe_repo/time_tracking

EXPOSE 1241

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "1241", "--no-access-log", "--log-level", "info"]
