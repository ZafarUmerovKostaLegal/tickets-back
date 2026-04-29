

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


from enum import Enum


class TimeGroupBy(str, Enum):
    clients = "clients"
    projects = "projects"


class ExpenseGroupBy(str, Enum):
    clients = "clients"
    projects = "projects"
    categories = "categories"
    team = "team"


class ExportFormat(str, Enum):
    csv = "csv"
    xlsx = "xlsx"


class PaginationOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    page: int
    per_page: int
    total_pages: int
    total_entries: int
    next_page: Optional[int] = None
    previous_page: Optional[int] = None


class ReportResponseOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    results: list[dict[str, Any]]
    pagination: PaginationOut
    meta: dict[str, Any]


class ReportMetaOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    reportTypes: list[str]
    groupOptions: list[str]
    pageSizeMax: int = 500
    currencies: list[str] = ["UZS", "USD", "EUR"]


class ReportUserForFilterOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: int
    displayName: Optional[str] = None
    email: Optional[str] = None
