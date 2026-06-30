from __future__ import annotations

from fastapi import HTTPException


def dedupe_member_ids(member_auth_user_ids: list[int]) -> list[int]:
    seen: set[int] = set()
    out: list[int] = []
    for raw in member_auth_user_ids:
        uid = int(raw)
        if uid in seen:
            raise HTTPException(
                status_code=400,
                detail="Участники команды не должны дублироваться",
            )
        seen.add(uid)
        out.append(uid)
    return out
