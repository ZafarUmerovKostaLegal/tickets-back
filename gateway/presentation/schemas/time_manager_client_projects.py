

from datetime import date
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

ReportVisibility = Literal["managers_only", "all_assigned"]
ProjectType = Literal["time_and_materials", "fixed_fee", "non_billable"]
ProjectCurrency = Literal["USD", "UZS", "EUR", "RUB", "GBP"]


class TimeManagerClientProjectCreateBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(..., min_length=1, max_length=500)
    code: Optional[str] = Field(None, max_length=64)
    start_date: Optional[date] = Field(None, alias="startDate")
    end_date: Optional[date] = Field(None, alias="endDate")
    notes: Optional[str] = None
    report_visibility: ReportVisibility = Field("managers_only", alias="reportVisibility")
    project_type: ProjectType = Field("time_and_materials", alias="projectType")
    currency: ProjectCurrency = Field("USD", description="Валюта проекта")
    billable_rate_type: Optional[str] = Field(None, max_length=64, alias="billableRateType")
    project_billable_rate_amount: Optional[Decimal] = Field(
        None,
        ge=0,
        alias="projectBillableRateAmount",
        description="Ставка billable за час по проекту; копируется на сотрудников с доступом.",
    )
    budget_type: Optional[str] = Field(None, max_length=64, alias="budgetType")
    budget_amount: Optional[Decimal] = Field(
        None,
        ge=0,
        alias="budgetAmount",
        description="Бюджет в валюте; для fixed_fee — сумма контракта. С budgetHours — пакет сумма+часы.",
    )
    budget_hours: Optional[Decimal] = Field(
        None,
        ge=0,
        alias="budgetHours",
        description="Лимит часов; с budgetAmount — оба лимита (дашборд hours_and_money).",
    )
    budget_resets_every_month: bool = Field(False, alias="budgetResetsEveryMonth")
    budget_includes_expenses: bool = Field(False, alias="budgetIncludesExpenses")
    send_budget_alerts: bool = Field(False, alias="sendBudgetAlerts")
    budget_alert_threshold_percent: Optional[Decimal] = Field(
        None,
        ge=0,
        le=100,
        alias="budgetAlertThresholdPercent",
    )
    fixed_fee_amount: Optional[Decimal] = Field(
        None,
        ge=0,
        alias="fixedFeeAmount",
        description="Устарело — задайте budgetAmount.",
    )
    is_archived: bool = Field(False, alias="isArchived")
    initial_time_tracking_user_auth_ids: list[int] = Field(
        default_factory=list,
        alias="initialTimeTrackingUserAuthIds",
        description="Доступ к новому проекту сразу после создания (auth_user_id в TT).",
    )
    access_granted_by_auth_user_id: Optional[int] = Field(
        None,
        alias="accessGrantedByAuthUserId",
        description="Кто выдал доступ (аудит).",
    )


class TimeManagerClientProjectPatchBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: Optional[str] = Field(None, max_length=500)
    code: Optional[str] = Field(None, max_length=64)
    start_date: Optional[date] = Field(None, alias="startDate")
    end_date: Optional[date] = Field(None, alias="endDate")
    notes: Optional[str] = None
    report_visibility: Optional[ReportVisibility] = Field(None, alias="reportVisibility")
    project_type: Optional[ProjectType] = Field(None, alias="projectType")
    currency: Optional[ProjectCurrency] = Field(None, description="Валюта проекта")
    billable_rate_type: Optional[str] = Field(None, max_length=64, alias="billableRateType")
    project_billable_rate_amount: Optional[Decimal] = Field(
        None,
        ge=0,
        alias="projectBillableRateAmount",
        description="Ставка billable за час по проекту; копируется на сотрудников с доступом.",
    )
    budget_type: Optional[str] = Field(None, max_length=64, alias="budgetType")
    budget_amount: Optional[Decimal] = Field(
        None,
        ge=0,
        alias="budgetAmount",
        description="Бюджет в валюте; с budgetHours — пакет сумма+часы.",
    )
    budget_hours: Optional[Decimal] = Field(
        None,
        ge=0,
        alias="budgetHours",
        description="Лимит часов; с budgetAmount — оба лимита.",
    )
    budget_resets_every_month: Optional[bool] = Field(None, alias="budgetResetsEveryMonth")
    budget_includes_expenses: Optional[bool] = Field(None, alias="budgetIncludesExpenses")
    send_budget_alerts: Optional[bool] = Field(None, alias="sendBudgetAlerts")
    budget_alert_threshold_percent: Optional[Decimal] = Field(
        None,
        ge=0,
        le=100,
        alias="budgetAlertThresholdPercent",
    )
    fixed_fee_amount: Optional[Decimal] = Field(
        None,
        ge=0,
        alias="fixedFeeAmount",
        description="Устарело — используйте budgetAmount.",
    )
    is_archived: Optional[bool] = Field(None, alias="isArchived")
