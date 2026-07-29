from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LeavePdfCopy:
    subject: str
    body: str


_ROLE_GENITIVE: dict[str, str] = {
    "partner": "партнера",
    "assistant": "помощника",
    "lawyer": "юриста",
    "attorney": "адвоката",
    "intern": "стажера",
    "trainee": "стажера",
    "партнер": "партнера",
    "партнёр": "партнёра",
    "помощник": "помощника",
    "юрист": "юриста",
    "адвокат": "адвоката",
    "стажер": "стажера",
    "стажёр": "стажёра",
}


def employee_role_genitive(position: str | None) -> str:
    """Return the employee role for the PDF sender line with an uppercase initial."""
    pos = (position or "").strip()
    if not pos:
        return "Помощника"
    role = _ROLE_GENITIVE.get(pos.casefold(), pos)
    return f"{role[:1].upper()}{role[1:]}"


                                                                      
KIND_PDF_COPY: dict[int, LeavePdfCopy] = {
    1: LeavePdfCopy(
        subject="О предоставлении ежегодного отпуска.",
        body=(
            "Прошу предоставить мне ежегодный оплачиваемый отпуск на период с {date_from} "
            "({days_count} количество рабочих дней) выход {return_date}."
        ),
    ),
    2: LeavePdfCopy(
        subject="О предоставлении больничного листа.",
        body=(
            "Прошу зафиксировать моё отсутствие по больничному листу на период с {date_from} "
            "({days_count} количество рабочих дней) выход {return_date}."
        ),
    ),
    3: LeavePdfCopy(
        subject="О предоставлении нерабочего дня (day off).",
        body=(
            "Прошу предоставить мне нерабочий день (day off) на период с {date_from} "
            "({days_count} количество рабочих дней) выход {return_date}."
        ),
    ),
    4: LeavePdfCopy(
        subject="О командировке.",
        body=(
            "Прошу согласовать мою командировку на период с {date_from} "
            "({days_count} количество рабочих дней) выход {return_date}."
        ),
    ),
    5: LeavePdfCopy(
        subject="О переводе на дистанционный режим работы.",
        body=(
            "Прошу перевести меня на дистанционный режим работы на период с {date_from} "
            "({days_count} количество рабочих дней) выход {return_date}."
        ),
    ),
}

DEFAULT_PDF_COPY = LeavePdfCopy(
    subject="О предоставлении отсутствия.",
    body=(
        "Прошу согласовать моё отсутствие на период с {date_from} "
        "({days_count} количество рабочих дней) выход {return_date}."
    ),
)

FIRM_LINE = 'Адвокатской фирмы «Kosta Legal»'
MANAGING_PARTNER_NAME = 'Azizbek Akhmadjonov'
