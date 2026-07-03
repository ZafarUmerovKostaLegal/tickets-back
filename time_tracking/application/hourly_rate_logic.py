

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Literal

_MIN = date(1, 1, 1)
_MAX = date(9999, 12, 31)


def effective_start(d: date | None) -> date:
    return d if d is not None else _MIN


def effective_end(d: date | None) -> date:
    return d if d is not None else _MAX


def intervals_overlap(
    a_start: date | None,
    a_end: date | None,
    b_start: date | None,
    b_end: date | None,
) -> bool:

    if effective_end(a_end) < effective_start(b_start):
        return False
    if effective_end(b_end) < effective_start(a_start):
        return False
    return True


def validate_range_order(valid_from: date | None, valid_to: date | None) -> None:
    if valid_from is not None and valid_to is not None and valid_from > valid_to:
        raise ValueError("Дата начала не может быть позже даты окончания")


def normalize_currency(currency: str | None) -> str:
    return (currency or "USD").strip().upper()[:10] or "USD"


def filter_rates_by_currency(rows: list[Any], currency: str) -> list[Any]:

    cur = normalize_currency(currency)
    return [r for r in rows if normalize_currency(getattr(r, "currency", None)) == cur]


def pick_rate_for_date(
    rows: list[Any],
    on_date: date,
    *,
    valid_from_attr: str = "valid_from",
    valid_to_attr: str = "valid_to",
) -> Any | None:

    candidates: list[Any] = []
    for row in rows:
        vf = getattr(row, valid_from_attr, None)
        vt = getattr(row, valid_to_attr, None)
        if effective_start(vf) <= on_date <= effective_end(vt):
            candidates.append(row)
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

                                                                                    
                                                       
    def _key(r: Any) -> tuple[date, str]:
        vf = getattr(r, valid_from_attr, None)
        start = effective_start(vf)
        rid = str(getattr(r, "id", "") or "")
        return start, rid

    return max(candidates, key=_key)


@dataclass
class RateChangePlan:
    """План смены ставки с конкретного дня (effective_from).

    - update_existing_id: период уже начинается ровно в этот день — просто меняем сумму.
    - close_existing_id / close_valid_to: закрыть действующий период днём раньше.
    - create_before_amount / create_before_valid_to: при первом проектном
      переопределении сохранить период «до X» по старой (действующей) ставке,
      чтобы расчёт до X не обнулялся (проектные ставки имеют приоритет над общими).
    - create_new / create_new_valid_to: создать новый период с новой суммой с дня X.
    """

    update_existing_id: str | None = None
    close_existing_id: str | None = None
    close_valid_to: date | None = None
    create_before_amount: Any | None = None
    create_before_valid_to: date | None = None
    create_new: bool = True
    create_new_valid_to: date | None = None


def build_rate_change_plan(
    project_rates: list[Any],
    global_rates: list[Any],
    effective_from: date,
    *,
    valid_from_attr: str = "valid_from",
    valid_to_attr: str = "valid_to",
) -> RateChangePlan:
    """Строит план смены проектной ставки пользователя с дня effective_from.

    Старая ставка действует до дня перед effective_from, новая — с effective_from.
    Функция чистая (без БД), чтобы её можно было покрыть юнит-тестами.
    """

    def _vf(r: Any) -> date | None:
        return getattr(r, valid_from_attr, None)

    covering = pick_rate_for_date(
        project_rates,
        effective_from,
        valid_from_attr=valid_from_attr,
        valid_to_attr=valid_to_attr,
    )

    if covering is not None and effective_start(_vf(covering)) == effective_from:
        return RateChangePlan(
            update_existing_id=str(getattr(covering, "id", "") or ""),
            create_new=False,
        )

    day_before = effective_from - timedelta(days=1)
    future_starts = sorted(
        effective_start(_vf(r))
        for r in project_rates
        if effective_start(_vf(r)) > effective_from
    )
    new_valid_to = (future_starts[0] - timedelta(days=1)) if future_starts else None

    plan = RateChangePlan(create_new_valid_to=new_valid_to)

    if covering is not None:
        plan.close_existing_id = str(getattr(covering, "id", "") or "")
        plan.close_valid_to = day_before
    elif not project_rates:
        eff_global = pick_rate_for_date(
            global_rates,
            effective_from,
            valid_from_attr=valid_from_attr,
            valid_to_attr=valid_to_attr,
        )
        if eff_global is not None:
            plan.create_before_amount = getattr(eff_global, "amount", None)
            plan.create_before_valid_to = day_before

    return plan


@dataclass
class RateReconcileAction:
    kind: Literal["close", "delete"]
    rate_id: str
    valid_to: date | None = None


def plan_overlapping_reconcile(
    project_rates: list[Any],
    effective_from: date,
    new_valid_to: date | None,
    *,
    update_existing_id: str | None = None,
    keeper_rate_id: str | None = None,
    valid_from_attr: str = "valid_from",
    valid_to_attr: str = "valid_to",
) -> list[RateReconcileAction]:
    """План закрытия/удаления пересекающихся проектных ставок перед сменой с даты.

    Оставляет одну ставку, действовавшую накануне effective_from (для отчётов «до»),
    удаляет дубликаты и периоды, которые мешают новому интервалу.
    """

    if update_existing_id:
        return []

    day_before = effective_from - timedelta(days=1)

    def _vf(r: Any) -> date | None:
        return getattr(r, valid_from_attr, None)

    def _rid(r: Any) -> str:
        return str(getattr(r, "id", "") or "")

    keeper = None
    if keeper_rate_id:
        keeper = next((r for r in project_rates if _rid(r) == keeper_rate_id), None)
    if keeper is None:
        keeper = pick_rate_for_date(
            project_rates,
            day_before,
            valid_from_attr=valid_from_attr,
            valid_to_attr=valid_to_attr,
        )
    keeper_id = _rid(keeper) if keeper is not None else None

    actions: list[RateReconcileAction] = []
    deleted: set[str] = set()

    for rate in project_rates:
        rid = _rid(rate)
        if not rid:
            continue

        overlaps_new = intervals_overlap(
            getattr(rate, valid_from_attr, None),
            getattr(rate, valid_to_attr, None),
            effective_from,
            new_valid_to,
        )
        overlaps_hist_dup = (
            keeper_id is not None
            and rid != keeper_id
            and intervals_overlap(
                getattr(rate, valid_from_attr, None),
                getattr(rate, valid_to_attr, None),
                None,
                day_before,
            )
        )
        if not overlaps_new and not overlaps_hist_dup:
            continue

        if overlaps_new:
            if effective_start(_vf(rate)) < effective_from:
                if rid == keeper_id:
                    actions.append(RateReconcileAction(kind="close", rate_id=rid, valid_to=day_before))
                elif rid not in deleted:
                    actions.append(RateReconcileAction(kind="delete", rate_id=rid))
                    deleted.add(rid)
            elif rid not in deleted:
                actions.append(RateReconcileAction(kind="delete", rate_id=rid))
                deleted.add(rid)
        elif overlaps_hist_dup and rid not in deleted:
            actions.append(RateReconcileAction(kind="delete", rate_id=rid))
            deleted.add(rid)

    return actions
