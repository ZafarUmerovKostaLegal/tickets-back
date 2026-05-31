from __future__ import annotations

from datetime import date, datetime, timedelta
from io import BytesIO

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import simpleSplit
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from application.leave_pdf_copy import DEFAULT_PDF_COPY, FIRM_LINE, KIND_PDF_COPY
from infrastructure.models import LeaveRequest

_FONT_REG = "LeavePdfRegular"
_FONT_SIZE = 12
_LINE_H = 5.8 * mm
_FONTS_READY = False


def _register_fonts() -> str:
    global _FONTS_READY, _FONT_REG
    if _FONTS_READY:
        return _FONT_REG
    candidates = (
        "C:/Windows/Fonts/times.ttf",
        "C:/Windows/Fonts/Times New Roman.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
        "/usr/share/fonts/truetype/msttcorefonts/Times_New_Roman.ttf",
    )
    for path in candidates:
        try:
            pdfmetrics.registerFont(TTFont(_FONT_REG, path))
            _FONTS_READY = True
            return _FONT_REG
        except Exception:
            continue
    _FONT_REG = "Times-Roman"
    _FONTS_READY = True
    return _FONT_REG


_MONTHS_GEN = (
    "",
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)


def _fmt_date(d: date | None) -> str:
    return d.strftime("%d.%m.%Y") if d else "__________"


def _submission_date(req: LeaveRequest) -> date:
    if isinstance(req.created_at, datetime):
        return req.created_at.date()
    return date.today()


def _return_date(d_to: date) -> str:
    return _fmt_date(d_to + timedelta(days=1))


def _date_phrase(d: date) -> str:
    return f"«{d.day}» {_MONTHS_GEN[d.month]} {d.year} год."


def _partner_dative(req: LeaveRequest) -> str:
    name = (req.partner_full_name or "").strip()
    return name or f"User #{req.partner_user_id}"


def _employee_role_label(req: LeaveRequest) -> str:
    pos = (req.employee_position or "").strip().lower()
    if pos:
        return pos
    return "помощника"


def _copy_for(req: LeaveRequest) -> tuple[str, str]:
    tpl = KIND_PDF_COPY.get(req.kind_code, DEFAULT_PDF_COPY)
    ctx = {
        "date_from": _fmt_date(req.date_from),
        "date_to": _fmt_date(req.date_to),
        "days_count": str(req.days_count),
        "return_date": _return_date(req.date_to),
    }
    body = tpl.body.format(**ctx)
    return tpl.subject, body


def _draw_wrapped(c: canvas.Canvas, text: str, x: float, y: float, max_w: float, font: str) -> float:
    lines = simpleSplit(text, font, _FONT_SIZE, max_w)
    for line in lines:
        c.drawString(x, y, line)
        y -= _LINE_H
    return y


def _draw_underline(c: canvas.Canvas, x: float, y: float, width: float) -> None:
    c.line(x, y - 1.2, x + width, y - 1.2)


def _draw_centered_lines(c: canvas.Canvas, lines: list[str], y: float, page_w: float) -> float:
    cx = page_w / 2
    for line in lines:
        c.drawCentredString(cx, y, line)
        y -= _LINE_H
    return y


def render_leave_request_pdf(req: LeaveRequest) -> bytes:
    """PDF заявления по образцу ОТПУСК.doc (Kosta Legal)."""
    font = _register_fonts()
    buf = BytesIO()
    page_w, page_h = A4
    c = canvas.Canvas(buf, pagesize=A4, title=f"Заявление #{req.id}")

    left = 25 * mm
    right = page_w - 25 * mm
    text_w = right - left

    c.setFont(font, _FONT_SIZE)

    y = page_h - 25 * mm
    y = _draw_centered_lines(
        c,
        [
            "Управляющему партнеру",
            FIRM_LINE,
            _partner_dative(req),
        ],
        y,
        page_w,
    )

    y -= 10 * mm
    prefix = f"От {_employee_role_label(req)} "
    c.drawString(left, y, prefix)
    prefix_w = c.stringWidth(prefix, font, _FONT_SIZE)
    name_x = left + prefix_w
    employee_name = (req.employee_full_name or "").strip()
    if employee_name:
        c.drawString(name_x, y, employee_name)
        underline_w = max(55 * mm, c.stringWidth(employee_name, font, _FONT_SIZE) + 6 * mm)
    else:
        underline_w = 70 * mm
    _draw_underline(c, name_x, y, underline_w)

    y -= 18 * mm
    c.drawCentredString(page_w / 2, y, "З А Я В Л Е Н И Е")

    subject, body = _copy_for(req)
    y -= 10 * mm
    c.drawCentredString(page_w / 2, y, subject)

    y -= 12 * mm
    y = _draw_wrapped(c, body, left, y, text_w, font)

    if (req.reason or "").strip():
        y -= 4 * mm
        y = _draw_wrapped(c, f"Примечание: {req.reason.strip()}", left, y, text_w, font)

    y -= 16 * mm
    _draw_underline(c, left + 35 * mm, y, 45 * mm)
    _draw_underline(c, left + 85 * mm, y, 45 * mm)

    y -= 14 * mm
    _draw_underline(c, left, y, text_w)

    y -= 16 * mm
    sub_d = _submission_date(req)
    date_line = _date_phrase(sub_d)
    c.drawRightString(right, y, date_line)

    c.showPage()
    c.save()
    return buf.getvalue()
