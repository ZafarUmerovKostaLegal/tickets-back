"""Server-side auto-filing: new mail of enrolled mailboxes is filed into matters by mail rules.

Every few minutes each enrolled mailbox is asked (Graph delta) for messages that arrived since the
previous run — Inbox is matched by sender, Sent Items by recipients. A matching message is filed the
same way the Outlook add-in files it: same folders (Correspondence/Emails/<person>), same duplicate
detection, same «already filed» marks in Outlook.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from application.mail_rules import MailFacts, message_file_name, pick_rule, subject_phrases
from infrastructure.config import get_settings
from infrastructure.database import async_session_factory
from infrastructure.graph_mail import GraphError, GraphMessage, download_mime, mark_saved, read_delta
from infrastructure.models import AclModel, EmailFilingModel, FolderModel, MailboxModel, MailRuleModel, MatterWorkspaceModel
from infrastructure.tt_directory import Matter
from presentation.common import next_doc_number, now
from presentation.deps import Viewer
from presentation.intake import file_into_matter
from presentation.routes.mail import mail_target_folder, new_email_filing, normalize_message_id
from presentation.versioning import backfill_fingerprints, digest, store_content

_log = logging.getLogger(__name__)
_run_lock = asyncio.Lock()
_task: asyncio.Task | None = None


def rule_label(rule: MailRuleModel) -> str:
    parts = [rule.match] if rule.match else []
    if subject_phrases(rule.subject):
        parts.append(f"тема: {rule.subject}")
    return " · ".join(parts) or "—"


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


async def _emails_folder(session: AsyncSession, matter_id: str) -> FolderModel | None:
    """Correspondence/Emails of the matter template (None → matter root)."""
    folders = list(await session.scalars(select(FolderModel).where(
        FolderModel.matter_id == matter_id,
        FolderModel.deleted_at.is_(None),
        func.lower(FolderModel.name).in_(["correspondence", "emails"]),
    )))
    by_id = {folder.id: folder for folder in folders}
    for folder in folders:
        parent = by_id.get(folder.parent_id or "")
        if folder.name.strip().lower() == "emails" and parent is not None and parent.name.strip().lower() == "correspondence":
            return folder
    return None


async def _target_folder(session: AsyncSession, rule: MailRuleModel) -> FolderModel | None:
    if rule.folder_id:
        folder = await session.get(FolderModel, rule.folder_id)
        if folder is not None and folder.matter_id == rule.matter_id and folder.deleted_at is None:
            return folder
    return await _emails_folder(session, rule.matter_id)


async def _denied(session: AsyncSession, matter_id: str, user_id: int) -> bool:
    acl = await session.get(AclModel, (matter_id, user_id))
    return acl is not None and acl.level == "deny"


async def _already_filed(session: AsyncSession, matter_id: str, message: GraphMessage) -> bool:
    message_id = normalize_message_id(message.internet_message_id)
    if not message_id:
        return False
    return await session.scalar(select(EmailFilingModel.id).where(
        EmailFilingModel.matter_id == matter_id,
        EmailFilingModel.message_id == message_id,
        EmailFilingModel.kind == "email",
    ).limit(1)) is not None


async def file_message(
    session: AsyncSession,
    box: MailboxModel,
    rule: MailRuleModel,
    message: GraphMessage,
    content: bytes,
) -> str:
    """File one message by one rule. Returns the intake status (created / exists / …)."""
    workspace = await session.get(MatterWorkspaceModel, rule.matter_id)
    if workspace is None:
        return "no-matter"
    viewer = Viewer(box.user_id, box.user_name or box.email, False, False, "", email=box.email)
    matter = Matter(rule.matter_id, rule.client_id or workspace.client_id, rule.matter_name, rule.matter_code, False)
    folder = await mail_target_folder(session, viewer, matter, await _target_folder(session, rule))
    await backfill_fingerprints(session, matter.id, mail_only=True)
    stamp = now()
    first = await next_doc_number(session)
    numbers = iter(range(first, first + 10))

    async def next_number() -> int:
        return next(numbers)

    async def store() -> None:
        await store_content(content)

    status, doc = await file_into_matter(
        session, viewer, matter,
        folder_id=folder.id if folder else None,
        name=message_file_name(message.subject, message.sender, message.received_at),
        content_type="message/rfc822",
        size=len(content),
        sha=digest(content),
        head=content[:65536],
        comment=f"Автосохранение по правилу «{rule_label(rule)}»",
        allow_duplicate=False,
        stamp=stamp,
        next_number=next_number,
        store=store,
        is_mail=True,
        kind="email",
        mail_message_id=message.internet_message_id,
        mail_conversation_id=message.conversation_id,
    )
    if status in ("created", "version"):
        subject = " ".join(message.subject.split())[:500]
        if subject and (status == "version" or not (doc.description or "").strip()):
            doc.description = subject
        session.add(new_email_filing(
            viewer, doc,
            message_id=message.internet_message_id,
            conversation_id=message.conversation_id,
            from_email=message.sender,
            subject=message.subject,
            kind="email",
            stamp=stamp,
        ))
        rule.hits = int(rule.hits or 0) + 1
        rule.last_hit_at = stamp
        box.filed_total = int(box.filed_total or 0) + 1
        box.last_filed_at = stamp
    await session.commit()
    return status


async def process_mailbox(
    session: AsyncSession,
    box: MailboxModel,
    rules: list[MailRuleModel],
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> int:
    """Pull new mail of one mailbox and file what the rules say. Returns how many were filed."""
    filed = 0
    limit = get_settings().max_file_bytes
    for direction, attr in (("in", "inbox_delta"), ("out", "sent_delta")):
        page = await read_delta(box.email, direction, getattr(box, attr) or "", _aware(box.since), transport=transport)
        for message in page.messages:
            if message.received_at is not None and _aware(message.received_at) < _aware(box.since):
                continue
            facts = MailFacts(direction, message.sender, message.recipients, message.received_at, message.subject)
            rule = pick_rule(rules, facts, box.user_id)
            if rule is None:
                continue
            if await _denied(session, rule.matter_id, box.user_id) or await _already_filed(session, rule.matter_id, message):
                continue
            content = await download_mime(box.email, message.id, transport=transport)
            if not content or len(content) > limit:
                continue
            status = await file_message(session, box, rule, message, content)
            if status in ("created", "version"):
                filed += 1
            if status in ("created", "version", "exists", "unchanged"):
                await mark_saved(box.email, message, transport=transport)
        setattr(box, attr, page.delta_link)
        await session.commit()
    return filed


async def run_once(*, transport: httpx.AsyncBaseTransport | None = None) -> dict:
    """One pass over every enrolled mailbox (runs are never parallel)."""
    settings = get_settings()
    if not settings.mail_autofile_enabled:
        return {"skipped": "disabled"}
    if not settings.graph_ready() and transport is None:
        return {"skipped": "graph-not-configured"}
    async with _run_lock:
        summary = {"mailboxes": 0, "filed": 0, "errors": 0}
        async with async_session_factory() as session:
            rules = list(await session.scalars(select(MailRuleModel).where(MailRuleModel.enabled.is_(True))))
            boxes = list(await session.scalars(select(MailboxModel).where(MailboxModel.enabled.is_(True))))
            for box in boxes:
                summary["mailboxes"] += 1
                user_id = box.user_id
                error = ""
                try:
                    summary["filed"] += await process_mailbox(session, box, rules, transport=transport)
                except GraphError as exc:
                    error = str(exc)
                except Exception as exc:  # noqa: BLE001 — one mailbox must never stop the others
                    _log.exception("auto-filing failed for user %s", user_id)
                    error = f"Ошибка автосохранения: {exc}"
                if error:
                    await session.rollback()
                    summary["errors"] += 1
                    # rollback expired every loaded row: the next mailbox needs fresh rules.
                    rules = list(await session.scalars(select(MailRuleModel).where(MailRuleModel.enabled.is_(True))))
                box = await session.get(MailboxModel, user_id)
                if box is None:
                    continue
                box.last_error = error[:500]
                box.last_run_at = now()
                await session.commit()
        return summary


async def _loop() -> None:
    interval = max(60, int(get_settings().mail_autofile_interval_sec))
    await asyncio.sleep(30)
    while True:
        try:
            await run_once()
        except Exception:  # noqa: BLE001
            _log.exception("mail auto-filing pass failed")
        await asyncio.sleep(interval)


def start_autofile_loop() -> None:
    global _task
    settings = get_settings()
    if not settings.mail_autofile_enabled or not settings.graph_ready():
        _log.info("mail auto-filing is off (FILE_MANAGER_MAIL_AUTOFILE or Graph credentials missing)")
        return
    if _task is None or _task.done():
        _task = asyncio.create_task(_loop(), name="mail-autofile")


async def stop_autofile_loop() -> None:
    global _task
    if _task is not None:
        _task.cancel()
        try:
            await _task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        _task = None

