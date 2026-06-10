from __future__ import annotations

# Канонический справочник должностей (job positions) — единый источник правды.
#
# Фронт берёт этот список через GET /api/v1/positions (gateway) и показывает его
# выпадающим списком при назначении должности пользователю. По должностям далее
# будем делить доступы в time tracking (правила доступа настраиваются отдельно).
#
# Поле user.position в auth остаётся свободным текстом (VARCHAR), список здесь не
# является жёстким ограничением — он задаёт рекомендованный набор значений и
# порядок их отображения. Чтобы добавить новую должность, допишите её в кортеж.
JOB_POSITIONS: tuple[str, ...] = (
    "Business Development Manager",
    "Contracts and BD Assistant",
    "Accountant",
    "Office Manager",
)


def list_positions() -> list[str]:
    """Список должностей в каноническом порядке (для выпадающего списка на фронте)."""
    return list(JOB_POSITIONS)


def normalize_position(value: str | None) -> str | None:
    """Приводит должность к каноническому написанию из справочника.

    Сопоставление регистронезависимое и игнорирует крайние пробелы. Если значение
    не найдено в справочнике — возвращает исходную обрезанную строку (или None).
    """
    if value is None:
        return None
    trimmed = str(value).strip()
    if not trimmed:
        return None
    target = trimmed.casefold()
    for p in JOB_POSITIONS:
        if p.casefold() == target:
            return p
    return trimmed


def is_known_position(value: str | None) -> bool:
    """True, если должность присутствует в каноническом справочнике."""
    if value is None:
        return False
    target = str(value).strip().casefold()
    if not target:
        return False
    return any(p.casefold() == target for p in JOB_POSITIONS)
