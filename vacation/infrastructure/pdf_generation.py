from __future__ import annotations

from io import BytesIO
from datetime import date, datetime

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.lib import colors

from application.kind_legend import KIND_LABELS_RU
from infrastructure.models import LeaveRequest


def _register_cyrillic_font() -> str:
    candidates = (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/Library/Fonts/Arial Unicode.ttf",
        "C:/Windows/Fonts/arial.ttf",
    )
    for path in candidates:
        try:
            pdfmetrics.registerFont(TTFont("BodyFont", path))
            return "BodyFont"
        except Exception:
            continue
    return "Helvetica"


_FONT = _register_cyrillic_font()


def _fmt_date(d: date | None) -> str:
    return d.strftime("%d.%m.%Y") if d else "—"


def render_leave_request_pdf(req: LeaveRequest) -> bytes:
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=2 * cm,
        rightMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
        title=f"Заявка на отсутствие #{req.id}",
    )
    body = ParagraphStyle(
        "Body",
        parent=getSampleStyleSheet()["BodyText"],
        fontName=_FONT,
        fontSize=11,
        leading=14,
    )
    h1 = ParagraphStyle(
        "H1",
        parent=body,
        fontSize=16,
        leading=20,
        spaceAfter=10,
        spaceBefore=0,
        textColor=colors.HexColor("#1e3a8a"),
    )
    label = ParagraphStyle("Label", parent=body, textColor=colors.HexColor("#475569"))
    small = ParagraphStyle("Small", parent=body, fontSize=9, textColor=colors.HexColor("#64748b"))

    kind_ru = KIND_LABELS_RU.get(req.kind_code, "—")

    flow: list = []
    flow.append(Paragraph("Kosta Legal", small))
    flow.append(Paragraph(f"Заявка на отсутствие №{req.id}", h1))
    flow.append(
        Paragraph(
            f"Создана: {req.created_at.strftime('%d.%m.%Y %H:%M')} · Статус: <b>{req.status}</b>",
            small,
        )
    )
    flow.append(Spacer(1, 0.4 * cm))

    rows = [
        ("Сотрудник", req.employee_full_name or "—"),
        ("Должность", req.employee_position or "—"),
        ("E-mail сотрудника", req.employee_email or "—"),
        ("Согласующий партнёр", req.partner_full_name or f"User #{req.partner_user_id}"),
        ("E-mail партнёра", req.partner_email or "—"),
        ("Вид отсутствия", kind_ru),
        ("Период", f"с {_fmt_date(req.date_from)} по {_fmt_date(req.date_to)}"),
        ("Календарных дней", str(req.days_count)),
        ("Комментарий сотрудника", (req.reason or "").strip() or "—"),
    ]
    if req.status != "pending":
        rows.append(
            (
                "Решение",
                (
                    f"{req.status} · "
                    + (req.decision_at.strftime("%d.%m.%Y %H:%M") if req.decision_at else "—")
                    + (
                        f"\nКомментарий: {req.decision_reason}"
                        if req.decision_reason
                        else ""
                    )
                ),
            )
        )

    table_data = [[Paragraph(k, label), Paragraph(str(v).replace("\n", "<br/>"), body)] for k, v in rows]
    tbl = Table(table_data, colWidths=[5.5 * cm, 11.5 * cm])
    tbl.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), _FONT),
                ("FONTSIZE", (0, 0), (-1, -1), 11),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LINEBELOW", (0, 0), (-1, -1), 0.4, colors.HexColor("#e2e8f0")),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    flow.append(tbl)
    flow.append(Spacer(1, 1.0 * cm))
    flow.append(
        Paragraph(
            "Прошу согласовать указанный период отсутствия. Электронная подпись (PDF-документ) "
            "формируется автоматически системой Kosta Legal на основании заявки сотрудника.",
            body,
        )
    )
    flow.append(Spacer(1, 1.5 * cm))

    sign_data = [
        [Paragraph("Сотрудник", label), Paragraph(req.employee_full_name or "—", body)],
        [Paragraph("Дата подачи", label), Paragraph(_fmt_date(req.created_at.date() if isinstance(req.created_at, datetime) else None), body)],
    ]
    sign_tbl = Table(sign_data, colWidths=[5.5 * cm, 11.5 * cm])
    sign_tbl.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), _FONT),
                ("FONTSIZE", (0, 0), (-1, -1), 11),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    flow.append(sign_tbl)

    doc.build(flow)
    return buf.getvalue()
