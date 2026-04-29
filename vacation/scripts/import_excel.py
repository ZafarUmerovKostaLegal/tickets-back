
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from application.excel_schedule_import import import_schedule_from_workbook
from infrastructure.db_sync import sync_engine_url


def main() -> None:
    p = argparse.ArgumentParser(description="Импорт графика отсутствий из Excel в БД vacation.")
    p.add_argument("xlsx", type=Path, help="Путь к .xlsx")
    p.add_argument("--year", type=int, default=2026)
    p.add_argument("--sheet", type=str, default=None, help="Имя листа (по умолчанию первый)")
    args = p.parse_args()

    url = (os.environ.get("DATABASE_URL") or "").strip()
    if not url:
        print("Задайте DATABASE_URL (postgresql://...)", file=sys.stderr)
        sys.exit(1)
    if not args.xlsx.is_file():
        print(f"Файл не найден: {args.xlsx}", file=sys.stderr)
        sys.exit(1)

    engine = create_engine(sync_engine_url(url), echo=False)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    with SessionLocal() as session:
        n_emp, n_day = import_schedule_from_workbook(
            session,
            year=args.year,
            sheet_name=args.sheet,
            source=args.xlsx,
        )
    print(f"Импорт завершён: сотрудников={n_emp}, отмеченных дней={n_day}, год={args.year}")


if __name__ == "__main__":
    main()
