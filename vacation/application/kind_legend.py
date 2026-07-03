

from __future__ import annotations

from pydantic import BaseModel, Field


class KindLegendEntry(BaseModel):
    kind_code: int = Field(..., ge=1, le=5)
    kind: str = Field(description="Ключ API, совпадает с полем kind в ответах по дням")
    label_ru: str = Field(description="Подпись для легенды и тултипов")
    color_hex: str = Field(description="Фон «плашки» / ячейки, формат #RRGGBB")
    color_text_hex: str = Field(description="Цвет текста на плашке для контраста")


                                                                                   
REQUESTABLE_KIND_CODES: tuple[int, ...] = (1, 3, 5)

                                                                                    
                                                                                     
DOCUMENT_REQUIRED_KIND_CODES: tuple[int, ...] = (1, 2, 3, 4, 5)

KIND_LEGEND_ENTRIES: list[KindLegendEntry] = [
    KindLegendEntry(
        kind_code=1,
        kind="annual_vacation",
        label_ru="Ежегодный отпуск",
        color_hex="#E8D5F2",
        color_text_hex="#4A148C",
    ),
    KindLegendEntry(
        kind_code=2,
        kind="sick_leave",
        label_ru="Больничный",
        color_hex="#FFCDD2",
        color_text_hex="#B71C1C",
    ),
    KindLegendEntry(
        kind_code=3,
        kind="day_off",
        label_ru="Day Off (нерабочий)",
        color_hex="#81D4FA",
        color_text_hex="#01579B",
    ),
    KindLegendEntry(
        kind_code=4,
        kind="business_trip",
        label_ru="Командировка",
        color_hex="#C8E6C9",
        color_text_hex="#1B5E20",
    ),
    KindLegendEntry(
        kind_code=5,
        kind="remote_work",
        label_ru="Дистанционный режим",
        color_hex="#FFF59D",
        color_text_hex="#F57F17",
    ),
]

KIND_BY_KEY: dict[str, int] = {e.kind: e.kind_code for e in KIND_LEGEND_ENTRIES}
KIND_BY_CODE: dict[int, str] = {e.kind_code: e.kind for e in KIND_LEGEND_ENTRIES}
KIND_LABELS_RU: dict[int, str] = {e.kind_code: e.label_ru for e in KIND_LEGEND_ENTRIES}
ALL_KIND_CODES: tuple[int, ...] = tuple(e.kind_code for e in KIND_LEGEND_ENTRIES)


def document_required_for(kind_code: int) -> bool:
    """Нужен ли документ-основание для ручной записи данной категории."""
    return int(kind_code) in DOCUMENT_REQUIRED_KIND_CODES
