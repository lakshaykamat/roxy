"""LLM tool definitions and handlers for the expense tracker.

Handlers are intentionally thin: they parse the model's arguments, delegate
validation to :mod:`src.services.expense_models`, HTTP to the async client, and
wording to :mod:`src.utils.expense_formatting`. Multi-turn concerns (ambiguous
matches, delete confirmation) are held in :mod:`src.utils.expense_state`.

Every handler returns a plain ``dict`` and never raises, converting
:class:`ExpenseTrackerError` into a short, sanitized ``error`` string.
"""

from __future__ import annotations

import json
import logging

from src import config
from src.services import expense_models as models
from src.services.expense_errors import ExpenseTrackerError, ExpenseValidationError
from src.services.expense_models import Expense, match_expenses
from src.services.expense_tracker_client import get_client
from src.utils import expense_formatting as fmt
from src.utils import expense_state as state

logger = logging.getLogger(__name__)

# Keyword -> category, used only when the user did not name a category and the
# title clearly implies one. Low-confidence titles are left uncategorised.
CATEGORY_KEYWORDS = {
    "Food": ("coffee", "lunch", "dinner", "breakfast", "snack", "restaurant", "pizza", "tea"),
    "Transport": ("uber", "taxi", "cab", "fuel", "petrol", "diesel", "bus", "train", "metro", "ola"),
    "Entertainment": ("netflix", "cinema", "movie", "spotify", "game", "concert"),
    "Bills": ("electricity", "internet", "rent", "water", "gas bill", "phone bill", "wifi"),
    "Health": ("medicine", "doctor", "pharmacy", "hospital", "gym", "clinic"),
    "Groceries": ("groceries", "grocery", "supermarket", "vegetables"),
    "Education": ("course", "book", "tuition", "class", "udemy"),
    "Shopping": ("clothes", "shoes", "electronics", "amazon", "shirt", "gadget"),
}

DATE_FORMAT_NOTE = "Dates use YYYY-MM-DD and months use YYYY-MM."


def infer_category(title: str) -> str | None:
    text = (title or "").casefold()
    for category, keywords in CATEGORY_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            return category
    return None


# --------------------------------------------------------------------------- #
# Tool definitions
# --------------------------------------------------------------------------- #

CREATE_DEFINITION = {
    "type": "function",
    "function": {
        "name": "create_expense",
        "description": (
            "Record a new expense the user reports spending. Use only when the user is actually "
            "adding an expense, not when merely discussing one. Requires a title (3-100 chars) and "
            "an amount (>= 0.01). " + DATE_FORMAT_NOTE + " Date defaults to today if omitted."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "What the money was spent on, e.g. 'Dinner'."},
                "amount": {"type": "number", "description": "Amount spent, minimum 0.01."},
                "category": {"type": "string", "description": "Optional free-text category."},
                "description": {"type": "string", "description": "Optional note, max 500 chars."},
                "date": {"type": "string", "description": "Optional expense date in YYYY-MM-DD."},
                "currency": {"type": "string", "description": "Currency code if the user named one, e.g. USD."},
            },
            "required": ["title", "amount"],
            "additionalProperties": False,
        },
    },
}

LIST_DEFINITION = {
    "type": "function",
    "function": {
        "name": "list_expenses",
        "description": (
            "List or summarize expenses. With no filters it returns the current month, newest "
            "first. Use groupBy='category' for a spending-by-category summary. " + DATE_FORMAT_NOTE
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "month": {"type": "string", "description": "A specific month as YYYY-MM."},
                "start_date": {"type": "string", "description": "Range start YYYY-MM-DD (needs end_date)."},
                "end_date": {"type": "string", "description": "Range end YYYY-MM-DD (needs start_date)."},
                "group_by": {"type": "string", "enum": ["category"], "description": "Group totals by category."},
                "limit": {"type": "integer", "description": "Max records, 1-100."},
                "label": {"type": "string", "description": "Human label for the period, e.g. 'July'."},
            },
            "additionalProperties": False,
        },
    },
}

GET_DEFINITION = {
    "type": "function",
    "function": {
        "name": "get_expense",
        "description": (
            "Retrieve one expense by its API id, or by the 1-based selection number from a list "
            "Roxy just showed. Ids must come from API results and must never be invented."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "expense_id": {"type": "string", "description": "The expense's API id (24-char ObjectId)."},
                "selection": {"type": "integer", "description": "1-based choice from the last shown list."},
            },
            "additionalProperties": False,
        },
    },
}

UPDATE_DEFINITION = {
    "type": "function",
    "function": {
        "name": "update_expense",
        "description": (
            "Update fields of an existing expense. Provide the target via expense_id, a 1-based "
            "selection from the last list, or search hints (query/amount/category plus an optional "
            "period). Put only the fields to change inside 'changes'; never send an empty change. "
            "If several expenses match the search, Roxy returns candidates instead of guessing."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "expense_id": {"type": "string", "description": "The expense's API id."},
                "selection": {"type": "integer", "description": "1-based choice from the last shown list."},
                "query": {"type": "string", "description": "Text to find the expense by title/description."},
                "amount": {"type": "number", "description": "Approximate amount to help find the expense."},
                "category": {"type": "string", "description": "Category to help find the expense."},
                "month": {"type": "string", "description": "Period to search, YYYY-MM."},
                "start_date": {"type": "string", "description": "Search range start YYYY-MM-DD."},
                "end_date": {"type": "string", "description": "Search range end YYYY-MM-DD."},
                "changes": {
                    "type": "object",
                    "description": "Only the fields to change.",
                    "properties": {
                        "title": {"type": "string"},
                        "amount": {"type": "number"},
                        "category": {"type": "string"},
                        "description": {"type": "string"},
                        "date": {"type": "string", "description": "YYYY-MM-DD"},
                    },
                    "additionalProperties": False,
                },
            },
            "required": ["changes"],
            "additionalProperties": False,
        },
    },
}

DELETE_DEFINITION = {
    "type": "function",
    "function": {
        "name": "delete_expense",
        "description": (
            "Permanently delete an expense. Deletion is irreversible and REQUIRES explicit user "
            "confirmation: first call with confirmed=false to identify the expense and ask the "
            "user, then call again with confirmed=true only after they say yes. Provide the target "
            "via expense_id, a 1-based selection, or search hints. Never delete when the match is "
            "ambiguous; Roxy will return candidates to choose from."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "expense_id": {"type": "string", "description": "The expense's API id."},
                "selection": {"type": "integer", "description": "1-based choice from the last shown list."},
                "query": {"type": "string", "description": "Text to find the expense by title/description."},
                "amount": {"type": "number", "description": "Approximate amount to help find the expense."},
                "category": {"type": "string", "description": "Category to help find the expense."},
                "month": {"type": "string", "description": "Period to search, YYYY-MM."},
                "start_date": {"type": "string", "description": "Search range start YYYY-MM-DD."},
                "end_date": {"type": "string", "description": "Search range end YYYY-MM-DD."},
                "confirmed": {
                    "type": "boolean",
                    "description": "True only after the user explicitly confirmed deletion.",
                },
            },
            "additionalProperties": False,
        },
    },
}


# --------------------------------------------------------------------------- #
# Handlers
# --------------------------------------------------------------------------- #


def _currency(values: dict[str, object]) -> str:
    currency = values.get("currency")
    if isinstance(currency, str) and currency.strip():
        return currency.strip().upper()
    return config.DEFAULT_CURRENCY


def _parse_arguments(arguments: str) -> dict[str, object]:
    values = json.loads(arguments)
    if not isinstance(values, dict):
        raise ExpenseValidationError("Tool arguments must be an object.")
    return values


async def _run(handler) -> dict[str, object]:
    """Execute a handler, translating known failures into user-safe errors."""
    try:
        return await handler()
    except ExpenseTrackerError as error:
        return {"ok": False, "error": error.user_message}
    except (ValueError, TypeError, json.JSONDecodeError) as error:
        return {"ok": False, "error": str(error) or "I couldn't understand that request."}


async def create_expense(arguments: str) -> dict[str, object]:
    async def handler() -> dict[str, object]:
        values = _parse_arguments(arguments)
        if "category" not in values or not values.get("category"):
            inferred = infer_category(str(values.get("title", "")))
            if inferred:
                values = {**values, "category": inferred}
        payload = models.build_create_payload(values)
        expense = await get_client().create_expense(payload)
        currency = _currency(values)
        return {
            "ok": True,
            "expense": expense.to_public(),
            "formatted": fmt.format_created(expense, currency),
        }

    return await _run(handler)


async def list_expenses(arguments: str) -> dict[str, object]:
    async def handler() -> dict[str, object]:
        values = _parse_arguments(arguments)
        params = models.build_list_params(
            month=values.get("month"),
            start_date=values.get("start_date"),
            end_date=values.get("end_date"),
            group_by=values.get("group_by"),
            limit=values.get("limit"),
        )
        currency = _currency(values)
        label = str(values.get("label") or "")
        result = await get_client().list_expenses(params)

        if params.get("groupBy") == "category":
            groups = result["groups"]
            return {
                "ok": True,
                "groups": groups,
                "formatted": fmt.format_category_summary(groups, currency, label or "This month"),
            }

        expenses: list[Expense] = result["expenses"]
        _remember(expenses, currency)
        return {
            "ok": True,
            "expenses": [expense.to_public() for expense in expenses],
            "formatted": fmt.format_expense_list(expenses, currency, label),
        }

    return await _run(handler)


async def get_expense(arguments: str) -> dict[str, object]:
    async def handler() -> dict[str, object]:
        values = _parse_arguments(arguments)
        expense_id = _resolve_id(values)
        if expense_id is None:
            return {"ok": False, "error": "Tell me which expense you mean."}
        expense = await get_client().get_expense(expense_id)
        return {
            "ok": True,
            "expense": expense.to_public(),
            "formatted": fmt.summarize_expense(expense, _currency(values)),
        }

    return await _run(handler)


async def update_expense(arguments: str) -> dict[str, object]:
    async def handler() -> dict[str, object]:
        values = _parse_arguments(arguments)
        changes = values.get("changes")
        if not isinstance(changes, dict):
            raise ExpenseValidationError("Tell me what to change about the expense.")
        payload = models.build_update_payload(changes)  # rejects empty updates
        currency = _currency(values)

        before, ambiguous = await _locate(values)
        if ambiguous is not None:
            return ambiguous
        if before is None:
            return {"ok": False, "error": "I couldn't find that expense to update."}

        after = await get_client().update_expense(before.id, payload)
        state.clear()
        return {
            "ok": True,
            "expense": after.to_public(),
            "formatted": fmt.format_updated(before, after, currency),
        }

    return await _run(handler)


async def delete_expense(arguments: str) -> dict[str, object]:
    async def handler() -> dict[str, object]:
        values = _parse_arguments(arguments)
        currency = _currency(values)
        confirmed = values.get("confirmed") is True

        target, ambiguous = await _locate(values, allow_pending_delete=confirmed)
        if ambiguous is not None:
            return ambiguous
        if target is None:
            return {"ok": False, "error": "I couldn't find that expense to delete."}

        summary = fmt.summarize_expense(target, currency)
        if not confirmed:
            # Never touch the API before the user confirms; stash the target.
            state.set_pending_delete(target.id, summary)
            return {
                "ok": True,
                "needs_confirmation": True,
                "expense": target.to_public(),
                "formatted": f"I found “{summary}”. Should I permanently delete it?",
            }

        await get_client().delete_expense(target.id)
        state.clear()
        return {"ok": True, "deleted": True, "formatted": fmt.format_deleted(target, currency)}

    return await _run(handler)


# --------------------------------------------------------------------------- #
# Shared targeting logic
# --------------------------------------------------------------------------- #


def _remember(expenses: list[Expense], currency: str) -> None:
    state.remember_matches(
        [state.Candidate(id=expense.id, summary=fmt.summarize_expense(expense, currency))
         for expense in expenses]
    )


def _resolve_id(values: dict[str, object]) -> str | None:
    expense_id = values.get("expense_id")
    if isinstance(expense_id, str) and expense_id:
        return models.validate_object_id(expense_id)
    selection = values.get("selection")
    if isinstance(selection, int) and not isinstance(selection, bool):
        candidate = state.resolve_selection(selection)
        if candidate is not None:
            return candidate.id
    return None


async def _locate(
    values: dict[str, object], *, allow_pending_delete: bool = False
) -> tuple[Expense | None, dict[str, object] | None]:
    """Resolve the target expense for an update/delete.

    Returns ``(expense, None)`` for a single confident target, ``(None, result)``
    with an ambiguity/candidate payload when the user must choose, or
    ``(None, None)`` when nothing matched.
    """
    currency = _currency(values)
    client = get_client()

    # 1. An explicit id or a prior selection is unambiguous.
    expense_id = _resolve_id(values)
    if expense_id is not None:
        return await client.get_expense(expense_id), None

    # 2. A pending delete confirmation with no fresh target.
    has_search = any(values.get(key) for key in ("query", "amount", "category"))
    if allow_pending_delete and not has_search:
        pending_id, _summary = state.get_pending_delete()
        if pending_id:
            return await client.get_expense(pending_id), None

    if not has_search:
        return None, None

    # 3. Search a period and match by title/category/amount.
    params = models.build_list_params(
        month=values.get("month"),
        start_date=values.get("start_date"),
        end_date=values.get("end_date"),
    )
    result = await client.list_expenses(params)
    matches = match_expenses(
        result["expenses"],
        {
            "title": values.get("query"),
            "category": values.get("category"),
            "amount": values.get("amount"),
        },
    )

    if not matches:
        return None, None
    if len(matches) == 1:
        return matches[0], None

    _remember(matches, currency)
    return None, {
        "ok": True,
        "ambiguous": True,
        "matches": [
            {"selection": index, "id": expense.id,
             "summary": fmt.summarize_expense(expense, currency)}
            for index, expense in enumerate(matches, start=1)
        ],
        "formatted": _format_choices(matches, currency),
    }


def _format_choices(matches: list[Expense], currency: str) -> str:
    lines = [
        f"{index}. {fmt.summarize_expense(expense, currency)}"
        for index, expense in enumerate(matches, start=1)
    ]
    return "I found a few that match:\n\n" + "\n".join(lines) + "\n\nWhich one did you mean?"
