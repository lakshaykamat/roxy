from collections.abc import Awaitable, Callable

from src import config
from src.tools import expenses, manage_tasks, schedule_task

# Executors return a result dict, or a coroutine resolving to one (async tools).
ToolResult = dict[str, object]
ToolExecutor = Callable[[str], ToolResult | Awaitable[ToolResult]]

TOOL_DEFINITIONS = [
    schedule_task.DEFINITION,
    manage_tasks.DEFINITION,
]
TOOL_EXECUTORS: dict[str, ToolExecutor] = {
    "schedule_task": schedule_task.execute,
    "manage_reminders": manage_tasks.execute,
}

TOOL_INTENTS = {
    "reminders": {
        "description": "Create, list, change, remove, or clear Roxy reminders.",
        "tool_names": frozenset({"schedule_task", "manage_reminders"}),
    },
    "expenses": {
        "description": "Record, list, inspect, update, or delete personal expenses.",
        "tool_names": frozenset(
            {
                "create_expense",
                "list_expenses",
                "get_expense",
                "update_expense",
                "delete_expense",
            }
        ),
    },
}

# Expense tracking is optional. Only advertise its tools to the LLM when an API
# key is configured, so users without a tracker never see broken actions.
if config.EXPENSE_TRACKER_ENABLED:
    TOOL_DEFINITIONS += [
        expenses.CREATE_DEFINITION,
        expenses.LIST_DEFINITION,
        expenses.GET_DEFINITION,
        expenses.UPDATE_DEFINITION,
        expenses.DELETE_DEFINITION,
    ]
    TOOL_EXECUTORS.update(
        {
            "create_expense": expenses.create_expense,
            "list_expenses": expenses.list_expenses,
            "get_expense": expenses.get_expense,
            "update_expense": expenses.update_expense,
            "delete_expense": expenses.delete_expense,
        }
    )


def execute_tool_call(name: str, arguments: str) -> ToolResult | Awaitable[ToolResult]:
    executor = TOOL_EXECUTORS.get(name)
    if executor is None:
        return {"ok": False, "error": "That action is not available."}
    return executor(arguments)


def available_tool_intents() -> dict[str, str]:
    available_names = {definition["function"]["name"] for definition in TOOL_DEFINITIONS}
    return {
        intent: details["description"]
        for intent, details in TOOL_INTENTS.items()
        if details["tool_names"].issubset(available_names)
    }


def tool_definitions_for_intent(intent: str) -> list[object]:
    details = TOOL_INTENTS.get(intent)
    if details is None:
        return TOOL_DEFINITIONS

    return [
        definition
        for definition in TOOL_DEFINITIONS
        if definition["function"]["name"] in details["tool_names"]
    ]
