from __future__ import annotations

import pytest

from application.project_partner_users import list_partner_auth_user_ids_by_projects


@pytest.mark.asyncio
async def test_list_partner_auth_user_ids_by_projects_empty():
    class _Access:
        async def list_access_by_project_ids(self, _pids):
            return {}

    out = await list_partner_auth_user_ids_by_projects(
        session=None,  # type: ignore[arg-type]
        access_repo=_Access(),  # type: ignore[arg-type]
        project_ids=[],
        authorization=None,
    )
    assert out == {}
