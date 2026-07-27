from collections.abc import Awaitable, Callable

from src import config
from src.expenses import tools as expenses
from src.reminders import create_tool, manage_tool
from src.knowledge import brain_tools

# Executors return a result dict, or a coroutine resolving to one (async tools).
ToolResult = dict[str, object]
ToolExecutor = Callable[[str], ToolResult | Awaitable[ToolResult]]

TOOL_DEFINITIONS = [
    create_tool.DEFINITION,
    manage_tool.DEFINITION,
    *brain_tools.DEFINITIONS,
]
TOOL_EXECUTORS: dict[str, ToolExecutor] = {
    "schedule_task": create_tool.execute,
    "manage_reminders": manage_tool.execute,
    "search_brain": brain_tools.search_brain,
    "archive_brain_item": brain_tools.archive_brain_item,
    "delete_brain_item": brain_tools.delete_brain_item,
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
    "brain": {
        "description": "Automatically save a durable idea, fact, preference, person, project, goal, decision, reference, or reflection.",
        "tool_names": frozenset({"save_brain_item"}),
    },
    "brain_management": {
        "description": "Search, archive, or delete an already saved brain item.",
        "tool_names": frozenset({"search_brain", "archive_brain_item", "delete_brain_item"}),
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


def execute_tool_call(
    name: str, arguments: str, *, capture_key: str | None = None,
    source_content: str | None = None,
) -> ToolResult | Awaitable[ToolResult]:
    if name == "save_brain_item":
        return brain_tools.save_brain_item(
            arguments, capture_key=capture_key, source_content=source_content
        )
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
