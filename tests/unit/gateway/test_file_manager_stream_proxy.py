"""The gateway streams file-manager bodies both ways (multi-GB uploads/downloads must not sit in memory)."""

import asyncio

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from support.service_path import ensure_service_in_path

ensure_service_in_path("gateway")
import presentation.routes.file_manager_routes as fm  # noqa: E402

upstream = FastAPI()
seen: dict = {}


@upstream.put("/api/v1/files/uploads/{upload_id}")
async def put_chunk(upload_id: str, request: Request, offset: int):
    body = b"".join([part async for part in request.stream()])
    seen.update(upload_id=upload_id, offset=offset, size=len(body), length=request.headers.get("content-length"))
    return {"received": offset + len(body)}


@upstream.get("/api/v1/files/documents/{doc_id}/content")
async def download(doc_id: str):
    async def blocks():
        for _ in range(4):
            yield b"x" * 1024

    return StreamingResponse(blocks(), media_type="application/octet-stream",
                             headers={"Content-Disposition": "attachment; filename=a.bin", "Content-Length": "4096"})


def test_upload_chunk_and_download_pass_through(monkeypatch):
    real_client = httpx.AsyncClient
    monkeypatch.setattr(fm.httpx, "AsyncClient",
                        lambda **kw: real_client(transport=httpx.ASGITransport(app=upstream), **kw))
    monkeypatch.setattr(fm, "_file_manager_base", lambda: "http://fm")
    gateway = FastAPI()
    gateway.include_router(fm.router)

    async def run():
        async with real_client(transport=httpx.ASGITransport(app=gateway), base_url="http://gw") as client:
            put = await client.put("/api/v1/files/uploads/u1", params={"offset": 8}, content=b"a" * 5000)
            down = await client.get("/api/v1/files/documents/d1/content")
            return put, down

    put, down = asyncio.run(run())
    assert put.status_code == 200 and put.json() == {"received": 5008}
    assert seen == {"upload_id": "u1", "offset": 8, "size": 5000, "length": "5000"}
    assert down.status_code == 200 and len(down.content) == 4096
    assert down.headers["content-disposition"] == "attachment; filename=a.bin"
