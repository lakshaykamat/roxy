"""Typed request/response models and validation for the expense tracker.

The project does not use Pydantic, so these mirror the existing dataclass style
used elsewhere in the codebase. Response objects never expose internal fields
such as ``userId`` or ``__v`` through :meth:`Expense.to_public`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import TypeVar

from src.expenses.errors import ExpenseServiceUnavailableError, ExpenseValidationError

OBJECT_ID_PATTERN = re.compile(r"^[0-9a-fA-F]{24}$")
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MONTH_PATTERN = re.compile(r"^\d{4}-\d{2}$")

TITLE_MIN = 3
TITLE_MAX = 100
DESCRIPTION_MAX = 500
MIN_AMOUNT = 0.01
BULK_EXPENSES_MIN = 1
BULK_EXPENSES_MAX = 100
BUDGET_PUBLIC_FIELDS = frozenset({"amount", "month", "totalBudget", "remainingBudget"})
ANALYSIS_REQUIRED_FIELDS = frozenset({
    "totalBudget",
    "totalExpenses",
    "remainingBudget",
    "budgetUsedPercentage",
    "budgetExists",
    "dailyAverageSpend",
})
AnalysisItem = TypeVar("AnalysisItem", "ExpenseCategoryTotal", "TopExpense", "WeeklyExpense")

# Only these fields are ever accepted from the model or sent to the API.
WRITABLE_FIELDS = ("title", "amount", "category", "description", "date")


@dataclass(frozen=True)
class Expense:
    """A single expense as returned by the API, without internal fields."""

    id: str
    title: str
    amount: float
    date: str  # normalised to YYYY-MM-DD
    category: str | None = None
    description: str | None = None

    @classmethod
    def from_api(cls, data: dict[str, object]) -> "Expense":
        if not isinstance(data, dict):
            raise ExpenseValidationError("The expense tracker returned an unexpected response.")
        identifier = data.get("_id") or data.get("id")
        return cls(
            id=str(identifier) if identifier is not None else "",
            title=str(data.get("title", "")),
            amount=_coerce_amount(data.get("amount", 0)),
            date=_normalise_date(data.get("date")),
            category=_optional_str(data.get("category")),
            description=_optional_str(data.get("description")),
        )

    def to_public(self) -> dict[str, object]:
        """Return a user-safe dict, dropping ``userId``/``__v`` and empty values."""
        public: dict[str, object] = {
            "id": self.id,
            "title": self.title,
            "amount": self.amount,
            "date": self.date,
        }
        if self.category:
            public["category"] = self.category
        if self.description:
            public["description"] = self.description
        return public


@dataclass(frozen=True)
class ExpenseCategoryTotal:
    category: str
    amount: float

    @classmethod
    def from_api(cls, data: dict[str, object]) -> "ExpenseCategoryTotal":
        return cls(
            category=str(data.get("category") or "Uncategorised"),
            amount=_coerce_amount(data.get("amount", data.get("total", 0))),
        )

    def to_public(self) -> dict[str, object]:
        return {"category": self.category, "amount": self.amount}


@dataclass(frozen=True)
class TopExpense:
    title: str
    amount: float

    @classmethod
    def from_api(cls, data: dict[str, object]) -> "TopExpense":
        return cls(
            title=str(data.get("title") or "Untitled expense"),
            amount=_coerce_amount(data.get("amount", 0)),
        )

    def to_public(self) -> dict[str, object]:
        return {"title": self.title, "amount": self.amount}


@dataclass(frozen=True)
class WeeklyExpense:
    week: int
    amount: float
    start_date: str
    end_date: str

    @classmethod
    def from_api(cls, data: dict[str, object]) -> "WeeklyExpense":
        return cls(
            week=int(_coerce_amount(data.get("week", 0))),
            amount=_coerce_amount(data.get("amount", 0)),
            start_date=_normalise_date(data.get("startDate")),
            end_date=_normalise_date(data.get("endDate")),
        )

    def to_public(self) -> dict[str, object]:
        return {
            "week": self.week,
            "amount": self.amount,
            "startDate": self.start_date,
            "endDate": self.end_date,
        }


@dataclass(frozen=True)
class ExpenseAnalysis:
    total_budget: float
    total_expenses: float
    remaining_budget: float
    budget_used_percentage: float
    budget_exists: bool
    daily_average_spend: float
    top_categories: list[ExpenseCategoryTotal]
    top_expenses: list[TopExpense]
    weekly_expenses: list[WeeklyExpense]
    budget: dict[str, object] | None = None

    @classmethod
    def from_api(cls, data: dict[str, object]) -> "ExpenseAnalysis":
        if not isinstance(data, dict) or not ANALYSIS_REQUIRED_FIELDS.issubset(data):
            raise ExpenseServiceUnavailableError()
        return cls(
            total_budget=_coerce_amount(data.get("totalBudget", 0)),
            total_expenses=_coerce_amount(data.get("totalExpenses", 0)),
            remaining_budget=_coerce_amount(data.get("remainingBudget", 0)),
            budget_used_percentage=_coerce_amount(data.get("budgetUsedPercentage", 0)),
            budget_exists=data.get("budgetExists") is True,
            daily_average_spend=_coerce_amount(data.get("dailyAverageSpend", 0)),
            top_categories=_parse_analysis_items(data.get("topCategories"), ExpenseCategoryTotal),
            top_expenses=_parse_analysis_items(data.get("topExpenses"), TopExpense),
            weekly_expenses=_parse_analysis_items(data.get("weeklyExpenses"), WeeklyExpense),
            budget=_public_budget(data.get("budget")),
        )

    def to_public(self) -> dict[str, object]:
        public: dict[str, object] = {
            "totalBudget": self.total_budget,
            "totalExpenses": self.total_expenses,
            "remainingBudget": self.remaining_budget,
            "budgetUsedPercentage": self.budget_used_percentage,
            "budgetExists": self.budget_exists,
            "dailyAverageSpend": self.daily_average_spend,
            "topCategories": [category.to_public() for category in self.top_categories],
            "topExpenses": [expense.to_public() for expense in self.top_expenses],
            "weeklyExpenses": [expense.to_public() for expense in self.weekly_expenses],
        }
        if self.budget is not None:
            public["budget"] = self.budget
        return public


def _parse_analysis_items(
    item_data: object, item_type: type[AnalysisItem]
) -> list[AnalysisItem]:
    if not isinstance(item_data, list):
        return []
    return [item_type.from_api(item) for item in item_data if isinstance(item, dict)]


def _public_budget(data: object) -> dict[str, object] | None:
    if not isinstance(data, dict):
        return None
    return {key: value for key, value in data.items() if key in BUDGET_PUBLIC_FIELDS}


def _coerce_amount(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalise_date(value: object) -> str:
    """Reduce an API date (ISO 8601 or ``YYYY-MM-DD``) to ``YYYY-MM-DD``."""
    if not value:
        return ""
    text = str(value)
    if DATE_PATTERN.match(text):
        return text
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text[:10]
    return parsed.date().isoformat()


def parse_expense(data: dict[str, object]) -> Expense:
    return Expense.from_api(data)


def parse_expense_list(items: object) -> list[Expense]:
    if not isinstance(items, list):
        return []
    return [Expense.from_api(item) for item in items if isinstance(item, dict)]


def validate_object_id(expense_id: object) -> str:
    if not isinstance(expense_id, str) or not OBJECT_ID_PATTERN.match(expense_id):
        raise ExpenseValidationError("That expense reference doesn't look valid.")
    return expense_id


def _validate_title(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ExpenseValidationError("An expense needs a title.")
    title = value.strip()
    if not (TITLE_MIN <= len(title) <= TITLE_MAX):
        raise ExpenseValidationError(
            f"The title must be between {TITLE_MIN} and {TITLE_MAX} characters."
        )
    return title


def _validate_amount(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ExpenseValidationError("The amount must be a number.")
    amount = float(value)
    if amount < MIN_AMOUNT:
        raise ExpenseValidationError("The amount must be at least 0.01.")
    return round(amount, 2)


def _validate_category(value: object, supported_categories: list[str] | None = None) -> str:
    if not isinstance(value, str):
        raise ExpenseValidationError("The category must be text.")
    category = value.strip()
    if not category:
        raise ExpenseValidationError("The category must not be empty.")
    if supported_categories is None:
        return category
    lookup = {item.casefold(): item for item in supported_categories}
    canonical = lookup.get(category.casefold())
    if canonical is None:
        raise ExpenseValidationError(f"'{value}' is not a supported category.")
    return canonical


def _validate_description(value: object) -> str:
    if not isinstance(value, str):
        raise ExpenseValidationError("The description must be text.")
    description = value.strip()
    if len(description) > DESCRIPTION_MAX:
        raise ExpenseValidationError(
            f"The description can be at most {DESCRIPTION_MAX} characters."
        )
    return description


def _validate_date(value: object) -> str:
    if not isinstance(value, str) or not DATE_PATTERN.match(value):
        raise ExpenseValidationError("Dates must use YYYY-MM-DD format.")
    try:
        date.fromisoformat(value)
    except ValueError as error:
        raise ExpenseValidationError("That date isn't a real calendar date.") from error
    return value


def build_create_payload(
    values: dict[str, object], supported_categories: list[str] | None = None,
) -> dict[str, object]:
    """Validate and assemble the POST body for creating an expense."""
    payload: dict[str, object] = {
        "title": _validate_title(values.get("title")),
        "amount": _validate_amount(values.get("amount")),
        "category": _validate_category(values.get("category"), supported_categories),
    }
    if values.get("description") not in (None, ""):
        payload["description"] = _validate_description(values["description"])
    if values.get("date") not in (None, ""):
        payload["date"] = _validate_date(values["date"])
    return payload


def build_update_payload(
    values: dict[str, object], supported_categories: list[str] | None = None,
) -> dict[str, object]:
    """Validate and assemble a PATCH body containing only supplied fields.

    Raises :class:`ExpenseValidationError` if no updatable field is present so
    that an empty update body is never sent to the API.
    """
    payload: dict[str, object] = {}
    if "title" in values:
        payload["title"] = _validate_title(values["title"])
    if "amount" in values:
        payload["amount"] = _validate_amount(values["amount"])
    if "category" in values:
        payload["category"] = _validate_category(values["category"], supported_categories)
    if "description" in values:
        payload["description"] = _validate_description(values["description"])
    if "date" in values:
        payload["date"] = _validate_date(values["date"])
    if not payload:
        raise ExpenseValidationError("Tell me what to change about the expense.")
    return payload


def build_bulk_upsert_payload(
    values: object, supported_categories: list[str] | None = None,
) -> list[dict[str, object]]:
    """Validate bulk expense records and translate tool IDs to API ``_id`` values."""
    if not isinstance(values, list) or not (BULK_EXPENSES_MIN <= len(values) <= BULK_EXPENSES_MAX):
        raise ExpenseValidationError(
            f"Provide between {BULK_EXPENSES_MIN} and {BULK_EXPENSES_MAX} expenses."
        )

    payloads: list[dict[str, object]] = []
    for index, value in enumerate(values, start=1):
        if not isinstance(value, dict):
            raise ExpenseValidationError(f"Expense {index} must be an object.")
        payload = build_create_payload(value, supported_categories)
        expense_id = value.get("expense_id")
        if expense_id is not None:
            payload["_id"] = validate_object_id(expense_id)
        payloads.append(payload)
    return payloads


def build_list_params(
    *,
    month: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    group_by: str | None = None,
    limit: int | None = None,
) -> dict[str, object]:
    """Validate and assemble query params for listing expenses."""
    params: dict[str, object] = {}
    if month is not None:
        if not isinstance(month, str) or not MONTH_PATTERN.match(month):
            raise ExpenseValidationError("Months must use YYYY-MM format.")
        params["month"] = month
    if (start_date is None) != (end_date is None):
        raise ExpenseValidationError("A date range needs both a start and an end date.")
    if start_date is not None and end_date is not None:
        params["startDate"] = _validate_date(start_date)
        params["endDate"] = _validate_date(end_date)
        if params["startDate"] > params["endDate"]:
            raise ExpenseValidationError("The start date must be on or before the end date.")
    if group_by is not None:
        if group_by != "category":
            raise ExpenseValidationError("I can only group expenses by category.")
        if start_date is None or end_date is None:
            raise ExpenseValidationError(
                "A category summary needs both a start date and an end date."
            )
        params["groupBy"] = "category"
    if limit is not None:
        if isinstance(limit, bool) or not isinstance(limit, int) or not (1 <= limit <= 100):
            raise ExpenseValidationError("The limit must be a whole number from 1 to 100.")
        params["limit"] = limit
    return params


def build_analysis_params(month: object) -> str:
    if (
        not isinstance(month, str)
        or not MONTH_PATTERN.match(month)
        or not 1 <= int(month[5:]) <= 12
    ):
        raise ExpenseValidationError("Months must use YYYY-MM format.")
    return month


def match_expenses(expenses: list[Expense], query: dict[str, object]) -> list[Expense]:
    """Rank candidate expenses against a loose query.

    ``query`` may contain ``title``/``text``, ``category``, and ``amount``.
    Returns candidates sorted by descending match strength; only expenses with a
    positive score are returned so an empty result means "no plausible match".
    """
    text = str(query.get("title") or query.get("text") or "").strip().casefold()
    category = str(query.get("category") or "").strip().casefold()
    amount = query.get("amount")

    scored: list[tuple[int, Expense]] = []
    for expense in expenses:
        score = 0
        haystack = f"{expense.title} {expense.category or ''} {expense.description or ''}".casefold()
        if text:
            if text == expense.title.casefold():
                score += 3
            elif text in haystack:
                score += 2
        if category and expense.category and category == expense.category.casefold():
            score += 2
        if isinstance(amount, (int, float)) and not isinstance(amount, bool):
            if abs(expense.amount - float(amount)) < 0.005:
                score += 3
        if score:
            scored.append((score, expense))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [expense for _, expense in scored]
