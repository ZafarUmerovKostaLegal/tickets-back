from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from application.invoice_pdf_files import (
    InvoicePdfValidationError,
    MAX_INVOICE_PDF_BYTES,
    content_disposition,
    safe_pdf_file_name,
    validate_invoice_pdf,
)
from infrastructure.models_invoices import InvoicePdfFileModel
from infrastructure.repository_invoice_pdf_files import InvoicePdfFileRepository

PDF_A = b"%PDF-1.7\n%a\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"
PDF_B = b"%PDF-1.4\n%b\n%%EOF"


# --- pure helpers -------------------------------------------------------------------------


def test_validate_invoice_pdf_accepts_pdf_and_rejects_the_rest():
    validate_invoice_pdf(PDF_A)
    validate_invoice_pdf(b"\xef\xbb\xbf" + PDF_A)
    with pytest.raises(InvoicePdfValidationError, match="Пустой"):
        validate_invoice_pdf(b"")
    with pytest.raises(InvoicePdfValidationError, match="не PDF"):
        validate_invoice_pdf(b"PK\x03\x04 docx")
    with pytest.raises(InvoicePdfValidationError, match="МБ"):
        validate_invoice_pdf(b"%PDF-" + b"0" * MAX_INVOICE_PDF_BYTES)


def test_safe_pdf_file_name():
    assert safe_pdf_file_name('Счёт: INV/2026?*.pdf', "x.pdf") == "Счёт_ INV_2026_.pdf"
    assert safe_pdf_file_name("  ", "INV-1.pdf") == "INV-1.pdf"
    assert safe_pdf_file_name("invoice", "x.pdf") == "invoice.pdf"
    long = safe_pdf_file_name("a" * 400, "x.pdf")
    assert len(long) <= 200 and long.endswith(".pdf")


def test_content_disposition_has_ascii_fallback_and_utf8_name():
    header = content_disposition("Счёт INV-1.pdf", "attachment")
    assert header.startswith('attachment; filename=" INV-1.pdf"')
    assert "filename*=UTF-8''%D0%A1%D1%87%D1%91%D1%82%20INV-1.pdf" in header


# --- repository ---------------------------------------------------------------------------


@pytest.fixture()
def session_factory(tmp_path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'pdf.db').as_posix()}")

    async def prepare():
        async with engine.begin() as conn:
            await conn.run_sync(lambda c: InvoicePdfFileModel.__table__.create(c))

    asyncio.run(prepare())
    yield async_sessionmaker(engine, expire_on_commit=False)
    asyncio.run(engine.dispose())


def test_replace_keeps_previous_file_hidden(session_factory):
    async def scenario():
        async with session_factory() as s:
            repo = InvoicePdfFileRepository(s)
            first, prev0 = await repo.replace(invoice_id="inv-1", file_name="a.pdf", content=PDF_A, uploaded_by_auth_user_id=7)
            await s.commit()
            second, prev1 = await repo.replace(invoice_id="inv-1", file_name="b.pdf", content=PDF_B, uploaded_by_auth_user_id=8)
            await s.commit()
            assert prev0 is None
            assert prev1 is not None and prev1.id == first.id

        async with session_factory() as s:
            repo = InvoicePdfFileRepository(s)
            current = await repo.get_current("inv-1", with_content=True)
            assert current is not None and current.id == second.id
            assert current.content == PDF_B
            assert current.file_name == "b.pdf"

            rows = (await s.execute(select(InvoicePdfFileModel).order_by(InvoicePdfFileModel.uploaded_at))).scalars().all()
            assert len(rows) == 2
            old = next(r for r in rows if r.id == first.id)
            assert old.is_current is False
            assert old.replaced_at is not None
            assert old.replaced_by_file_id == second.id
            assert old.uploaded_by_auth_user_id == 7

    asyncio.run(scenario())


def test_current_meta_by_invoice_returns_only_current_files(session_factory):
    async def scenario():
        async with session_factory() as s:
            repo = InvoicePdfFileRepository(s)
            await repo.replace(invoice_id="inv-1", file_name="a.pdf", content=PDF_A, uploaded_by_auth_user_id=1)
            await repo.replace(invoice_id="inv-1", file_name="b.pdf", content=PDF_B, uploaded_by_auth_user_id=1)
            await repo.replace(invoice_id="inv-2", file_name="c.pdf", content=PDF_A, uploaded_by_auth_user_id=1)
            await s.commit()
            meta = await repo.current_meta_by_invoice(["inv-1", "inv-2", "inv-3", ""])
            assert {k: v.file_name for k, v in meta.items()} == {"inv-1": "b.pdf", "inv-2": "c.pdf"}
            assert await repo.current_meta_by_invoice([]) == {}

    asyncio.run(scenario())


# --- routes -------------------------------------------------------------------------------


@pytest.fixture()
def pdf_client(session_factory, monkeypatch: pytest.MonkeyPatch):
    from infrastructure.database import get_session
    from presentation import deps
    from presentation.routes import invoice_pdf_files as routes

    audits: list = []

    class FakeInvoiceRepository:
        def __init__(self, _session):
            pass

        async def get(self, invoice_id: str):
            return SimpleNamespace(id=invoice_id, invoice_number="INV-2026-00173") if invoice_id == "inv-1" else None

        def add_audit(self, log):
            audits.append(log)

    monkeypatch.setattr(routes, "InvoiceRepository", FakeInvoiceRepository)

    app = FastAPI()
    app.include_router(routes.router)

    async def override_session():
        async with session_factory() as session:
            yield session

    async def override_user():
        return {"id": 42, "role": "Администратор"}

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[deps.require_bearer_user] = override_user
    with TestClient(app) as client:
        yield client, audits


def test_upload_replace_and_download_flow(pdf_client):
    client, audits = pdf_client
    assert client.get("/invoices/inv-1/pdf-file").json() == {"current": None}
    assert client.get("/invoices/inv-1/pdf-file/content").status_code == 404

    r = client.put("/invoices/inv-1/pdf-file", params={"fileName": "Счёт.pdf"}, content=PDF_A)
    assert r.status_code == 200, r.text
    first = r.json()["current"]
    assert first["fileName"] == "Счёт.pdf"
    assert first["uploadedByAuthUserId"] == 42

    r = client.put("/invoices/inv-1/pdf-file", content=PDF_B)
    second = r.json()["current"]
    assert second["id"] != first["id"]
    assert second["fileName"] == "INV-2026-00173.pdf"

    meta = client.get("/invoices/inv-1/pdf-file").json()["current"]
    assert meta["id"] == second["id"]
    content = client.get("/invoices/inv-1/pdf-file/content", params={"download": "true"})
    assert content.status_code == 200
    assert content.content == PDF_B
    assert content.headers["content-type"] == "application/pdf"
    assert content.headers["content-disposition"].startswith("attachment;")

    assert [a.action for a in audits] == ["pdf_replaced", "pdf_replaced"]
    assert '"previousFileId": "' + first["id"] in audits[1].detail


def test_upload_rejects_non_pdf_and_unknown_invoice(pdf_client):
    client, _ = pdf_client
    r = client.put("/invoices/inv-1/pdf-file", content=b"not a pdf")
    assert r.status_code == 400
    assert "PDF" in r.json()["detail"]
    assert client.put("/invoices/missing/pdf-file", content=PDF_A).status_code == 404
