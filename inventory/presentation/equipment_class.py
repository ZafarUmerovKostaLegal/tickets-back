from typing import Optional

from fastapi import HTTPException

VALID_EQUIPMENT_CLASSES = frozenset({"A", "B", "C", "D", "E"})


def normalize_equipment_class(value: Optional[str]) -> Optional[str]:
    """Нормализует класс оборудования: A–E или None."""
    if value is None:
        return None
    s = (value or "").strip().upper()
    if not s:
        return None
    if s not in VALID_EQUIPMENT_CLASSES:
        raise HTTPException(
            status_code=400,
            detail="equipment_class must be one of: A, B, C, D, E",
        )
    return s
