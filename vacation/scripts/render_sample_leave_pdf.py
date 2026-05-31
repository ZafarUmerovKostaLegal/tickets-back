"""Сгенерировать пример PDF заявления (для проверки вёрстки)."""
from __future__ import annotations

import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from infrastructure.models import LeaveRequest
from infrastructure.pdf_generation import render_leave_request_pdf

_BASE = dict(
    employee_user_id=1,
    employee_email="m.ivanova@example.com",
    partner_user_id=2,
    partner_full_name="Ахмаджonovu Азizbeku Akhmadovichu",
    partner_email="a.ahmadjonov@kostalegal.com",
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

annual = LeaveRequest(
    id=1,
    employee_full_name="Иванова Мария Петровна",
    employee_position="помощника",
    kind_code=1,
    **_BASE,
)

remote = LeaveRequest(
    id=2,
    employee_full_name="Zafar Umerov",
    employee_position="partner",
    kind_code=5,
    date_from=date(2026, 5, 31),
    date_to=date(2026, 6, 1),
    days_count=2,
    reason="Проверка работы и отправки",
    partner_full_name="Azizbek Akhmadjonov",
    **_BASE,
)

out_dir = Path(__file__).resolve().parent
(out_dir / "sample_leave_request.pdf").write_bytes(render_leave_request_pdf(annual))
(out_dir / "sample_remote_work_request.pdf").write_bytes(render_leave_request_pdf(remote))
print(out_dir / "sample_leave_request.pdf")
print(out_dir / "sample_remote_work_request.pdf")
