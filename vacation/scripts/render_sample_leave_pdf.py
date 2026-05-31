"""Сгенерировать пример PDF заявления (для проверки вёрстки)."""
from __future__ import annotations

import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from infrastructure.models import LeaveRequest
from infrastructure.pdf_generation import render_leave_request_pdf

req = LeaveRequest(
    id=1,
    employee_user_id=1,
    employee_full_name="Иванова Мария Петровна",
    employee_email="m.ivanova@example.com",
    employee_position="помощника",
    partner_user_id=2,
    partner_full_name="Ахмаджонову Азизбеку Ахмадовичу",
    partner_email="a.ahmadjonov@kostalegal.com",
    kind_code=1,
    date_from=date(2026, 6, 1),
    date_to=date(2026, 6, 14),
    days_count=14,
    reason=None,
    status="pending",
    decision_at=None,
    decision_reason=None,
    decided_by_user_id=None,
    pdf_storage_key=None,
    email_sent_at=None,
    created_at=datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc),
    updated_at=None,
)

out = Path(__file__).with_name("sample_leave_request.pdf")
out.write_bytes(render_leave_request_pdf(req))
print(out)
