from __future__ import annotations

import io
import time
import re
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from urllib.parse import quote
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer
from starlette.concurrency import run_in_threadpool

from backend_common.blob_store import read_link, sign_link

from infrastructure import blobs
from infrastructure.database import get_session
from infrastructure.models import (
    LegalHubDeletedFileModel,
    LegalHubDriveAclModel,
    LegalHubDriveFileModel,
    LegalHubDriveFolderModel,
)
from presentation.deps import require_user

router = APIRouter(tags=["legal-hub"])
# One request carries files up to this size; bigger ones go through /drive/uploads (chunked, up to 2 GB).
MAX_FILE_BYTES = 40 * 1024 * 1024
LINK_TTL_SECONDS = 300
MAX_BULK_BYTES = 300 * 1024 * 1024
MAX_BATCH = 500
SEARCH_LIMIT = 300
ROOT_ACL_ID = "__root__"
ACL_LEVELS = frozenset({"read", "edit", "manage", "deny"})
LEVEL_RANK = {"deny": 0, "read": 1, "edit": 2, "manage": 3}
_BAD_NAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_SUFFIX = re.compile(r"^(.*?)(?: \((\d+)\))?$")


class FolderIn(BaseModel):
    name: str = Field(min_length=1, max_length=1000)
    parentId: str | None = None

    @field_validator("name")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()


class ItemPatchIn(BaseModel):
    name: str | None = Field(default=None, max_length=300)
    hidden: bool | None = None


class SelectionIn(BaseModel):
    folderIds: list[str] = Field(default_factory=list, max_length=MAX_BATCH)
    fileIds: list[str] = Field(default_factory=list, max_length=MAX_BATCH)


class TransferIn(SelectionIn):
    targetId: str | None = None


class HideIn(SelectionIn):
    hidden: bool


class AclIn(BaseModel):
    userId: int
    userName: str = Field(default="", max_length=200)
    level: str = Field(min_length=1, max_length=20)
    note: str = Field(default="", max_length=500)

    @field_validator("level")
    @classmethod
    def _level(cls, value: str) -> str:
        level = value.strip().lower()
        if level not in ACL_LEVELS:
            raise ValueError("Invalid ACL level")
        return level

    @field_validator("userName", "note")
    @classmethod
    def _trim(cls, value: str) -> str:
        return value.strip()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _user_id(user: dict) -> int:
    return int(user["id"])


def _user_name(user: dict) -> str:
    return str(user.get("name") or user.get("display_name") or user.get("email") or "").strip() or f"User {_user_id(user)}"


def _is_manager(user: dict) -> bool:
    role = str(user.get("role") or "").strip().lower()
    return "администратор" in role or "partner" in role or "партнёр" in role or "партнер" in role


def _acl_folder_id(folder_id: str | None) -> str:
    return (folder_id or "").strip() or ROOT_ACL_ID


def _clean_id(value: str | None) -> str | None:
    return (value or "").strip() or None


def _acl_out(row: LegalHubDriveAclModel) -> dict:
    return {
        "folderId": None if row.folder_id == ROOT_ACL_ID else row.folder_id,
        "userId": row.user_id,
        "userName": row.user_name,
        "level": row.level,
        "note": row.note,
        "grantedBy": row.granted_by_name,
        "grantedAt": row.granted_at.isoformat() if row.granted_at else None,
    }


def _file_kind(name: str) -> str:
    lowered = name.lower()
    if lowered.endswith(".pdf"):
        return "pdf"
    if lowered.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".heic")):
        return "image"
    if lowered.endswith((".doc", ".docx", ".rtf", ".odt")):
        return "doc"
    if lowered.endswith((".xls", ".xlsx", ".csv", ".ods")):
        return "sheet"
    if lowered.endswith((".ppt", ".pptx", ".odp")):
        return "slides"
    if lowered.endswith((".msg", ".eml")):
        return "email"
    if lowered.endswith((".zip", ".rar", ".7z", ".tar", ".gz")):
        return "archive"
    if lowered.endswith((".txt", ".md", ".json", ".xml", ".log")):
        return "text"
    return "file"


def _file_size(size: int) -> str:
    if size < 1024 * 1024:
        return f"{max(1, round(size / 1024))} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def clean_name(raw: str | None) -> str:
    """Windows-style item name: no path separators or reserved characters, no trailing dots / spaces."""
    name = (raw or "").strip().rstrip(". ").strip()
    if not name or name in {".", ".."}:
        raise HTTPException(status_code=400, detail="Укажите название")
    if _BAD_NAME.search(name):
        raise HTTPException(status_code=400, detail='Название не может содержать символы \\ / : * ? " < > |')
    if len(name) > 300:
        raise HTTPException(status_code=400, detail="Название длиннее 300 символов")
    return name


def split_path(raw: str | None) -> list[str]:
    """«A/B/C» or «A\\B\\C» → ['A', 'B', 'C'], each segment validated."""
    parts = [part for part in re.split(r"[\\/]+", raw or "") if part.strip()]
    if not parts:
        raise HTTPException(status_code=400, detail="Укажите название папки")
    if len(parts) > 32:
        raise HTTPException(status_code=400, detail="Слишком глубокая вложенность")
    return [clean_name(part) for part in parts]


def unique_name(name: str, taken: set[str], *, is_file: bool) -> str:
    """Windows-like auto-rename: «Отчёт.pdf» → «Отчёт (2).pdf», «Папка» → «Папка (2)»."""
    if name.casefold() not in taken:
        return name
    stem, ext = name, ""
    if is_file and not name.startswith(".") and "." in name:
        stem, ext = name.rsplit(".", 1)
        ext = f".{ext}"
    match = _SUFFIX.match(stem)
    base = (match.group(1) if match else "") or stem
    n = 2
    while True:
        candidate = f"{base} ({n}){ext}"
        if candidate.casefold() not in taken:
            return candidate
        n += 1


class DriveTree:
    """All folders + the user's ACL rows in memory: permissions, paths and subtree walks without N+1 queries."""

    def __init__(self, folders: list[LegalHubDriveFolderModel], acl: dict[str, str], *, manager: bool) -> None:
        self.folders = {folder.id: folder for folder in folders}
        self.children: dict[str | None, list[LegalHubDriveFolderModel]] = defaultdict(list)
        for folder in folders:
            parent = folder.parent_id if folder.parent_id in self.folders else None
            self.children[parent].append(folder)
        self.acl = acl
        self.manager = manager
        self._levels: dict[str | None, str] = {}

    def level(self, folder_id: str | None) -> str:
        """Nearest explicit ACL up the path wins; no ACL on the path → edit (shared storage)."""
        if self.manager:
            return "manage"
        chain: list[str | None] = []
        current = folder_id
        seen: set[str] = set()
        level = "edit"
        while True:
            if current in self._levels:
                level = self._levels[current]
                break
            chain.append(current)
            key = _acl_folder_id(current)
            if key in self.acl:
                level = self.acl[key] if self.acl[key] in LEVEL_RANK else "deny"
                break
            if current is None or current in seen:
                break
            seen.add(current)
            folder = self.folders.get(current)
            current = folder.parent_id if folder is not None and folder.parent_id in self.folders else None
        for item in chain:
            self._levels[item] = level
        return level

    def can(self, folder_id: str | None, need: str) -> bool:
        return LEVEL_RANK.get(self.level(folder_id), 0) >= LEVEL_RANK[need]

    def require(self, folder_id: str | None, need: str) -> None:
        if not self.can(folder_id, need):
            raise HTTPException(status_code=403, detail="Недостаточно прав для этой папки")

    def get(self, folder_id: str | None) -> LegalHubDriveFolderModel | None:
        return self.folders.get(folder_id) if folder_id else None

    def ancestors(self, folder_id: str | None) -> list[LegalHubDriveFolderModel]:
        """Folders from the root down to folder_id (inclusive)."""
        out: list[LegalHubDriveFolderModel] = []
        current = self.get(folder_id)
        seen: set[str] = set()
        while current is not None and current.id not in seen:
            seen.add(current.id)
            out.append(current)
            current = self.get(current.parent_id)
        out.reverse()
        return out

    def crumbs(self, folder_id: str | None) -> list[dict]:
        return [{"id": folder.id, "name": folder.name} for folder in self.ancestors(folder_id)]

    def is_within(self, folder_id: str | None, ancestor_id: str) -> bool:
        return any(folder.id == ancestor_id for folder in self.ancestors(folder_id))

    def walk(self, root_id: str | None, *, show_hidden: bool) -> list[LegalHubDriveFolderModel]:
        """Readable folders under root (root excluded), pruning denied / hidden branches."""
        out: list[LegalHubDriveFolderModel] = []
        stack = list(self.children.get(root_id, []))
        while stack:
            folder = stack.pop()
            if not self.can(folder.id, "read") or (folder.is_hidden and not show_hidden):
                continue
            out.append(folder)
            stack.extend(self.children.get(folder.id, []))
        return out


async def load_tree(session: AsyncSession, user: dict) -> DriveTree:
    folders = list(await session.scalars(select(LegalHubDriveFolderModel)))
    acl_rows = (await session.execute(
        select(LegalHubDriveAclModel.folder_id, LegalHubDriveAclModel.level)
        .where(LegalHubDriveAclModel.user_id == _user_id(user))
    )).all()
    return DriveTree(folders, {folder_id: level for folder_id, level in acl_rows}, manager=_is_manager(user))


def _folder_in(tree: DriveTree, folder_id: str | None) -> str | None:
    folder_id = _clean_id(folder_id)
    if folder_id and tree.get(folder_id) is None:
        raise HTTPException(status_code=404, detail="Папка не найдена")
    return folder_id


def _in_folder(column, parent: str | None):
    return column.is_(None) if parent is None else column == parent


async def _taken_names(session: AsyncSession, parent: str | None, *, exclude: set[str] | None = None) -> set[str]:
    exclude = exclude or set()
    rows = [
        *(await session.execute(
            select(LegalHubDriveFolderModel.id, LegalHubDriveFolderModel.name)
            .where(_in_folder(LegalHubDriveFolderModel.parent_id, parent))
        )).all(),
        *(await session.execute(
            select(LegalHubDriveFileModel.id, LegalHubDriveFileModel.name)
            .where(_in_folder(LegalHubDriveFileModel.folder_id, parent))
        )).all(),
    ]
    return {name.casefold() for item_id, name in rows if item_id not in exclude}


def _file_out(item: LegalHubDriveFileModel) -> dict:
    return {
        "id": item.id,
        "name": item.name,
        "kind": _file_kind(item.name),
        "size": _file_size(item.size_bytes),
        "sizeBytes": item.size_bytes,
        "createdAt": _iso(item.created_at),
        "updatedAt": _iso(item.updated_at or item.created_at),
        "hidden": bool(item.is_hidden),
        "folderId": item.folder_id,
    }


def _folder_out(item: LegalHubDriveFolderModel, count: int = 0) -> dict:
    return {
        "id": item.id,
        "name": item.name,
        "createdAt": _iso(item.created_at),
        "updatedAt": _iso(item.updated_at or item.created_at),
        "hidden": bool(item.is_hidden),
        "itemCount": count,
        "parentId": item.parent_id,
    }


async def _child_counts(session: AsyncSession, ids: list[str], *, show_hidden: bool) -> dict[str, int]:
    if not ids:
        return {}
    counts: dict[str, int] = defaultdict(int)
    folder_q = select(LegalHubDriveFolderModel.parent_id, func.count()).where(LegalHubDriveFolderModel.parent_id.in_(ids))
    file_q = select(LegalHubDriveFileModel.folder_id, func.count()).where(LegalHubDriveFileModel.folder_id.in_(ids))
    if not show_hidden:
        folder_q = folder_q.where(LegalHubDriveFolderModel.is_hidden.is_(False))
        file_q = file_q.where(LegalHubDriveFileModel.is_hidden.is_(False))
    for parent, count in (await session.execute(folder_q.group_by(LegalHubDriveFolderModel.parent_id))).all():
        counts[parent] += int(count)
    for parent, count in (await session.execute(file_q.group_by(LegalHubDriveFileModel.folder_id))).all():
        counts[parent] += int(count)
    return counts


async def _browse(session: AsyncSession, user: dict, parent: str | None, *, show_hidden: bool = False) -> dict:
    tree = await load_tree(session, user)
    parent = _folder_in(tree, parent)
    tree.require(parent, "read")
    level = tree.level(parent)
    folders = sorted(
        (
            folder for folder in tree.children.get(parent, [])
            if tree.can(folder.id, "read") and (show_hidden or not folder.is_hidden)
        ),
        key=lambda folder: folder.name.casefold(),
    )
    file_q = (
        select(LegalHubDriveFileModel)
        .options(defer(LegalHubDriveFileModel.content))
        .where(_in_folder(LegalHubDriveFileModel.folder_id, parent))
        .order_by(LegalHubDriveFileModel.name.asc())
    )
    if not show_hidden:
        file_q = file_q.where(LegalHubDriveFileModel.is_hidden.is_(False))
    files = list(await session.scalars(file_q))
    counts = await _child_counts(session, [folder.id for folder in folders], show_hidden=show_hidden)
    return {
        "path": tree.crumbs(parent),
        "folderId": parent,
        "level": level,
        "canEdit": LEVEL_RANK.get(level, 0) >= LEVEL_RANK["edit"],
        "canManage": LEVEL_RANK.get(level, 0) >= LEVEL_RANK["manage"],
        "folders": [_folder_out(folder, counts.get(folder.id, 0)) for folder in folders],
        "files": [_file_out(item) for item in files],
    }


async def _ensure_folder_path(
    session: AsyncSession,
    tree: DriveTree,
    parent: str | None,
    parts: list[str],
    user: dict,
    *,
    cache: dict[tuple[str | None, str], str] | None = None,
    unique_last: bool = False,
) -> str | None:
    """Walk / create «A/B/C» under parent, reusing existing folders (case-insensitive).
    With unique_last the last level is always new («Папка (2)» if the name is taken)."""
    cache = cache if cache is not None else {}
    current = parent
    now = _now()
    for index, part in enumerate(parts):
        fresh = unique_last and index == len(parts) - 1
        key = (current, part.casefold())
        if not fresh and key in cache:
            current = cache[key]
            continue
        existing = None if fresh else next(
            (folder for folder in tree.children.get(current, []) if folder.name.casefold() == part.casefold()),
            None,
        )
        if existing is not None:
            tree.require(existing.id, "edit")
            cache[key] = existing.id
            current = existing.id
            continue
        tree.require(current, "edit")
        name = unique_name(part, await _taken_names(session, current), is_file=False)
        folder = LegalHubDriveFolderModel(
            id=str(uuid4()),
            parent_id=current,
            name=name,
            is_hidden=False,
            created_by_user_id=_user_id(user),
            created_at=now,
            updated_at=now,
        )
        session.add(folder)
        await session.flush()
        tree.folders[folder.id] = folder
        tree.children[current].append(folder)
        cache[(current, name.casefold())] = folder.id
        current = folder.id
    return current


@router.get("/drive")
async def list_drive(
    parentId: str | None = Query(default=None),
    showHidden: bool = Query(default=False),
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(require_user),
):
    return await _browse(session, user, parentId, show_hidden=showHidden)


@router.get("/drive/tree")
async def drive_tree(
    showHidden: bool = Query(default=False),
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(require_user),
):
    """Folder hierarchy for the navigation pane: only branches the user can open."""
    tree = await load_tree(session, user)
    if not tree.can(None, "read"):
        return {"folders": []}
    return {
        "folders": [
            {"id": folder.id, "parentId": folder.parent_id, "name": folder.name, "hidden": bool(folder.is_hidden)}
            for folder in sorted(tree.walk(None, show_hidden=showHidden), key=lambda folder: folder.name.casefold())
        ],
    }


@router.get("/drive/search")
async def search_drive(
    q: str = Query(min_length=1, max_length=200),
    folderId: str | None = Query(default=None),
    showHidden: bool = Query(default=False),
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(require_user),
):
    """Search by name in the folder and all its subfolders (like the Explorer search box)."""
    tree = await load_tree(session, user)
    scope = _folder_in(tree, folderId)
    tree.require(scope, "read")
    words = [word.casefold() for word in q.split() if word.strip()]
    if not words:
        return {"query": q, "folders": [], "files": [], "truncated": False}

    def matches(name: str) -> bool:
        folded = name.casefold()
        return all(word in folded for word in words)

    folders = tree.walk(scope, show_hidden=showHidden)
    folder_hits = [folder for folder in folders if matches(folder.name)]
    readable = [folder.id for folder in folders]
    file_q = (
        select(LegalHubDriveFileModel)
        .options(defer(LegalHubDriveFileModel.content))
        .where(LegalHubDriveFileModel.name.ilike(f"%{words[0]}%"))
    )
    if scope is None:
        file_q = file_q.where(or_(
            LegalHubDriveFileModel.folder_id.is_(None),
            LegalHubDriveFileModel.folder_id.in_(readable or [""]),
        ))
    else:
        file_q = file_q.where(LegalHubDriveFileModel.folder_id.in_([scope, *readable]))
    if not showHidden:
        file_q = file_q.where(LegalHubDriveFileModel.is_hidden.is_(False))
    files = [item for item in await session.scalars(file_q.limit(SEARCH_LIMIT * 4)) if matches(item.name)]
    folder_hits.sort(key=lambda folder: folder.name.casefold())
    files.sort(key=lambda item: item.name.casefold())
    truncated = len(folder_hits) + len(files) > SEARCH_LIMIT
    folder_hits = folder_hits[:SEARCH_LIMIT]
    files = files[: max(0, SEARCH_LIMIT - len(folder_hits))]
    counts = await _child_counts(session, [folder.id for folder in folder_hits], show_hidden=showHidden)
    return {
        "query": q,
        "folders": [
            {**_folder_out(folder, counts.get(folder.id, 0)), "path": tree.crumbs(folder.parent_id)}
            for folder in folder_hits
        ],
        "files": [{**_file_out(item), "path": tree.crumbs(item.folder_id)} for item in files],
        "truncated": truncated,
    }


@router.post("/drive/folders", status_code=201)
async def create_folder(
    body: FolderIn,
    showHidden: bool = Query(default=False),
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(require_user),
):
    """Create a folder; «A/B/C» creates the whole structure at once (existing levels are reused)."""
    tree = await load_tree(session, user)
    parent = _folder_in(tree, body.parentId)
    tree.require(parent, "edit")
    created = await _ensure_folder_path(session, tree, parent, split_path(body.name), user, unique_last=True)
    await session.commit()
    result = await _browse(session, user, parent, show_hidden=showHidden)
    result["createdId"] = created
    return result


@router.post("/drive/files", status_code=201)
async def upload_files(
    files: list[UploadFile] = File(...),
    parentId: str | None = Form(default=None),
    paths: list[str] | None = Form(default=None),
    showHidden: bool = Query(default=False),
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(require_user),
):
    """Upload files; with `paths` (relative paths of a dropped / picked folder) the folder structure is recreated.
    Name clashes get «(2)» like in Explorer instead of overwriting."""
    tree = await load_tree(session, user)
    parent = _folder_in(tree, parentId)
    tree.require(parent, "edit")
    now = _now()
    stored = 0
    cache: dict[tuple[str | None, str], str] = {}
    taken_by_folder: dict[str | None, set[str]] = {}
    for index, upload in enumerate(files):
        content = await upload.read()
        if len(content) > MAX_FILE_BYTES:
            raise HTTPException(status_code=413, detail=f"Файл «{upload.filename}» больше 40 МБ — он будет загружен частями")
        relative = (paths[index] if paths and index < len(paths) else "") or ""
        segments = [part for part in re.split(r"[\\/]+", relative) if part.strip()]
        folder_parts = [clean_name(part) for part in segments[:-1]]
        target = await _ensure_folder_path(session, tree, parent, folder_parts, user, cache=cache) if folder_parts else parent
        if not content:
            continue
        if target not in taken_by_folder:
            taken_by_folder[target] = await _taken_names(session, target)
        try:
            raw_name = clean_name((upload.filename or "file").replace("\\", "/").rsplit("/", 1)[-1])
        except HTTPException:
            raw_name = "file"
        name = unique_name(raw_name, taken_by_folder[target], is_file=True)
        taken_by_folder[target].add(name.casefold())
        session.add(LegalHubDriveFileModel(
            id=str(uuid4()),
            folder_id=target,
            name=name,
            content_type=(upload.content_type or "application/octet-stream")[:120],
            size_bytes=len(content),
            content=b"",
            blob_sha=await run_in_threadpool(blobs.store.put_bytes, content),
            is_hidden=False,
            created_by_user_id=_user_id(user),
            created_at=now,
            updated_at=now,
        ))
        stored += 1
    if stored == 0 and not cache:
        raise HTTPException(status_code=400, detail="Выберите файл")
    await session.commit()
    return await _browse(session, user, parent, show_hidden=showHidden)


@router.get("/drive/files/{file_id}")
async def download_drive_file(
    file_id: str,
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(require_user),
):
    item = await session.get(LegalHubDriveFileModel, file_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Файл не найден")
    tree = await load_tree(session, user)
    tree.require(item.folder_id, "read")
    return _file_response(item)


def _file_response(item: LegalHubDriveFileModel, *, inline: bool = False) -> Response:
    headers = {
        "Content-Disposition": f"{'inline' if inline else 'attachment'}; filename*=UTF-8''{quote(item.name)}",
        "Cache-Control": "private, no-store",
    }
    media = item.content_type or "application/octet-stream"
    if item.blob_sha:
        path = blobs.store.blob_path(item.blob_sha)
        if not path.is_file():
            raise HTTPException(status_code=404, detail="Файл не найден в хранилище")
        return FileResponse(path, media_type=media, headers=headers)  # streamed, Range supported
    return Response(content=item.content, media_type=media, headers=headers)


@router.post("/drive/files/{file_id}/link")
async def drive_file_link(
    file_id: str,
    inline: bool = Query(default=False),
    entry: int | None = Query(default=None, ge=0),
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(require_user),
):
    """Short-lived signed URL: the browser downloads big files itself (to disk, resumable)."""
    item = await session.get(LegalHubDriveFileModel, file_id, options=[defer(LegalHubDriveFileModel.content)])
    if item is None:
        raise HTTPException(status_code=404, detail="Файл не найден")
    tree = await load_tree(session, user)
    tree.require(item.folder_id, "read")
    payload = {"f": item.id, "i": 1 if inline else 0, "u": _user_id(user), "exp": int(time.time()) + LINK_TTL_SECONDS}
    if entry is not None:
        payload["e"] = entry
    token = sign_link(blobs.link_secret(), payload)
    return {"url": f"/api/v1/legal-hub/drive/dl/{token}", "expiresIn": LINK_TTL_SECONDS}


@router.get("/drive/dl/{token}")
async def drive_signed_download(token: str, session: AsyncSession = Depends(get_session)):
    payload = read_link(blobs.link_secret(), token)
    item = await session.get(LegalHubDriveFileModel, str(payload.get("f", "")))
    if item is None:
        raise HTTPException(status_code=404, detail="Файл не найден")
    if payload.get("e") is not None:
        from presentation.routes.drive_preview import entry_response, opener
        return await entry_response(opener(item), int(payload["e"]), inline=bool(payload.get("i")))
    return _file_response(item, inline=bool(payload.get("i")))


async def archive_drive_file(
    session: AsyncSession,
    item: LegalHubDriveFileModel,
    user: dict,
    folder_path: str,
    stamp: datetime,
) -> None:
    """Keep a deleted Disk file (bytes or blob reference) in legal_hub_deleted_files, then drop the live row."""
    content = item.content if item.blob_sha is None else b""
    if item.blob_sha is None and not content:
        loaded = await session.scalar(select(LegalHubDriveFileModel.content).where(LegalHubDriveFileModel.id == item.id))
        content = loaded or b""
    session.add(LegalHubDeletedFileModel(
        id=str(uuid4()), source="drive", original_id=item.id, name=item.name, content_type=item.content_type,
        size_bytes=int(item.size_bytes or 0), content=content, blob_sha=item.blob_sha, folder_id=item.folder_id,
        folder_path=folder_path, task_id=None, created_by_user_id=int(item.created_by_user_id or 0),
        created_at=item.created_at, deleted_by_user_id=_user_id(user), deleted_by_name=_user_name(user),
        deleted_at=stamp,
    ))
    await session.delete(item)


async def _rename_or_hide(
    session: AsyncSession,
    tree: DriveTree,
    item: LegalHubDriveFolderModel | LegalHubDriveFileModel,
    parent: str | None,
    body: ItemPatchIn,
) -> None:
    tree.require(parent, "edit")
    if body.name is not None:
        name = clean_name(body.name)
        if name != item.name:
            if name.casefold() in await _taken_names(session, parent, exclude={item.id}):
                raise HTTPException(status_code=409, detail=f"В этой папке уже есть элемент «{name}»")
            item.name = name
    if body.hidden is not None:
        item.is_hidden = body.hidden
    item.updated_at = _now()


@router.patch("/drive/folders/{folder_id}")
async def patch_folder(
    folder_id: str,
    body: ItemPatchIn,
    showHidden: bool = Query(default=False),
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(require_user),
):
    """Rename and / or hide a folder."""
    tree = await load_tree(session, user)
    folder = tree.get(folder_id)
    if folder is None:
        raise HTTPException(status_code=404, detail="Папка не найдена")
    parent = folder.parent_id
    await _rename_or_hide(session, tree, folder, parent, body)
    await session.commit()
    return await _browse(session, user, parent, show_hidden=showHidden)


@router.patch("/drive/files/{file_id}")
async def patch_file(
    file_id: str,
    body: ItemPatchIn,
    showHidden: bool = Query(default=False),
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(require_user),
):
    """Rename and / or hide a file."""
    item = await session.get(LegalHubDriveFileModel, file_id, options=[defer(LegalHubDriveFileModel.content)])
    if item is None:
        raise HTTPException(status_code=404, detail="Файл не найден")
    parent = item.folder_id
    tree = await load_tree(session, user)
    await _rename_or_hide(session, tree, item, parent, body)
    await session.commit()
    return await _browse(session, user, parent, show_hidden=showHidden)


async def _selection(
    session: AsyncSession,
    tree: DriveTree,
    body: SelectionIn,
) -> tuple[list[LegalHubDriveFolderModel], list[LegalHubDriveFileModel]]:
    folder_ids = list(dict.fromkeys(item for item in (_clean_id(raw) for raw in body.folderIds) if item))
    file_ids = list(dict.fromkeys(item for item in (_clean_id(raw) for raw in body.fileIds) if item))
    if not folder_ids and not file_ids:
        raise HTTPException(status_code=400, detail="Ничего не выбрано")
    folders: list[LegalHubDriveFolderModel] = []
    for folder_id in folder_ids:
        folder = tree.get(folder_id)
        if folder is None:
            raise HTTPException(status_code=404, detail="Папка не найдена")
        folders.append(folder)
    files = list(await session.scalars(
        select(LegalHubDriveFileModel)
        .options(defer(LegalHubDriveFileModel.content))
        .where(LegalHubDriveFileModel.id.in_(file_ids or [""]))
    ))
    if len(files) != len(file_ids):
        raise HTTPException(status_code=404, detail="Файл не найден")
    # Items inside a selected folder travel with it.
    selected = {folder.id for folder in folders}
    folders = [folder for folder in folders if not any(a.id in selected for a in tree.ancestors(folder.parent_id))]
    files = [item for item in files if not any(a.id in selected for a in tree.ancestors(item.folder_id))]
    return folders, files


@router.post("/drive/move")
async def move_items(
    body: TransferIn,
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(require_user),
):
    """Move folders / files into another folder (drag-and-drop, cut + paste)."""
    tree = await load_tree(session, user)
    target = _folder_in(tree, body.targetId)
    tree.require(target, "edit")
    folders, files = await _selection(session, tree, body)
    taken = await _taken_names(session, target)
    now = _now()
    moved = 0
    for folder in folders:
        if folder.id == target or tree.is_within(target, folder.id):
            raise HTTPException(status_code=400, detail=f"Нельзя переместить папку «{folder.name}» в саму себя")
        tree.require(folder.parent_id, "edit")
        tree.require(folder.id, "edit")
        if folder.parent_id == target:
            continue
        folder.name = unique_name(folder.name, taken, is_file=False)
        taken.add(folder.name.casefold())
        folder.parent_id = target
        folder.updated_at = now
        moved += 1
    for item in files:
        tree.require(item.folder_id, "edit")
        if item.folder_id == target:
            continue
        item.name = unique_name(item.name, taken, is_file=True)
        taken.add(item.name.casefold())
        item.folder_id = target
        item.updated_at = now
        moved += 1
    await session.commit()
    return {"moved": moved, "targetId": target}


def _charge(budget: list[int], size: int) -> None:
    budget[0] += size
    if budget[0] > MAX_BULK_BYTES:
        raise HTTPException(status_code=400, detail="Слишком большой объём (больше 300 МБ) — разделите на части")


async def _copy_folder(
    session: AsyncSession,
    tree: DriveTree,
    source: LegalHubDriveFolderModel,
    target: str | None,
    name: str,
    user: dict,
    now: datetime,
    budget: list[int],
) -> int:
    copy = LegalHubDriveFolderModel(
        id=str(uuid4()),
        parent_id=target,
        name=name,
        is_hidden=source.is_hidden,
        created_by_user_id=_user_id(user),
        created_at=now,
        updated_at=now,
    )
    session.add(copy)
    count = 1
    files = list(await session.scalars(
        select(LegalHubDriveFileModel).where(LegalHubDriveFileModel.folder_id == source.id)
    ))
    for item in files:
        if not item.blob_sha:
            _charge(budget, item.size_bytes)
        session.add(LegalHubDriveFileModel(
            id=str(uuid4()),
            folder_id=copy.id,
            name=item.name,
            content_type=item.content_type,
            size_bytes=item.size_bytes,
            content=item.content,
            blob_sha=item.blob_sha,
            is_hidden=item.is_hidden,
            created_by_user_id=_user_id(user),
            created_at=now,
            updated_at=now,
        ))
        count += 1
    for child in list(tree.children.get(source.id, [])):
        if tree.can(child.id, "read"):
            count += await _copy_folder(session, tree, child, copy.id, child.name, user, now, budget)
    return count


@router.post("/drive/copy")
async def copy_items(
    body: TransferIn,
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(require_user),
):
    """Copy folders (with everything inside) and files into a folder (copy + paste, Ctrl + drag)."""
    tree = await load_tree(session, user)
    target = _folder_in(tree, body.targetId)
    tree.require(target, "edit")
    folders, files = await _selection(session, tree, body)
    taken = await _taken_names(session, target)
    now = _now()
    budget = [0]
    copied = 0
    for folder in folders:
        tree.require(folder.id, "read")
        if folder.id == target or tree.is_within(target, folder.id):
            raise HTTPException(status_code=400, detail=f"Нельзя скопировать папку «{folder.name}» в её же подпапку")
        name = unique_name(folder.name, taken, is_file=False)
        taken.add(name.casefold())
        copied += await _copy_folder(session, tree, folder, target, name, user, now, budget)
    for meta in files:
        tree.require(meta.folder_id, "read")
        content, blob_sha = (await session.execute(
            select(LegalHubDriveFileModel.content, LegalHubDriveFileModel.blob_sha).where(LegalHubDriveFileModel.id == meta.id)
        )).one()
        if not blob_sha:
            _charge(budget, meta.size_bytes)
        name = unique_name(meta.name, taken, is_file=True)
        taken.add(name.casefold())
        session.add(LegalHubDriveFileModel(
            id=str(uuid4()),
            folder_id=target,
            name=name,
            content_type=meta.content_type,
            size_bytes=meta.size_bytes,
            content=content,
            blob_sha=blob_sha,
            is_hidden=False,
            created_by_user_id=_user_id(user),
            created_at=now,
            updated_at=now,
        ))
        copied += 1
    await session.commit()
    return {"copied": copied, "targetId": target}


@router.post("/drive/hide")
async def hide_items(
    body: HideIn,
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(require_user),
):
    """Hide / show several items at once (Explorer «Hidden» attribute)."""
    tree = await load_tree(session, user)
    folders, files = await _selection(session, tree, body)
    now = _now()
    for folder in folders:
        tree.require(folder.parent_id, "edit")
        folder.is_hidden = body.hidden
        folder.updated_at = now
    for item in files:
        tree.require(item.folder_id, "edit")
        item.is_hidden = body.hidden
        item.updated_at = now
    await session.commit()
    return {"updated": len(folders) + len(files)}


async def _delete_folder_tree(
    session: AsyncSession,
    tree: DriveTree,
    folder_id: str,
    user: dict,
    stamp: datetime,
) -> None:
    children = list(await session.scalars(
        select(LegalHubDriveFolderModel).where(LegalHubDriveFolderModel.parent_id == folder_id)
    ))
    for child in children:
        await _delete_folder_tree(session, tree, child.id, user, stamp)
    files = list(await session.scalars(
        select(LegalHubDriveFileModel).where(LegalHubDriveFileModel.folder_id == folder_id)
    ))
    path = _folder_path_text(tree, folder_id)
    for item in files:
        await archive_drive_file(session, item, user, path, stamp)
    acls = list(await session.scalars(
        select(LegalHubDriveAclModel).where(LegalHubDriveAclModel.folder_id == folder_id)
    ))
    for row in acls:
        await session.delete(row)
    folder = await session.get(LegalHubDriveFolderModel, folder_id)
    if folder is not None:
        await session.delete(folder)


def _folder_path_text(tree: DriveTree, folder_id: str | None) -> str:
    return "/".join(folder.name for folder in tree.ancestors(folder_id))


@router.post("/drive/delete")
async def delete_items(
    body: SelectionIn,
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(require_user),
):
    """Delete several folders / files at once."""
    tree = await load_tree(session, user)
    folders, files = await _selection(session, tree, body)
    for folder in folders:
        tree.require(folder.id, "edit")
    for item in files:
        tree.require(item.folder_id, "edit")
    stamp = datetime.now(timezone.utc)
    for folder in folders:
        await _delete_folder_tree(session, tree, folder.id, user, stamp)
    for item in files:
        await archive_drive_file(session, item, user, _folder_path_text(tree, item.folder_id), stamp)
    await session.commit()
    return {"deleted": len(folders) + len(files)}


@router.post("/drive/zip")
async def zip_items(
    body: SelectionIn,
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(require_user),
):
    """Download several items / whole folders as one ZIP archive (folder structure kept)."""
    tree = await load_tree(session, user)
    folders, files = await _selection(session, tree, body)
    entries: list[tuple[str, str]] = []
    for item in files:
        tree.require(item.folder_id, "read")
        entries.append((item.id, item.name))
    for folder in folders:
        tree.require(folder.id, "read")
        skip = len(tree.ancestors(folder.parent_id))
        for sub in [folder, *tree.walk(folder.id, show_hidden=True)]:
            rel = "/".join(a.name for a in tree.ancestors(sub.id)[skip:])
            rows = (await session.execute(
                select(LegalHubDriveFileModel.id, LegalHubDriveFileModel.name)
                .where(LegalHubDriveFileModel.folder_id == sub.id)
            )).all()
            if not rows:
                entries.append(("", f"{rel}/"))
            entries.extend((file_id, f"{rel}/{name}") for file_id, name in rows)
    buffer = io.BytesIO()
    budget = [0]
    used: set[str] = set()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for file_id, arcname in entries:
            if not file_id:
                archive.writestr(arcname, b"")
                continue
            row = (await session.execute(
                select(LegalHubDriveFileModel.size_bytes, LegalHubDriveFileModel.content, LegalHubDriveFileModel.blob_sha)
                .where(LegalHubDriveFileModel.id == file_id)
            )).first()
            if row is None:
                continue
            _charge(budget, int(row[0] or 0))
            name, n = arcname, 2
            while name.casefold() in used:
                stem, dot, ext = arcname.rpartition(".")
                name = f"{stem} ({n}).{ext}" if dot else f"{arcname} ({n})"
                n += 1
            used.add(name.casefold())
            if row[2]:
                await run_in_threadpool(archive.write, blobs.store.blob_path(row[2]), name)
            else:
                archive.writestr(name, row[1])
    title = folders[0].name if len(folders) == 1 and not files else "Диск"
    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(title)}.zip"},
    )


@router.delete("/drive/files/{file_id}")
async def delete_drive_file(
    file_id: str,
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(require_user),
):
    item = await session.get(LegalHubDriveFileModel, file_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Файл не найден")
    parent = item.folder_id
    tree = await load_tree(session, user)
    tree.require(parent, "edit")
    await archive_drive_file(session, item, user, _folder_path_text(tree, parent), datetime.now(timezone.utc))
    await session.commit()
    return await _browse(session, user, parent)


@router.delete("/drive/folders/{folder_id}")
async def delete_folder(
    folder_id: str,
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(require_user),
):
    tree = await load_tree(session, user)
    folder = tree.get(folder_id)
    if folder is None:
        raise HTTPException(status_code=404, detail="Папка не найдена")
    parent = folder.parent_id
    tree.require(folder.id, "edit")
    await _delete_folder_tree(session, tree, folder.id, user, datetime.now(timezone.utc))
    await session.commit()
    return await _browse(session, user, parent)


@router.get("/drive/security")
async def get_drive_security(
    folderId: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(require_user),
):
    tree = await load_tree(session, user)
    folder = _folder_in(tree, folderId)
    tree.require(folder, "manage")
    key = _acl_folder_id(folder)
    rows = list(await session.scalars(
        select(LegalHubDriveAclModel)
        .where(LegalHubDriveAclModel.folder_id == key)
        .order_by(LegalHubDriveAclModel.user_name.asc())
    ))
    return {
        "folderId": folder,
        "acl": [_acl_out(row) for row in rows],
    }


@router.put("/drive/security")
async def set_drive_acl(
    body: AclIn,
    folderId: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(require_user),
):
    tree = await load_tree(session, user)
    folder = _folder_in(tree, folderId)
    tree.require(folder, "manage")
    key = _acl_folder_id(folder)
    now = _now()
    row = await session.get(LegalHubDriveAclModel, (key, body.userId))
    if row is None:
        row = LegalHubDriveAclModel(
            folder_id=key,
            user_id=body.userId,
            user_name=body.userName or f"User {body.userId}",
            level=body.level,
            note=body.note,
            granted_by_name=_user_name(user),
            granted_at=now,
        )
        session.add(row)
    else:
        row.user_name = body.userName or row.user_name
        row.level = body.level
        row.note = body.note
        row.granted_by_name = _user_name(user)
        row.granted_at = now
    await session.commit()
    return {"acl": _acl_out(row)}


@router.delete("/drive/security/{user_id}")
async def remove_drive_acl(
    user_id: int,
    folderId: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(require_user),
):
    tree = await load_tree(session, user)
    folder = _folder_in(tree, folderId)
    tree.require(folder, "manage")
    key = _acl_folder_id(folder)
    row = await session.get(LegalHubDriveAclModel, (key, user_id))
    if row is not None:
        await session.delete(row)
        await session.commit()
    return {"ok": True}


@router.get("/drive/deleted")
async def list_deleted_files(
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(require_user),
):
    """Everything ever deleted from Disk and task attachments — kept forever, managers can restore."""
    if not _is_manager(user):
        raise HTTPException(status_code=403, detail="Удалённые файлы видят администраторы и партнёры")
    rows = list(await session.scalars(
        select(LegalHubDeletedFileModel)
        .options(defer(LegalHubDeletedFileModel.content))
        .order_by(LegalHubDeletedFileModel.deleted_at.desc())
        .limit(2000)
    ))
    return {"items": [{
        "id": row.id,
        "source": row.source,
        "name": row.name,
        "sizeBytes": row.size_bytes,
        "contentType": row.content_type,
        "path": row.folder_path,
        "taskId": row.task_id,
        "deletedBy": row.deleted_by_name,
        "deletedAt": row.deleted_at.isoformat() if row.deleted_at else None,
        "createdAt": row.created_at.isoformat() if row.created_at else None,
    } for row in rows]}


@router.post("/drive/deleted/{deleted_id}/restore")
async def restore_deleted_file(
    deleted_id: str,
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(require_user),
):
    """Put a deleted file back on Disk: into its old folder if it still exists, otherwise into the root."""
    if not _is_manager(user):
        raise HTTPException(status_code=403, detail="Восстанавливать файлы могут администраторы и партнёры")
    row = await session.get(LegalHubDeletedFileModel, deleted_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Удалённый файл не найден")
    folder_id = row.folder_id if row.folder_id and await session.get(LegalHubDriveFolderModel, row.folder_id) else None
    stamp = datetime.now(timezone.utc)
    item = LegalHubDriveFileModel(
        id=str(uuid4()), folder_id=folder_id, name=row.name, content_type=row.content_type, size_bytes=row.size_bytes,
        content=row.content, blob_sha=row.blob_sha, is_hidden=False,
        created_by_user_id=row.created_by_user_id or _user_id(user), created_at=row.created_at or stamp, updated_at=stamp,
    )
    session.add(item)
    await session.delete(row)
    await session.commit()
    return {"file": _file_out(item), "parentId": folder_id}
