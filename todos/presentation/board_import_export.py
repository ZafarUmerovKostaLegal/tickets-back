"""Import/export досок: native kosta_todos JSON + Trello JSON export."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.models import (
    TodoBoardLabelModel,
    TodoCardChecklistItemModel,
    TodoCardLabelModel,
    TodoCardModel,
    TodoColumnModel,
)
from infrastructure.repositories import KanbanRepository
from presentation.board_payload import BoardOut, build_board_out

EXPORT_FORMAT = "kosta_todos"
EXPORT_VERSION = 1

COLUMN_COLORS = (
    "#7c3aed",
    "#2563eb",
    "#ea580c",
    "#059669",
    "#dc2626",
    "#0891b2",
    "#ca8a04",
    "#6b7280",
)

# Trello named colors → approx hex used in our UI
_TRELLO_LABEL_COLORS: dict[str, str] = {
    "green": "#059669",
    "yellow": "#ca8a04",
    "orange": "#ea580c",
    "red": "#dc2626",
    "purple": "#7c3aed",
    "blue": "#2563eb",
    "sky": "#0891b2",
    "lime": "#65a30d",
    "pink": "#db2777",
    "black": "#374151",
    "null": "#6b7280",
}


class BoardImportError(ValueError):
    """Некорректный файл импорта."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_due(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    raw = str(value).strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _clip(value: str | None, max_len: int, *, fallback: str = "") -> str:
    text = (value or "").strip() or fallback
    return text[:max_len]


def board_out_to_export_dict(board: BoardOut) -> dict[str, Any]:
    """Портативный JSON без внутренних id / media / участников / комментариев."""
    labels_payload = [
        {"title": lb.title, "color": lb.color, "position": lb.position}
        for lb in sorted(board.board_labels, key=lambda x: (x.position, x.id))
    ]
    columns_payload: list[dict[str, Any]] = []
    for col in sorted(board.columns, key=lambda x: (x.position, x.id)):
        cards_payload: list[dict[str, Any]] = []
        for card in sorted(col.cards, key=lambda x: (x.position, x.id)):
            if card.is_archived:
                continue
            cards_payload.append(
                {
                    "title": card.title,
                    "body": card.body,
                    "due_at": card.due_at.isoformat() if card.due_at else None,
                    "is_completed": bool(card.is_completed),
                    "labels": [{"title": lb.title, "color": lb.color} for lb in card.labels],
                    "checklist": [
                        {
                            "title": it.title,
                            "is_done": bool(it.is_done),
                            "position": it.position,
                        }
                        for it in sorted(card.checklist, key=lambda x: (x.position, x.id))
                    ],
                }
            )
        columns_payload.append(
            {
                "title": col.title,
                "color": col.color,
                "is_collapsed": bool(col.is_collapsed),
                "cards": cards_payload,
            }
        )
    return {
        "format": EXPORT_FORMAT,
        "version": EXPORT_VERSION,
        "exported_at": _utc_now().isoformat(),
        "board": {
            "title": board.title,
            "color": board.color,
            "board_labels": labels_payload,
            "columns": columns_payload,
        },
    }


def _normalize_native_board(raw: dict[str, Any]) -> dict[str, Any]:
    board = raw.get("board") if isinstance(raw.get("board"), dict) else raw
    if not isinstance(board, dict):
        raise BoardImportError("Invalid board payload")
    title = _clip(str(board.get("title") or "Imported board"), 200, fallback="Imported board")
    color = board.get("color")
    color_s = _clip(str(color), 32) if color else None
    labels_in = board.get("board_labels") or board.get("labels") or []
    if not isinstance(labels_in, list):
        labels_in = []
    columns_in = board.get("columns") or []
    if not isinstance(columns_in, list):
        raise BoardImportError("columns must be a list")

    labels: list[dict[str, Any]] = []
    for i, lb in enumerate(labels_in):
        if not isinstance(lb, dict):
            continue
        labels.append(
            {
                "title": _clip(str(lb.get("title") or f"Label {i + 1}"), 200, fallback=f"Label {i + 1}"),
                "color": _clip(str(lb.get("color") or "#6b7280"), 32, fallback="#6b7280"),
                "position": int(lb.get("position") if lb.get("position") is not None else i),
            }
        )

    columns: list[dict[str, Any]] = []
    for i, col in enumerate(columns_in):
        if not isinstance(col, dict):
            continue
        cards_in = col.get("cards") or []
        if not isinstance(cards_in, list):
            cards_in = []
        cards: list[dict[str, Any]] = []
        for j, card in enumerate(cards_in):
            if not isinstance(card, dict):
                continue
            if card.get("is_archived") or card.get("closed"):
                continue
            card_labels = card.get("labels") or []
            if not isinstance(card_labels, list):
                card_labels = []
            checklist = card.get("checklist") or card.get("checkItems") or []
            if not isinstance(checklist, list):
                checklist = []
            cards.append(
                {
                    "title": _clip(str(card.get("title") or card.get("name") or "Untitled"), 500, fallback="Untitled"),
                    "body": (str(card.get("body") or card.get("desc") or "").strip() or None),
                    "due_at": _parse_due(card.get("due_at") or card.get("due")),
                    "is_completed": bool(card.get("is_completed") or card.get("dueComplete")),
                    "labels": [
                        {
                            "title": _clip(str(x.get("title") or x.get("name") or ""), 200),
                            "color": _clip(str(x.get("color") or "#6b7280"), 32, fallback="#6b7280"),
                        }
                        for x in card_labels
                        if isinstance(x, dict) and (x.get("title") or x.get("name"))
                    ],
                    "checklist": [
                        {
                            "title": _clip(
                                str(it.get("title") or it.get("name") or f"Item {k + 1}"),
                                500,
                                fallback=f"Item {k + 1}",
                            ),
                            "is_done": bool(
                                it.get("is_done")
                                or it.get("checked")
                                or str(it.get("state") or "").lower() == "complete"
                            ),
                            "position": int(it.get("position") if it.get("position") is not None else k),
                        }
                        for k, it in enumerate(checklist)
                        if isinstance(it, dict)
                    ],
                }
            )
        columns.append(
            {
                "title": _clip(str(col.get("title") or col.get("name") or f"Column {i + 1}"), 200, fallback=f"Column {i + 1}"),
                "color": _clip(
                    str(col.get("color") or COLUMN_COLORS[i % len(COLUMN_COLORS)]),
                    32,
                    fallback=COLUMN_COLORS[i % len(COLUMN_COLORS)],
                ),
                "is_collapsed": bool(col.get("is_collapsed")),
                "cards": cards,
            }
        )
    return {"title": title, "color": color_s or None, "board_labels": labels, "columns": columns}


def _normalize_trello_board(raw: dict[str, Any]) -> dict[str, Any]:
    title = _clip(str(raw.get("name") or "Trello board"), 200, fallback="Trello board")
    lists = [L for L in (raw.get("lists") or []) if isinstance(L, dict) and not L.get("closed")]
    lists.sort(key=lambda L: (L.get("pos") is None, L.get("pos") or 0, str(L.get("id") or "")))
    cards_all = [c for c in (raw.get("cards") or []) if isinstance(c, dict) and not c.get("closed")]
    checklists = [cl for cl in (raw.get("checklists") or []) if isinstance(cl, dict)]
    checklist_by_card: dict[str, list[dict[str, Any]]] = {}
    for cl in checklists:
        cid = str(cl.get("idCard") or "")
        if not cid:
            continue
        items = []
        for k, it in enumerate(cl.get("checkItems") or []):
            if not isinstance(it, dict):
                continue
            items.append(
                {
                    "title": _clip(str(it.get("name") or f"Item {k + 1}"), 500, fallback=f"Item {k + 1}"),
                    "is_done": str(it.get("state") or "").lower() == "complete",
                    "position": int(it.get("pos") if it.get("pos") is not None else k),
                }
            )
        checklist_by_card.setdefault(cid, []).extend(items)

    trello_labels = [lb for lb in (raw.get("labels") or []) if isinstance(lb, dict)]
    board_labels: list[dict[str, Any]] = []
    seen_label_keys: set[tuple[str, str]] = set()
    for i, lb in enumerate(trello_labels):
        name = _clip(str(lb.get("name") or ""), 200)
        if not name:
            continue
        color_name = str(lb.get("color") or "null").lower()
        color = _TRELLO_LABEL_COLORS.get(color_name, "#6b7280")
        key = (name.lower(), color.lower())
        if key in seen_label_keys:
            continue
        seen_label_keys.add(key)
        board_labels.append({"title": name, "color": color, "position": i})

    cards_by_list: dict[str, list[dict[str, Any]]] = {str(L.get("id")): [] for L in lists}
    for c in cards_all:
        lid = str(c.get("idList") or "")
        if lid not in cards_by_list:
            continue
        card_id = str(c.get("id") or "")
        card_labels_raw = c.get("labels") or []
        if not isinstance(card_labels_raw, list):
            card_labels_raw = []
        card_labels = []
        for lb in card_labels_raw:
            if not isinstance(lb, dict):
                continue
            name = _clip(str(lb.get("name") or ""), 200)
            if not name:
                continue
            color_name = str(lb.get("color") or "null").lower()
            card_labels.append(
                {
                    "title": name,
                    "color": _TRELLO_LABEL_COLORS.get(color_name, "#6b7280"),
                }
            )
            key = (name.lower(), card_labels[-1]["color"].lower())
            if key not in seen_label_keys:
                seen_label_keys.add(key)
                board_labels.append(
                    {
                        "title": name,
                        "color": card_labels[-1]["color"],
                        "position": len(board_labels),
                    }
                )
        cards_by_list[lid].append(
            {
                "title": _clip(str(c.get("name") or "Untitled"), 500, fallback="Untitled"),
                "body": (str(c.get("desc") or "").strip() or None),
                "due_at": _parse_due(c.get("due")),
                "is_completed": bool(c.get("dueComplete")),
                "labels": card_labels,
                "checklist": checklist_by_card.get(card_id, []),
                "_pos": c.get("pos"),
            }
        )

    columns: list[dict[str, Any]] = []
    for i, L in enumerate(lists):
        lid = str(L.get("id"))
        cards = cards_by_list.get(lid, [])
        cards.sort(key=lambda c: (c.get("_pos") is None, c.get("_pos") or 0))
        for c in cards:
            c.pop("_pos", None)
        columns.append(
            {
                "title": _clip(str(L.get("name") or f"List {i + 1}"), 200, fallback=f"List {i + 1}"),
                "color": COLUMN_COLORS[i % len(COLUMN_COLORS)],
                "is_collapsed": False,
                "cards": cards,
            }
        )
    return {"title": title, "color": None, "board_labels": board_labels, "columns": columns}


def detect_and_normalize_import(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise BoardImportError("JSON root must be an object")
    fmt = str(payload.get("format") or "").strip().lower()
    if fmt == EXPORT_FORMAT or "columns" in payload or isinstance(payload.get("board"), dict):
        # native / BoardOut-like
        if "lists" in payload and "cards" in payload and "columns" not in payload and not isinstance(
            payload.get("board"), dict
        ):
            return _normalize_trello_board(payload)
        return _normalize_native_board(payload)
    if "lists" in payload and "cards" in payload:
        return _normalize_trello_board(payload)
    raise BoardImportError(
        "Unrecognized board format. Expected kosta_todos export or Trello JSON export."
    )


async def import_normalized_board(
    session: AsyncSession,
    owner_user_id: int,
    normalized: dict[str, Any],
) -> int:
    """Создаёт новую personal-доску из нормализованного payload. Возвращает board_id."""
    repo = KanbanRepository(session)
    board = await repo.create_board(
        owner_user_id,
        title=str(normalized["title"]),
        visibility="personal",
        color=normalized.get("color"),
        member_user_ids=[],
        instant_add_members=False,
        with_default_columns=False,
    )
    now = _utc_now()

    label_id_by_key: dict[tuple[str, str], int] = {}
    for i, lb in enumerate(normalized.get("board_labels") or []):
        row = TodoBoardLabelModel(
            board_id=board.id,
            title=str(lb["title"])[:200],
            color=str(lb.get("color") or "#6b7280")[:32],
            position=int(lb.get("position") if lb.get("position") is not None else i),
            created_at=now,
            updated_at=None,
        )
        session.add(row)
        await session.flush()
        label_id_by_key[(row.title.lower(), row.color.lower())] = row.id

    for col_idx, col in enumerate(normalized.get("columns") or []):
        col_row = TodoColumnModel(
            board_id=board.id,
            title=str(col["title"])[:200],
            position=col_idx,
            color=str(col.get("color") or COLUMN_COLORS[col_idx % len(COLUMN_COLORS)])[:32],
            is_collapsed=bool(col.get("is_collapsed")),
            created_at=now,
            updated_at=None,
        )
        session.add(col_row)
        await session.flush()

        for card_idx, card in enumerate(col.get("cards") or []):
            card_row = TodoCardModel(
                column_id=col_row.id,
                title=str(card["title"])[:500],
                body=card.get("body"),
                position=card_idx,
                due_at=card.get("due_at"),
                is_completed=bool(card.get("is_completed")),
                is_archived=False,
                created_at=now,
                updated_at=None,
            )
            session.add(card_row)
            await session.flush()

            for lab in card.get("labels") or []:
                key = (str(lab.get("title") or "").lower(), str(lab.get("color") or "#6b7280").lower())
                if not key[0]:
                    continue
                lid = label_id_by_key.get(key)
                if lid is None:
                    # ensure board label exists
                    row = TodoBoardLabelModel(
                        board_id=board.id,
                        title=str(lab["title"])[:200],
                        color=str(lab.get("color") or "#6b7280")[:32],
                        position=len(label_id_by_key),
                        created_at=now,
                        updated_at=None,
                    )
                    session.add(row)
                    await session.flush()
                    label_id_by_key[key] = row.id
                    lid = row.id
                session.add(TodoCardLabelModel(card_id=card_row.id, label_id=lid))

            for item_idx, item in enumerate(
                sorted(card.get("checklist") or [], key=lambda x: int(x.get("position") or 0))
            ):
                session.add(
                    TodoCardChecklistItemModel(
                        card_id=card_row.id,
                        title=str(item["title"])[:500],
                        is_done=bool(item.get("is_done")),
                        position=item_idx,
                        created_at=now,
                        updated_at=None,
                    )
                )

    await session.flush()
    return board.id


async def export_board_dict(session: AsyncSession, board_id: int, viewer_user_id: int) -> dict[str, Any]:
    board = await build_board_out(session, board_id, viewer_user_id=viewer_user_id)
    return board_out_to_export_dict(board)
