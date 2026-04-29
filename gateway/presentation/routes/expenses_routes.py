

import logging
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response

from infrastructure.auth_upstream import access_token_from_request, verify_bearer_and_get_user
from infrastructure.config import get_settings
from presentation.routes.users import require_main_admin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["expenses"])


async def get_current_user(
    request: Request,
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    return await verify_bearer_and_get_user(request, authorization)


def _base() -> str:
    settings = get_settings()
    base = (settings.expenses_service_url or "").rstrip("/")
    if not base:
        raise HTTPException(status_code=503, detail="Expenses service not configured")
    return base


def _strip_hop(headers: dict) -> dict:
    skip = {
        "connection",
        "keep-alive",
        "transfer-encoding",
        "content-encoding",
        "host",
    }
    return {k: v for k, v in headers.items() if k.lower() not in skip}


async def _forward(
    request: Request,
    upstream_path: str,
    authorization: Optional[str],
    timeout: float = 120.0,
) -> Response:
    url = f"{_base()}/{upstream_path.lstrip('/')}"
    if request.url.query:
        url = f"{url}?{request.url.query}"
    body = await request.body()
    headers = _strip_hop(dict(request.headers))
    headers.pop("host", None)
    tok = access_token_from_request(request, authorization)
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.request(
                method=request.method,
                url=url,
                headers=headers,
                content=body,
            )
    except httpx.RequestError as e:
        logger.warning(
            "expenses upstream request failed: url=%s err=%s",
            url,
            e,
        )
        raise HTTPException(
            status_code=503,
            detail=(
                "Expenses service unavailable. "
                "Deploy the expenses container on the same Docker network as gateway; "
                "EXPENSES_SERVICE_URL=http://expenses:1242 (see .env.example)."
            ),
        )
    resp_headers = {k: v for k, v in r.headers.items() if k.lower() not in ("connection", "transfer-encoding")}
    return Response(content=r.content, status_code=r.status_code, headers=resp_headers)


@router.post("/admin/expenses-database/reset")
async def proxy_expenses_database_reset(
    request: Request,
    authorization: Optional[str] = Header(None, alias="Authorization"),
    _: dict = Depends(require_main_admin),
):

    return await _forward(request, "admin/expenses-database/reset", authorization, timeout=300.0)


@router.get("/expenses/{expense_id}/email-action")
async def proxy_expense_email_action_public(
    expense_id: str,
    request: Request,
):

    return await _forward(request, f"expenses/{expense_id}/email-action", None, timeout=60.0)


@router.get("/expenses/{expense_id}/attachments/{attachment_id}/email-file")
async def proxy_expense_attachment_email_file(
    expense_id: str,
    attachment_id: str,
    request: Request,
):

    return await _forward(
        request,
        f"expenses/{expense_id}/attachments/{attachment_id}/email-file",
        None,
        timeout=120.0,
    )


@router.api_route("/expenses", methods=["GET", "POST"])
async def proxy_expenses_root(
    request: Request,
    authorization: Optional[str] = Header(None, alias="Authorization"),
    _: dict = Depends(get_current_user),
):
    return await _forward(request, "expenses", authorization, timeout=60.0)


@router.api_route("/expenses/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def proxy_expenses_subpath(
    path: str,
    request: Request,
    authorization: Optional[str] = Header(None, alias="Authorization"),
    _: dict = Depends(get_current_user),
):
    return await _forward(request, f"expenses/{path}", authorization, timeout=120.0)


@router.get("/expense-types")
async def proxy_expense_types(
    request: Request,
    authorization: Optional[str] = Header(None, alias="Authorization"),
    _: dict = Depends(get_current_user),
):
    return await _forward(request, "expense-types", authorization, timeout=30.0)


@router.get("/departments")
async def proxy_departments(
    request: Request,
    authorization: Optional[str] = Header(None, alias="Authorization"),
    _: dict = Depends(get_current_user),
):
    return await _forward(request, "departments", authorization, timeout=30.0)


@router.get("/projects")
async def proxy_projects(
    request: Request,
    authorization: Optional[str] = Header(None, alias="Authorization"),
    _: dict = Depends(get_current_user),
):

    settings = get_settings()
    tt_base = (settings.time_tracking_service_url or "").rstrip("/")
    if tt_base:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.get(f"{tt_base}/projects-for-expenses")
            if r.status_code == 200:
                return Response(content=r.content, status_code=200, headers={"Content-Type": "application/json"})
        except httpx.RequestError:
            pass
    return await _forward(request, "projects", authorization, timeout=30.0)


@router.get("/exchange-rates")
async def proxy_exchange_rates(
    request: Request,
    authorization: Optional[str] = Header(None, alias="Authorization"),
    _: dict = Depends(get_current_user),
):
    return await _forward(request, "exchange-rates", authorization, timeout=30.0)
