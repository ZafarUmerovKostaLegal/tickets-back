from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend_common.cors_origins import resolve_cors_origins

from presentation.routes import client_contacts, colleagues, health

CONTACTS_API_PREFIX = "/api/v1/contacts"

app = FastAPI(
    title="Contacts",
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=resolve_cors_origins(),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(health.router)
app.include_router(colleagues.router, prefix=CONTACTS_API_PREFIX)
app.include_router(client_contacts.router, prefix=CONTACTS_API_PREFIX)
