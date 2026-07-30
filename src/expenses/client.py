"""Asynchronous HTTP client for the expense tracker API.

The client owns a single :class:`httpx.AsyncClient` so connections are reused,
attaches the ``x-api-key`` header to every request, applies a sensible timeout,
and converts every transport/HTTP failure into an application-specific
exception. The API key is never logged.
"""

from __future__ import annotations

import logging

import httpx

from src import config
from src.expenses.errors import (
    ExpenseAuthenticationError,
    ExpenseNotFoundError,
    ExpenseServiceUnavailableError,
    ExpenseTrackerError,
    ExpenseValidationError,
)
from src.expenses.models import Expense, ExpenseAnalysis, parse_expense, parse_expense_list

logger = logging.getLogger(__name__)

API_PREFIX = "/api/v1/expenses"
CATEGORIES_PATH = f"{API_PREFIX}/categories"
BULK_UPSERT_PATH = f"{API_PREFIX}/bulk-upsert"
ANALYSIS_PATH = "/api/v1/budgets/analysis/stats"


class ExpenseTrackerClient:
    def __init__(
        self,
        *,
        api_key: str | None,
        base_url: str,
        timeout: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        if not api_key:
            # Surface a configuration problem without ever revealing the key.
            logger.error("EXPENSE_TRACKER_API_KEY is not configured")
            raise ExpenseAuthenticationError()
        self._api_key = api_key
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            transport=transport,
            headers={"x-api-key": api_key},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "ExpenseTrackerClient":
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    # ---- Public operations -------------------------------------------------

    async def create_expense(self, payload: dict[str, object]) -> Expense:
        data = await self._request("POST", API_PREFIX, json=payload)
        return parse_expense(_unwrap_object(data))

    async def bulk_upsert_expenses(self, payloads: list[dict[str, object]]) -> dict[str, object]:
        """Create records without ``_id`` and update records that include one."""
        data = await self._request("POST", BULK_UPSERT_PATH, json={"expenses": payloads})
        if not isinstance(data, dict):
            raise ExpenseServiceUnavailableError()
        return {
            "created": _coerce_count(data.get("created")),
            "updated": _coerce_count(data.get("updated")),
            "expenses": parse_expense_list(_unwrap_list(data)),
        }

    async def list_categories(self) -> list[str]:
        data = await self._request("GET", CATEGORIES_PATH)
        return _unwrap_categories(data)

    async def list_expenses(self, params: dict[str, object]) -> dict[str, object]:
        """Return ``{"expenses": [...]}`` or ``{"groups": [...]}`` when grouping.

        The API's grouped response shape is not fully specified, so grouping is
        detected from the request and the payload is normalised defensively.
        """
        data = await self._request("GET", API_PREFIX, params=params)
        if params.get("groupBy") == "category":
            return {"groups": _unwrap_groups(data)}
        return {"expenses": parse_expense_list(_unwrap_list(data))}

    async def get_expense(self, expense_id: str) -> Expense:
        data = await self._request("GET", f"{API_PREFIX}/{expense_id}")
        return parse_expense(_unwrap_object(data))

    async def update_expense(self, expense_id: str, changes: dict[str, object]) -> Expense:
        data = await self._request("PATCH", f"{API_PREFIX}/{expense_id}", json=changes)
        return parse_expense(_unwrap_object(data))

    async def delete_expense(self, expense_id: str) -> None:
        await self._request("DELETE", f"{API_PREFIX}/{expense_id}")

    async def get_analysis(self, month: str) -> ExpenseAnalysis:
        data = await self._request("GET", ANALYSIS_PATH, params={"month": month})
        return ExpenseAnalysis.from_api(_unwrap_object(data))

    # ---- Internals ---------------------------------------------------------

    async def _request(self, method: str, path: str, **kwargs: object) -> object:
        try:
            response = await self._client.request(method, path, **kwargs)
        except httpx.TimeoutException as error:
            logger.warning("Expense tracker request timed out: %s %s", method, path)
            raise ExpenseServiceUnavailableError() from error
        except httpx.HTTPError as error:
            # Never log the exception body/headers, which could echo the key.
            logger.warning("Expense tracker request failed: %s %s", method, path)
            raise ExpenseServiceUnavailableError() from error

        self._raise_for_status(response)
        if response.status_code == 204 or not response.content:
            return None
        try:
            return response.json()
        except ValueError as error:
            logger.warning("Expense tracker returned invalid JSON for %s %s", method, path)
            raise ExpenseServiceUnavailableError() from error

    def _raise_for_status(self, response: httpx.Response) -> None:
        status = response.status_code
        if status < 400:
            return
        if status == 400:
            raise ExpenseValidationError(self._field_message(response))
        if status == 401:
            logger.error("Expense tracker rejected the configured API key (401)")
            raise ExpenseAuthenticationError()
        if status == 404:
            raise ExpenseNotFoundError()
        if status >= 500:
            logger.warning("Expense tracker returned server error %s", status)
            raise ExpenseServiceUnavailableError()
        raise ExpenseTrackerError()

    @staticmethod
    def _field_message(response: httpx.Response) -> str | None:
        """Extract a safe, user-facing message from a 400 body when present."""
        try:
            body = response.json()
        except ValueError:
            return None
        if not isinstance(body, dict):
            return None
        message = body.get("message") or body.get("error")
        errors = body.get("errors")
        if isinstance(errors, list) and errors:
            first = errors[0]
            if isinstance(first, dict):
                field = first.get("field") or first.get("path")
                detail = first.get("message")
                if field and detail:
                    return f"{field}: {detail}"
                message = detail or message
        if isinstance(message, str) and message.strip():
            return message.strip()
        return None


def _unwrap_object(data: object) -> dict[str, object]:
    """Pull the expense dict out of common envelope shapes."""
    if isinstance(data, dict):
        for key in ("data", "expense", "result"):
            inner = data.get(key)
            if isinstance(inner, dict):
                return inner
        return data
    raise ExpenseServiceUnavailableError()


def _unwrap_list(data: object, keys: tuple[str, ...] = ("data", "expenses", "results", "items")) -> list[object]:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in keys:
            inner = data.get(key)
            if isinstance(inner, list):
                return inner
    return []


def _unwrap_groups(data: object) -> list[dict[str, object]]:
    """Normalise a category breakdown into ``[{category, total, count}]``."""
    groups: list[dict[str, object]] = []
    for entry in _unwrap_list(data, ("data", "groups", "categories", "expenses", "results")):
        if not isinstance(entry, dict):
            continue
        category = entry.get("category") or entry.get("_id") or entry.get("name") or "Uncategorised"
        total = entry.get("total") or entry.get("amount") or entry.get("sum") or 0
        try:
            total_value = float(total)
        except (TypeError, ValueError):
            total_value = 0.0
        group: dict[str, object] = {"category": str(category), "total": total_value}
        if "count" in entry:
            group["count"] = entry["count"]
        groups.append(group)
    return groups


def _unwrap_categories(data: object) -> list[str]:
    """Extract category labels from the documented ``data.categories`` response."""
    categories = _unwrap_object(data).get("categories")
    if not isinstance(categories, list):
        raise ExpenseServiceUnavailableError()
    labels: list[str] = []
    for category in categories:
        if isinstance(category, str) and category.strip():
            labels.append(category.strip())
        elif isinstance(category, dict):
            label = category.get("name") or category.get("label") or category.get("category")
            if isinstance(label, str) and label.strip():
                labels.append(label.strip())
    return labels


def _coerce_count(value: object) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


_client: ExpenseTrackerClient | None = None


def get_client() -> ExpenseTrackerClient:
    """Return a lazily-created, connection-reusing client from configuration."""
    global _client
    if _client is None:
        _client = ExpenseTrackerClient(
            api_key=config.EXPENSE_TRACKER_API_KEY,
            base_url=config.EXPENSE_TRACKER_BASE_URL,
            timeout=config.EXPENSE_TRACKER_TIMEOUT,
        )
    return _client
