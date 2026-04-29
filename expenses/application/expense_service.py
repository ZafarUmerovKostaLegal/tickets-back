

from decimal import Decimal, ROUND_HALF_UP


ALLOWED_STATUSES = frozenset(
    {
        "draft",
        "pending_approval",
        "revision_required",
        "approved",
        "rejected",
        "paid",
        "closed",
        "not_reimbursable",
        "withdrawn",
    }
)


ALLOWED_EXPENSE_TYPES = frozenset(
    {
        "transport",
        "food",
        "accommodation",
        "purchase",
        "services",
        "entertainment",
        "client_expense",
        "partner_expense",
        "other",
    }
)


PARTNER_EXPENSE_SUBTYPES = frozenset(
    {
        "partner_fuel",
        "partner_air",
        "partner_meetings_food",
        "partner_shop",
        "partner_misc",
    }
)


_EXPENSE_TYPE_ALIASES = {"meals": "food", "office": "services"}


ALLOWED_PAYMENT_METHODS = frozenset({"cash", "card", "transfer", "other_payment"})


REGISTRY_STATUSES = frozenset({"approved", "paid", "closed"})


REPORT_INCLUSION_STATUSES = frozenset(
    {
        "pending_approval",
        "revision_required",
        "approved",
        "paid",
        "closed",
        "not_reimbursable",
    }
)


def calc_equivalent(amount_uzs: Decimal, exchange_rate: Decimal) -> Decimal:

    if exchange_rate <= 0:
        raise ValueError("exchange_rate must be positive")
    return (amount_uzs / exchange_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def validate_expense_type(code: str) -> str:
    c = (code or "").strip()
    c = _EXPENSE_TYPE_ALIASES.get(c, c)
    if c not in ALLOWED_EXPENSE_TYPES:
        raise ValueError(
            f"Недопустимый expenseType: {code!r}. Допустимо: {', '.join(sorted(ALLOWED_EXPENSE_TYPES))}"
        )
    return c


def is_partner_expense(expense_type: str | None) -> bool:
    return (expense_type or "").strip() == "partner_expense"


def validate_expense_subtype_rules(expense_type: str, expense_subtype: str | None) -> None:

    if (expense_type or "").strip() != "partner_expense":
        return
    s = (expense_subtype or "").strip()
    if not s or s not in PARTNER_EXPENSE_SUBTYPES:
        raise ValueError(
            f"Для partner_expense укажите expenseSubtype из: {', '.join(sorted(PARTNER_EXPENSE_SUBTYPES))}"
        )


def normalize_payment_method(v: str | None) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    k = s.lower()
    if k not in ALLOWED_PAYMENT_METHODS:
        raise ValueError(
            f"Недопустимый paymentMethod: {v!r}. Допустимо: {', '.join(sorted(ALLOWED_PAYMENT_METHODS))} или пусто"
        )
    return k


def validate_submit_fields(
    *,
    description: str,
    expense_date,
    payment_deadline=None,
    amount_uzs: Decimal,
    exchange_rate: Decimal,
    expense_type: str,
    expense_subtype: str | None = None,
    is_reimbursable: bool,
    comment: str | None,
    project_id: str | None = None,
    attachment_count: int,
    expense_amount_limit_uzs: Decimal | None,
    payment_document_count: int = 0,
    payment_receipt_count: int = 0,
) -> None:
    if not (description or "").strip():
        raise ValueError("description is required")
    if expense_date is None:
        raise ValueError("expenseDate is required")
    if payment_deadline is not None and expense_date is not None and payment_deadline < expense_date:
        raise ValueError("Конечный срок оплаты не может быть раньше даты расхода")
    if amount_uzs is None or amount_uzs <= 0:
        raise ValueError("amountUzs must be greater than 0")
    if exchange_rate is None or exchange_rate <= 0:
        raise ValueError("exchangeRate must be greater than 0")
    validate_expense_type(expense_type)
    validate_expense_subtype_rules(expense_type, expense_subtype)
    if is_reimbursable is None:
        raise ValueError("isReimbursable is required")
    if expense_amount_limit_uzs is not None and amount_uzs > expense_amount_limit_uzs:
        raise ValueError("amountUzs exceeds allowed limit; additional approval may be required")

    if is_partner_expense(expense_type):
        return
    if is_reimbursable:


        if payment_document_count >= 1:
            pass
        elif attachment_count >= 1 and payment_document_count == 0 and payment_receipt_count == 0:

            pass
        else:
            raise ValueError("Для возмещаемого расхода приложите документ для оплаты")
    if (
        is_reimbursable
        and (expense_type or "").strip() == "other"
        and not (comment or "").strip()
    ):
        raise ValueError("comment is required when expenseType is other and the expense is reimbursable")
