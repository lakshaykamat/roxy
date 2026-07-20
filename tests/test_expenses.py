import importlib
import json
import logging
import os
import unittest
from datetime import date, datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import httpx

os.environ.setdefault("ALLOWED_USER_ID", "1")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("OPENAI_API_KEY", "test-key")

from src import config
from src.services import expense_models as models
from src.services.expense_errors import (
    ExpenseAuthenticationError,
    ExpenseNotFoundError,
    ExpenseServiceUnavailableError,
    ExpenseValidationError,
)
from src.services.expense_models import Expense, ExpenseCategory
from src.services.expense_tracker_client import ExpenseTrackerClient
from src.tools import expenses
from src.utils import expense_state as state
from src.utils.dates import resolve_month, resolve_relative_date
from src.utils.expense_formatting import (
    format_amount,
    format_category_summary,
    format_expense_list,
)

API_KEY = "SUPER-SECRET-KEY-do-not-log"
BASE_URL = "https://expenses.test"


def expense_json(**overrides):
    payload = {
        "_id": "665f1e2a9c4d1a0012ab34cd",
        "title": "Coffee",
        "amount": 4.5,
        "category": "Food",
        "description": "Morning latte",
        "date": "2026-07-20T00:00:00.000Z",
        "userId": "665f1e2a9c4d1a0012ab0000",
        "createdAt": "2026-07-20T08:15:30.123Z",
        "updatedAt": "2026-07-20T08:15:30.123Z",
        "__v": 0,
    }
    payload.update(overrides)
    return payload


def make_client(handler, api_key=API_KEY):
    return ExpenseTrackerClient(
        api_key=api_key,
        base_url=BASE_URL,
        transport=httpx.MockTransport(handler),
    )


class Recorder:
    """A MockTransport handler that records requests and serves canned routes."""

    def __init__(self):
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.respond(request)

    def respond(self, request: httpx.Request) -> httpx.Response:  # overridden per test
        return httpx.Response(200, json=expense_json())

    def methods(self):
        return [request.method for request in self.requests]


# --------------------------------------------------------------------------- #
# Models & validation
# --------------------------------------------------------------------------- #


class ModelTests(unittest.TestCase):
    def test_expense_from_api_drops_internal_fields_and_normalises_date(self):
        expense = Expense.from_api(expense_json())
        public = expense.to_public()
        self.assertEqual(expense.id, "665f1e2a9c4d1a0012ab34cd")
        self.assertEqual(expense.date, "2026-07-20")
        self.assertNotIn("userId", public)
        self.assertNotIn("__v", public)
        self.assertNotIn("createdAt", public)

    def test_build_create_payload_validates_and_keeps_optional_fields(self):
        payload = models.build_create_payload(
            {"title": "Coffee", "amount": 4.5, "category": "Food", "date": "2026-07-20"}
        )
        self.assertEqual(payload["amount"], 4.5)
        self.assertEqual(payload["category"], "Food")

    def test_build_create_payload_rejects_amount_below_minimum(self):
        with self.assertRaisesRegex(ExpenseValidationError, "at least 0.01"):
            models.build_create_payload({"title": "Coffee", "amount": 0})

    def test_build_create_payload_rejects_short_title(self):
        with self.assertRaisesRegex(ExpenseValidationError, "between 3 and 100"):
            models.build_create_payload({"title": "ab", "amount": 5})

    def test_build_update_payload_includes_only_supplied_fields(self):
        payload = models.build_update_payload({"amount": 220})
        self.assertEqual(payload, {"amount": 220.0})

    def test_build_update_payload_rejects_empty_body(self):
        with self.assertRaisesRegex(ExpenseValidationError, "what to change"):
            models.build_update_payload({})

    def test_build_list_params_requires_both_range_bounds(self):
        with self.assertRaisesRegex(ExpenseValidationError, "start and an end"):
            models.build_list_params(start_date="2026-07-01")

    def test_build_list_params_accepts_month_group_and_limit(self):
        params = models.build_list_params(month="2026-06", group_by="category", limit=10)
        self.assertEqual(params, {"month": "2026-06", "groupBy": "category", "limit": 10})

    def test_validate_object_id_rejects_non_object_id(self):
        with self.assertRaises(ExpenseValidationError):
            models.validate_object_id("not-an-id")

    def test_match_expenses_ranks_by_title_category_and_amount(self):
        coffee = Expense("a" * 24, "Coffee", 4.5, "2026-07-20", "Food")
        uber = Expense("b" * 24, "Uber ride", 6.2, "2026-07-19", "Transportation")
        matches = models.match_expenses([coffee, uber], {"title": "uber"})
        self.assertEqual([m.id for m in matches], [uber.id])

    def test_validate_category_accepts_canonical_value(self):
        payload = models.build_create_payload({"title": "Coffee", "amount": 4.5, "category": "Food"})
        self.assertEqual(payload["category"], "Food")

    def test_validate_category_case_insensitive(self):
        payload = models.build_create_payload({"title": "Coffee", "amount": 4.5, "category": "food"})
        self.assertEqual(payload["category"], "Food")
        payload2 = models.build_create_payload({"title": "KFC", "amount": 200, "category": "FAST FOOD"})
        self.assertEqual(payload2["category"], "Fast Food")

    def test_validate_category_rejects_unsupported_value(self):
        with self.assertRaises(ExpenseValidationError):
            models.build_create_payload({"title": "Coffee", "amount": 4.5, "category": "Bills"})

    def test_validate_category_rejects_unsupported_in_update(self):
        with self.assertRaises(ExpenseValidationError):
            models.build_update_payload({"category": "Transport"})

    def test_all_supported_categories_accepted_in_create(self):
        for cat in ExpenseCategory:
            payload = models.build_create_payload({"title": "Test", "amount": 1.0, "category": cat.value})
            self.assertEqual(payload["category"], cat.value)

    def test_all_supported_categories_accepted_in_update(self):
        for cat in ExpenseCategory:
            payload = models.build_update_payload({"category": cat.value})
            self.assertEqual(payload["category"], cat.value)


# --------------------------------------------------------------------------- #
# Category validation
# --------------------------------------------------------------------------- #


class CategoryValidationTests(unittest.TestCase):
    def test_tool_schema_create_category_enum_matches_expense_category_enum(self):
        schema_enums = set(
            expenses.CREATE_DEFINITION["function"]["parameters"]["properties"]["category"]["enum"]
        )
        self.assertEqual(schema_enums, {cat.value for cat in ExpenseCategory})

    def test_tool_schema_update_category_enum_matches_expense_category_enum(self):
        schema_enums = set(
            expenses.UPDATE_DEFINITION["function"]["parameters"]["properties"]["changes"]["properties"]["category"]["enum"]
        )
        self.assertEqual(schema_enums, {cat.value for cat in ExpenseCategory})

    def test_validate_category_case_insensitive(self):
        payload = models.build_create_payload({"title": "Coffee", "amount": 4.5, "category": "food"})
        self.assertEqual(payload["category"], "Food")
        payload2 = models.build_create_payload({"title": "KFC", "amount": 200, "category": "FAST FOOD"})
        self.assertEqual(payload2["category"], "Fast Food")

    def test_validate_category_rejects_unsupported_value(self):
        with self.assertRaises(ExpenseValidationError):
            models.build_create_payload({"title": "Coffee", "amount": 4.5, "category": "Bills"})

    def test_validate_category_rejects_unsupported_in_update(self):
        with self.assertRaises(ExpenseValidationError):
            models.build_update_payload({"category": "Transport"})


# --------------------------------------------------------------------------- #
# Currency & formatting
# --------------------------------------------------------------------------- #


class FormattingTests(unittest.TestCase):
    def test_format_amount_in_configured_currency(self):
        self.assertEqual(format_amount(450, "INR"), "₹450")
        self.assertEqual(format_amount(4.5, "INR"), "₹4.50")
        self.assertEqual(format_amount(2480, "INR"), "₹2,480")
        self.assertEqual(format_amount(5, "USD"), "$5")
        self.assertEqual(format_amount(5, "XYZ"), "5 XYZ")

    def test_format_expense_list_includes_total_and_handles_empty(self):
        expenses_list = [
            Expense("a" * 24, "Dinner", 450, "2026-07-20", "Food"),
            Expense("b" * 24, "Uber", 320, "2026-07-19", "Transportation"),
        ]
        rendered = format_expense_list(expenses_list, "INR", "July")
        self.assertIn("1. Dinner — ₹450 — Food — July 20", rendered)
        self.assertIn("Total: ₹770", rendered)
        self.assertEqual(
            format_expense_list([], "INR"),
            "I couldn't find any expenses for that period.",
        )

    def test_format_category_summary_uses_canonical_labels(self):
        groups = [
            {"category": "Food", "total": 2480},
            {"category": "Fast Food", "total": 1100},
            {"category": "Housing", "total": 3000},
        ]
        rendered = format_category_summary(groups, "INR")
        self.assertIn("Food: ₹2,480", rendered)
        self.assertIn("Fast Food: ₹1,100", rendered)
        self.assertIn("Housing: ₹3,000", rendered)
        self.assertIn("Total: ₹6,580", rendered)


# --------------------------------------------------------------------------- #
# Relative date parsing
# --------------------------------------------------------------------------- #


class DateTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 7, 20, 21, 0, tzinfo=ZoneInfo("Asia/Kolkata"))

    def test_today_yesterday_tomorrow(self):
        self.assertEqual(resolve_relative_date("today", now=self.now), "2026-07-20")
        self.assertEqual(resolve_relative_date("tonight", now=self.now), "2026-07-20")
        self.assertEqual(resolve_relative_date("yesterday", now=self.now), "2026-07-19")
        self.assertEqual(resolve_relative_date("tomorrow", now=self.now), "2026-07-21")

    def test_last_weekday_is_a_recent_past_friday(self):
        result = resolve_relative_date("last friday", now=self.now)
        parsed = date.fromisoformat(result)
        self.assertEqual(parsed.strftime("%A"), "Friday")
        self.assertLess(parsed, self.now.date())
        self.assertLessEqual((self.now.date() - parsed).days, 7)

    def test_month_phrases(self):
        self.assertEqual(resolve_month("this month", now=self.now), "2026-07")
        self.assertEqual(resolve_month("last month", now=self.now), "2026-06")

    def test_unknown_phrase_raises(self):
        with self.assertRaises(ValueError):
            resolve_relative_date("someday", now=self.now)


# --------------------------------------------------------------------------- #
# HTTP client (mocked transport)
# --------------------------------------------------------------------------- #


class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_expense_posts_body_and_parses_response(self):
        recorder = Recorder()
        recorder.respond = lambda request: httpx.Response(201, json=expense_json())
        async with make_client(recorder) as client:
            expense = await client.create_expense({"title": "Coffee", "amount": 4.5})
        self.assertEqual(recorder.methods(), ["POST"])
        self.assertEqual(json.loads(recorder.requests[0].content)["title"], "Coffee")
        self.assertEqual(expense.title, "Coffee")

    async def test_list_current_month_sends_no_filters(self):
        recorder = Recorder()
        recorder.respond = lambda request: httpx.Response(200, json=[expense_json()])
        async with make_client(recorder) as client:
            result = await client.list_expenses({})
        self.assertEqual(str(recorder.requests[0].url.params), "")
        self.assertEqual(len(result["expenses"]), 1)

    async def test_list_specific_month_sends_month_param(self):
        recorder = Recorder()
        recorder.respond = lambda request: httpx.Response(200, json=[])
        async with make_client(recorder) as client:
            await client.list_expenses({"month": "2026-06"})
        self.assertEqual(recorder.requests[0].url.params.get("month"), "2026-06")

    async def test_list_custom_range_sends_both_bounds(self):
        recorder = Recorder()
        recorder.respond = lambda request: httpx.Response(200, json=[])
        async with make_client(recorder) as client:
            await client.list_expenses({"startDate": "2026-07-01", "endDate": "2026-07-15"})
        params = recorder.requests[0].url.params
        self.assertEqual(params.get("startDate"), "2026-07-01")
        self.assertEqual(params.get("endDate"), "2026-07-15")

    async def test_list_category_grouping_returns_groups(self):
        recorder = Recorder()
        recorder.respond = lambda request: httpx.Response(
            200, json=[{"category": "Food", "total": 2480}, {"category": "Bills", "total": 3000}]
        )
        async with make_client(recorder) as client:
            result = await client.list_expenses({"groupBy": "category"})
        self.assertEqual(recorder.requests[0].url.params.get("groupBy"), "category")
        self.assertEqual(result["groups"][0], {"category": "Food", "total": 2480.0})

    async def test_get_expense_retrieves_by_id(self):
        recorder = Recorder()
        recorder.respond = lambda request: httpx.Response(200, json=expense_json())
        async with make_client(recorder) as client:
            expense = await client.get_expense("665f1e2a9c4d1a0012ab34cd")
        self.assertTrue(recorder.requests[0].url.path.endswith("665f1e2a9c4d1a0012ab34cd"))
        self.assertEqual(expense.amount, 4.5)

    async def test_update_expense_sends_only_supplied_fields(self):
        recorder = Recorder()
        recorder.respond = lambda request: httpx.Response(200, json=expense_json(amount=6.0))
        async with make_client(recorder) as client:
            await client.update_expense("665f1e2a9c4d1a0012ab34cd", {"amount": 6.0})
        self.assertEqual(recorder.methods(), ["PATCH"])
        self.assertEqual(json.loads(recorder.requests[0].content), {"amount": 6.0})

    async def test_400_maps_to_validation_error_with_message(self):
        recorder = Recorder()
        recorder.respond = lambda request: httpx.Response(
            400, json={"message": "amount must be at least 0.01"}
        )
        async with make_client(recorder) as client:
            with self.assertRaises(ExpenseValidationError) as ctx:
                await client.create_expense({"title": "x", "amount": 1})
        self.assertIn("at least 0.01", ctx.exception.user_message)

    async def test_401_maps_to_authentication_error(self):
        recorder = Recorder()
        recorder.respond = lambda request: httpx.Response(401, json={"message": "no"})
        async with make_client(recorder) as client:
            with self.assertRaises(ExpenseAuthenticationError):
                await client.list_expenses({})

    async def test_404_maps_to_not_found(self):
        recorder = Recorder()
        recorder.respond = lambda request: httpx.Response(404, json={"message": "gone"})
        async with make_client(recorder) as client:
            with self.assertRaises(ExpenseNotFoundError):
                await client.get_expense("665f1e2a9c4d1a0012ab34cd")

    async def test_timeout_maps_to_service_unavailable(self):
        def handler(request):
            raise httpx.TimeoutException("timed out", request=request)

        async with make_client(handler) as client:
            with self.assertRaises(ExpenseServiceUnavailableError):
                await client.list_expenses({})

    async def test_server_error_maps_to_service_unavailable(self):
        recorder = Recorder()
        recorder.respond = lambda request: httpx.Response(500, text="boom")
        async with make_client(recorder) as client:
            with self.assertRaises(ExpenseServiceUnavailableError):
                await client.list_expenses({})

    async def test_api_key_is_never_logged(self):
        captured: list[str] = []

        class ListHandler(logging.Handler):
            def emit(self, record):
                captured.append(record.getMessage())

        handler = ListHandler()
        root = logging.getLogger()
        root.addHandler(handler)
        previous_level = root.level
        root.setLevel(logging.DEBUG)
        try:
            def transport(request):
                if request.url.path.endswith("boom"):
                    return httpx.Response(401, json={"message": "no"})
                return httpx.Response(200, json=expense_json())

            async with make_client(transport) as client:
                await client.create_expense({"title": "Coffee", "amount": 4.5})
                with self.assertRaises(ExpenseAuthenticationError):
                    await client.get_expense("boom")
        finally:
            root.removeHandler(handler)
            root.setLevel(previous_level)

        joined = "\n".join(captured)
        self.assertNotIn(API_KEY, joined)


# --------------------------------------------------------------------------- #
# Tool handlers
# --------------------------------------------------------------------------- #


class ToolHandlerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        state.clear()
        self.addCleanup(state.clear)

    def use_client(self, recorder):
        client = make_client(recorder)
        patcher = patch("src.tools.expenses.get_client", return_value=client)
        patcher.start()
        self.addCleanup(patcher.stop)
        return recorder

    async def test_create_expense_with_llm_supplied_category(self):
        recorder = Recorder()
        recorder.respond = lambda request: httpx.Response(201, json=expense_json())
        self.use_client(recorder)

        result = await expenses.create_expense('{"title": "Coffee", "amount": 4.5, "category": "Food"}')

        self.assertTrue(result["ok"])
        self.assertEqual(json.loads(recorder.requests[0].content)["category"], "Food")
        self.assertIn("Added", result["formatted"])

    async def test_create_expense_without_category_sends_no_category(self):
        recorder = Recorder()
        recorder.respond = lambda request: httpx.Response(201, json=expense_json(category=None))
        self.use_client(recorder)

        result = await expenses.create_expense('{"title": "Coffee", "amount": 4.5}')

        self.assertTrue(result["ok"])
        body = json.loads(recorder.requests[0].content)
        self.assertNotIn("category", body)

    async def test_create_expense_with_canonical_category_sends_it(self):
        recorder = Recorder()
        recorder.respond = lambda request: httpx.Response(201, json=expense_json(category="Transportation"))
        self.use_client(recorder)

        result = await expenses.create_expense('{"title": "Uber", "amount": 180, "category": "Transportation"}')

        self.assertTrue(result["ok"])
        self.assertEqual(json.loads(recorder.requests[0].content)["category"], "Transportation")

    async def test_create_expense_accepts_case_insensitive_category(self):
        recorder = Recorder()
        recorder.respond = lambda request: httpx.Response(201, json=expense_json(category="Food"))
        self.use_client(recorder)

        result = await expenses.create_expense('{"title": "Lunch", "amount": 200, "category": "food"}')

        self.assertTrue(result["ok"])
        self.assertEqual(json.loads(recorder.requests[0].content)["category"], "Food")

    async def test_create_expense_rejects_invalid_category_without_api_call(self):
        recorder = Recorder()
        self.use_client(recorder)

        result = await expenses.create_expense('{"title": "Lunch", "amount": 200, "category": "Bills"}')

        self.assertFalse(result["ok"])
        self.assertIn("Bills", result["error"])
        self.assertEqual(recorder.requests, [])

    async def test_update_expense_sends_canonical_category(self):
        def handler(request):
            if request.method == "GET":
                return httpx.Response(200, json=expense_json(title="Gym", amount=800, category="Miscellaneous"))
            return httpx.Response(200, json=expense_json(title="Gym", amount=800, category="Health & Fitness"))

        recorder = Recorder()
        recorder.respond = handler
        self.use_client(recorder)

        result = await expenses.update_expense(
            '{"expense_id": "665f1e2a9c4d1a0012ab34cd", "changes": {"category": "Health & Fitness"}}'
        )

        self.assertTrue(result["ok"])
        patch_body = json.loads(next(r for r in recorder.requests if r.method == "PATCH").content)
        self.assertEqual(patch_body["category"], "Health & Fitness")

    async def test_update_expense_preserves_category_when_not_in_changes(self):
        def handler(request):
            if request.method == "GET":
                return httpx.Response(200, json=expense_json(title="Gym", amount=800, category="Health & Fitness"))
            return httpx.Response(200, json=expense_json(title="Gym", amount=900, category="Health & Fitness"))

        recorder = Recorder()
        recorder.respond = handler
        self.use_client(recorder)

        result = await expenses.update_expense(
            '{"expense_id": "665f1e2a9c4d1a0012ab34cd", "changes": {"amount": 900}}'
        )

        self.assertTrue(result["ok"])
        patch_body = json.loads(next(r for r in recorder.requests if r.method == "PATCH").content)
        self.assertNotIn("category", patch_body)

    async def test_create_rejects_invalid_amount_without_calling_api(self):
        recorder = Recorder()
        self.use_client(recorder)

        result = await expenses.create_expense('{"title": "Coffee", "amount": 0}')

        self.assertFalse(result["ok"])
        self.assertIn("0.01", result["error"])
        self.assertEqual(recorder.requests, [])

    async def test_list_current_month_formats_response(self):
        recorder = Recorder()
        recorder.respond = lambda request: httpx.Response(
            200, json=[expense_json(title="Dinner", amount=450, category="Food")]
        )
        self.use_client(recorder)

        result = await expenses.list_expenses("{}")

        self.assertTrue(result["ok"])
        self.assertEqual(len(result["expenses"]), 1)
        self.assertIn("Dinner", result["formatted"])

    async def test_delete_without_confirmation_never_calls_delete(self):
        recorder = Recorder()
        recorder.respond = lambda request: httpx.Response(200, json=expense_json(title="Uber ride", amount=620))
        self.use_client(recorder)

        result = await expenses.delete_expense(
            '{"expense_id": "665f1e2a9c4d1a0012ab34cd"}'
        )

        self.assertTrue(result["ok"])
        self.assertTrue(result["needs_confirmation"])
        self.assertNotIn("DELETE", recorder.methods())
        self.assertIn("Should I permanently delete", result["formatted"])

    async def test_delete_after_confirmation_calls_delete(self):
        recorder = Recorder()
        recorder.respond = lambda request: httpx.Response(200, json=expense_json(title="Uber ride", amount=620))
        self.use_client(recorder)

        result = await expenses.delete_expense(
            '{"expense_id": "665f1e2a9c4d1a0012ab34cd", "confirmed": true}'
        )

        self.assertTrue(result["ok"])
        self.assertTrue(result["deleted"])
        self.assertIn("DELETE", recorder.methods())
        self.assertIn("Deleted", result["formatted"])

    async def test_delete_with_ambiguous_matches_lists_candidates(self):
        recorder = Recorder()
        recorder.respond = lambda request: httpx.Response(
            200,
            json=[
                expense_json(_id="a" * 24, title="Uber ride", amount=620),
                expense_json(_id="b" * 24, title="Uber ride", amount=430),
            ],
        )
        self.use_client(recorder)

        result = await expenses.delete_expense('{"query": "uber"}')

        self.assertTrue(result["ok"])
        self.assertTrue(result["ambiguous"])
        self.assertEqual(len(result["matches"]), 2)
        self.assertNotIn("DELETE", recorder.methods())
        # The candidates are remembered so a follow-up selection can resolve them.
        self.assertEqual(len(state.get_matches()), 2)

    async def test_update_sends_only_supplied_fields(self):
        def handler(request):
            if request.method == "GET":
                return httpx.Response(200, json=expense_json(title="Coffee", amount=180))
            return httpx.Response(200, json=expense_json(title="Coffee", amount=220))

        recorder = Recorder()
        recorder.respond = handler
        self.use_client(recorder)

        result = await expenses.update_expense(
            '{"expense_id": "665f1e2a9c4d1a0012ab34cd", "changes": {"amount": 220}}'
        )

        patch_request = next(r for r in recorder.requests if r.method == "PATCH")
        self.assertEqual(json.loads(patch_request.content), {"amount": 220.0})
        self.assertEqual(result["formatted"], "Updated Coffee from ₹180 to ₹220.")

    async def test_update_with_empty_changes_is_rejected(self):
        recorder = Recorder()
        self.use_client(recorder)

        result = await expenses.update_expense(
            '{"expense_id": "665f1e2a9c4d1a0012ab34cd", "changes": {}}'
        )

        self.assertFalse(result["ok"])
        self.assertEqual(recorder.requests, [])


class OptionalIntegrationTests(unittest.TestCase):
    """Expense tracking is opt-in: tools and prompt appear only when configured."""

    def _reload(self, module):
        reloaded = importlib.reload(module)
        self.addCleanup(importlib.reload, module)  # restore real config state
        return reloaded

    def test_tools_are_hidden_when_not_configured(self):
        from src.tools import registry

        with patch.object(config, "EXPENSE_TRACKER_ENABLED", False):
            reloaded = self._reload(registry)

        names = [d["function"]["name"] for d in reloaded.TOOL_DEFINITIONS]
        self.assertNotIn("create_expense", names)
        self.assertNotIn("create_expense", reloaded.TOOL_EXECUTORS)
        self.assertIn("schedule_task", names)  # reminders stay available

    def test_tools_are_registered_when_configured(self):
        from src.tools import registry

        with patch.object(config, "EXPENSE_TRACKER_ENABLED", True):
            reloaded = self._reload(registry)

        names = [d["function"]["name"] for d in reloaded.TOOL_DEFINITIONS]
        for tool in ("create_expense", "list_expenses", "delete_expense"):
            self.assertIn(tool, names)
            self.assertIn(tool, reloaded.TOOL_EXECUTORS)

    def test_prompt_omits_expense_guidance_when_not_configured(self):
        from src.prompts import system

        with patch.object(config, "EXPENSE_TRACKER_ENABLED", False):
            reloaded = self._reload(system)

        self.assertNotIn("create_expense", reloaded.SYSTEM_PROMPT)
        self.assertIn("reminder", reloaded.SYSTEM_PROMPT)


if __name__ == "__main__":
    unittest.main()
