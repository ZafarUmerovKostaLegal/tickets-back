"""Microsoft Graph for server-side mail auto-filing (application permission Mail.Read + admin consent).

Only *new* messages are read: a delta query per folder, started at the moment the owner turned
auto-filing on, so the existing mailbox is never scanned.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

import httpx

from infrastructure.config import get_settings

GRAPH = "https://graph.microsoft.com/v1.0"
TOKEN_URL = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
FOLDERS = {"in": "inbox", "out": "sentitems"}
_SELECT = "id,internetMessageId,conversationId,subject,from,toRecipients,ccRecipients,receivedDateTime,isDraft,categories"
SAVED_CATEGORY = "Saved to Kosta"

_token: dict[str, Any] = {"value": "", "expires": datetime.min.replace(tzinfo=timezone.utc)}
_token_lock = asyncio.Lock()


class GraphError(Exception):
    """Graph refused or failed; the message says why (shown to the mailbox owner)."""


@dataclass
class GraphMessage:
    id: str
    internet_message_id: str
    conversation_id: str
    subject: str
    sender: str
    recipients: tuple[str, ...]
    received_at: datetime | None
    categories: tuple[str, ...] = ()


@dataclass
class DeltaPage:
    messages: list[GraphMessage] = field(default_factory=list)
    delta_link: str = ""


def _client(transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=60.0, transport=transport)


async def app_token(transport: httpx.AsyncBaseTransport | None = None) -> str:
    settings = get_settings()
    if not settings.graph_ready():
        raise GraphError("Не заданы AZURE_TENANT_ID / AZURE_CLIENT_ID / AZURE_CLIENT_SECRET")
    async with _token_lock:
        now = datetime.now(timezone.utc)
        if _token["value"] and _token["expires"] - timedelta(minutes=5) > now:
            return str(_token["value"])
        async with _client(transport) as client:
            response = await client.post(
                TOKEN_URL.format(tenant=settings.graph_tenant_id.strip()),
                data={
                    "client_id": settings.graph_client_id.strip(),
                    "client_secret": settings.graph_client_secret.strip(),
                    "scope": "https://graph.microsoft.com/.default",
                    "grant_type": "client_credentials",
                },
            )
        if response.status_code >= 400:
            raise GraphError(f"Microsoft не выдал токен ({response.status_code})")
        body = response.json()
        _token["value"] = body["access_token"]
        _token["expires"] = now + timedelta(seconds=int(body.get("expires_in", 3600)))
        return str(_token["value"])


def reset_token_cache() -> None:
    _token["value"] = ""
    _token["expires"] = datetime.min.replace(tzinfo=timezone.utc)


def _explain(response: httpx.Response) -> str:
    if response.status_code == 403:
        return "Нет разрешения Mail.Read для приложения Kosta в Microsoft 365 — нужно согласие администратора"
    if response.status_code == 404:
        return "Почтовый ящик не найден в Microsoft 365"
    try:
        detail = response.json().get("error", {}).get("message", "")
    except ValueError:
        detail = ""
    return f"Microsoft Graph {response.status_code}: {detail}"[:300]


def _address(raw: Any) -> str:
    return str(((raw or {}).get("emailAddress") or {}).get("address") or "").strip().lower()


def _parse(row: dict) -> GraphMessage | None:
    if row.get("@removed") or row.get("isDraft"):
        return None
    received = None
    raw = row.get("receivedDateTime")
    if isinstance(raw, str) and raw:
        try:
            received = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            received = None
    recipients = tuple(
        addr for addr in (_address(r) for r in [*(row.get("toRecipients") or []), *(row.get("ccRecipients") or [])]) if addr
    )
    return GraphMessage(
        id=str(row.get("id") or ""),
        internet_message_id=str(row.get("internetMessageId") or "").strip(),
        conversation_id=str(row.get("conversationId") or "").strip(),
        subject=str(row.get("subject") or ""),
        sender=_address(row.get("from")),
        recipients=recipients,
        received_at=received,
        categories=tuple(str(item) for item in (row.get("categories") or []) if item),
    )


async def read_delta(
    mailbox: str,
    direction: str,
    delta_link: str,
    since: datetime,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    max_pages: int = 20,
) -> DeltaPage:
    """New messages of Inbox ("in") or Sent Items ("out") since the previous call."""
    token = await app_token(transport)
    if delta_link:
        url = delta_link
    else:
        stamp = since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        url = (
            f"{GRAPH}/users/{quote(mailbox)}/mailFolders/{FOLDERS[direction]}/messages/delta"
            f"?$select={_SELECT}&$filter=receivedDateTime+ge+{stamp}"
        )
    page = DeltaPage()
    headers = {"Authorization": f"Bearer {token}", "Prefer": "odata.maxpagesize=50"}
    async with _client(transport) as client:
        for _ in range(max_pages):
            response = await client.get(url, headers=headers)
            if response.status_code == 410:
                # The delta token expired: start over from `since` — duplicates are skipped later.
                return await read_delta(mailbox, direction, "", since, transport=transport, max_pages=max_pages)
            if response.status_code >= 400:
                raise GraphError(_explain(response))
            body = response.json()
            for row in body.get("value") or []:
                message = _parse(row)
                if message and message.id:
                    page.messages.append(message)
            next_link = body.get("@odata.nextLink")
            if next_link:
                url = next_link
                continue
            page.delta_link = str(body.get("@odata.deltaLink") or "")
            return page
    # Huge backlog: keep what we have and continue from the next page on the following run.
    page.delta_link = url
    return page


def _people(rows: Any) -> list[dict[str, str]]:
    out = []
    for row in rows or []:
        address = ((row or {}).get("emailAddress") or {})
        out.append({"email": str(address.get("address") or ""), "name": str(address.get("name") or "")})
    return out


async def read_conversation(
    mailbox: str,
    conversation_id: str,
    internet_message_id: str = "",
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """
    Every message of one conversation in this mailbox with its HTML body, oldest first.
    The conversation is found by its id or, failing that, through the message's Internet-Message-ID.
    """
    token = await app_token(transport)
    headers = {
        "Authorization": f"Bearer {token}",
        "Prefer": 'outlook.body-content-type="html"',
    }
    select = "id,internetMessageId,conversationId,subject,from,toRecipients,ccRecipients,receivedDateTime,body,isDraft"
    base = f"{GRAPH}/users/{quote(mailbox)}/messages"

    async def fetch(filter_expr: str, fields: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        url = f"{base}?$filter={quote(filter_expr)}&$select={fields}&$top=50"
        async with _client(transport) as client:
            while url and len(rows) < limit:
                response = await client.get(url, headers=headers)
                if response.status_code >= 400:
                    raise GraphError(_explain(response))
                body = response.json()
                rows.extend(body.get("value") or [])
                url = str(body.get("@odata.nextLink") or "")
        return rows

    def quoted(value: str) -> str:
        return value.replace("'", "''")

    rows = await fetch(f"conversationId eq '{quoted(conversation_id)}'", select) if conversation_id.strip() else []
    if not rows and internet_message_id.strip():
        mid = internet_message_id.strip()
        if not mid.startswith("<"):
            mid = f"<{mid}>"
        found = await fetch(f"internetMessageId eq '{quoted(mid)}'", "id,conversationId")
        real = str((found[0] if found else {}).get("conversationId") or "")
        if real and real != conversation_id:
            rows = await fetch(f"conversationId eq '{quoted(real)}'", select)
    messages = []
    for row in rows:
        if row.get("isDraft"):
            continue
        sender = ((row.get("from") or {}).get("emailAddress") or {})
        body = row.get("body") or {}
        messages.append({
            "graphId": str(row.get("id") or ""),
            "internetMessageId": str(row.get("internetMessageId") or ""),
            "conversationId": str(row.get("conversationId") or ""),
            "subject": str(row.get("subject") or ""),
            "fromEmail": str(sender.get("address") or ""),
            "fromName": str(sender.get("name") or ""),
            "received": str(row.get("receivedDateTime") or ""),
            "to": _people(row.get("toRecipients")),
            "cc": _people(row.get("ccRecipients")),
            "bodyType": str(body.get("contentType") or "html").lower(),
            "body": str(body.get("content") or ""),
        })
    messages.sort(key=lambda item: item["received"])
    return messages


async def mark_saved(
    mailbox: str,
    message: GraphMessage,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> bool:
    """
    Put the "Saved to Kosta" category on the message, like the Outlook add-in does.
    Needs the Mail.ReadWrite application permission; without it the badge is simply skipped.
    """
    if SAVED_CATEGORY in message.categories:
        return True
    try:
        token = await app_token(transport)
        async with _client(transport) as client:
            response = await client.patch(
                f"{GRAPH}/users/{quote(mailbox)}/messages/{quote(message.id)}",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={"categories": [*message.categories, SAVED_CATEGORY]},
            )
    except (GraphError, httpx.HTTPError):
        return False
    return response.status_code < 400


async def download_mime(mailbox: str, message_id: str, *, transport: httpx.AsyncBaseTransport | None = None) -> bytes:
    """The original message (.eml, attachments included)."""
    token = await app_token(transport)
    async with _client(transport) as client:
        response = await client.get(
            f"{GRAPH}/users/{quote(mailbox)}/messages/{quote(message_id)}/$value",
            headers={"Authorization": f"Bearer {token}"},
        )
    if response.status_code >= 400:
        raise GraphError(_explain(response))
    return response.content
