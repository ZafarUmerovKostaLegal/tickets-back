

from __future__ import annotations

from pydantic import BaseModel, Field


class KindLegendEntry(BaseModel):
    kind_code: int = Field(..., ge=1, le=5)
    kind: str = Field(description="Ключ API, совпадает с полем kind в ответах по дням")
    label_ru: str = Field(description="Подпись для легенды и тултипов")
    color_hex: str = Field(description="Фон «плашки» / ячейки, формат #RRGGBB")
    color_text_hex: str = Field(description="Цвет текста на плашке для контраста")


KIND_LEGEND_ENTRIES: list[KindLegendEntry] = [
    KindLegendEntry(
        kind_code=1,
        kind="annual_vacation",
        label_ru="ежегодный отпуск",
        color_hex="#E8D5F2",
        color_text_hex="#4A148C",
    ),
    KindLegendEntry(
        kind_code=2,
        kind="sick_leave",
        label_ru="отсутствие по болезни",
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
        label_ru="командировка",
        color_hex="#C8E6C9",
        color_text_hex="#1B5E20",
    ),
    KindLegendEntry(
        kind_code=5,
        kind="remote_work",
        label_ru="дистанционный режим",
        color_hex="#FFF59D",
        color_text_hex="#F57F17",
    ),
]
