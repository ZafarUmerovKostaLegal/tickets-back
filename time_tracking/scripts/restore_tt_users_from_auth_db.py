"""Restore time tracking users from auth.users table.

Run inside time_tracking service/container:

  python scripts/restore_tt_users_from_auth_db.py --dry-run --auth-db-url "$AUTH_DATABASE_URL"
  python scripts/restore_tt_users_from_auth_db.py --execute --auth-db-url "$AUTH_DATABASE_URL"

Optional:
  --delete-missing   remove TT users that no longer have TT role in auth DB.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from infrastructure.database import async_session_factory, make_async_url
from infrastructure.models import TimeTrackingUserModel
from infrastructure.repository_shared import _now_utc


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Restore TT users from auth.users where time_tracking_role is user/manager."
    )
    p.add_argument(
        "--auth-db-url",
        type=str,
        default=os.getenv("AUTH_DATABASE_URL", "").strip(),
        help="Auth database URL (or env AUTH_DATABASE_URL).",
    )
    p.add_argument(
        "--delete-missing",
        action="store_true",
        help="Delete TT users missing in auth selection.",
    )
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true", help="Show plan only.")
    g.add_argument("--execute", action="store_true", help="Apply changes.")
    return p.parse_args()


async def _load_auth_users(auth_db_url: str) -> list[dict]:
    engine = create_async_engine(make_async_url(auth_db_url), echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as s:
            r = await s.execute(
                text(
                    """
                    SELECT
                        id,
                        email,
                        display_name,
                        picture,
                        role,
                        position,
                        is_blocked,
                        is_archived,
                        time_tracking_role
                    FROM users
                    WHERE time_tracking_role IN ('user', 'manager')
                    ORDER BY id
                    """
                )
            )
            out: list[dict] = []
            for row in r.mappings().all():
                pos = (str(row["position"]).strip() if row["position"] is not None else "")
                if not pos:
                    # TT role requires position; skip invalid users explicitly.
                    continue
                out.append(
                    {
                        "auth_user_id": int(row["id"]),
                        "email": str(row["email"]),
                        "display_name": row["display_name"],
                        "picture": row["picture"],
                        "role": str(row["time_tracking_role"]),
                        "position": pos,
                        "is_blocked": bool(row["is_blocked"]),
                        "is_archived": bool(row["is_archived"]),
                    }
                )
            return out
    finally:
        await engine.dispose()


async def _run(*, auth_db_url: str, dry_run: bool, delete_missing: bool) -> int:
    if not auth_db_url.strip():
        print("Не задан auth DB URL: передайте --auth-db-url или env AUTH_DATABASE_URL", file=sys.stderr)
        return 1

    auth_users = await _load_auth_users(auth_db_url)
    auth_ids = {u["auth_user_id"] for u in auth_users}
    print(f"Найдено пользователей в auth с TT role (и непустой должностью): {len(auth_users)}")

    async with async_session_factory() as tt_session:
        existing_rows = (await tt_session.execute(select(TimeTrackingUserModel))).scalars().all()
        existing_by_auth = {int(r.auth_user_id): r for r in existing_rows}
        existing_ids = set(existing_by_auth.keys())

        to_insert = [u for u in auth_users if u["auth_user_id"] not in existing_ids]
        to_update = [u for u in auth_users if u["auth_user_id"] in existing_ids]
        to_delete_ids = sorted(existing_ids - auth_ids) if delete_missing else []

        print(f"Будет создано: {len(to_insert)}")
        print(f"Будет обновлено: {len(to_update)}")
        print(f"Будет удалено (delete-missing): {len(to_delete_ids)}")

        if dry_run:
            print("[dry-run] Без изменений.")
            return 0

        now = _now_utc()
        for u in to_insert:
            tt_session.add(
                TimeTrackingUserModel(
                    auth_user_id=u["auth_user_id"],
                    email=u["email"],
                    display_name=u["display_name"],
                    picture=u["picture"],
                    position=u["position"],
                    role=u["role"],
                    is_blocked=u["is_blocked"],
                    is_archived=u["is_archived"],
                    created_at=now,
                    updated_at=None,
                )
            )

        for u in to_update:
            row = existing_by_auth[u["auth_user_id"]]
            row.email = u["email"]
            row.display_name = u["display_name"]
            row.picture = u["picture"]
            row.position = u["position"]
            row.role = u["role"]
            row.is_blocked = u["is_blocked"]
            row.is_archived = u["is_archived"]
            row.updated_at = now
            tt_session.add(row)

        if to_delete_ids:
            await tt_session.execute(
                delete(TimeTrackingUserModel).where(TimeTrackingUserModel.auth_user_id.in_(to_delete_ids))
            )

        await tt_session.commit()
        print("Готово: пользователи TT восстановлены.")
        return 0


def main() -> int:
    args = _parse_args()
    return asyncio.run(
        _run(
            auth_db_url=args.auth_db_url,
            dry_run=not args.execute,
            delete_missing=bool(args.delete_missing),
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())

