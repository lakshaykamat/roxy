from collections.abc import Callable

from src.tools import schedule_task

ToolExecutor = Callable[[str], dict[str, object]]

TOOL_DEFINITIONS = [schedule_task.DEFINITION]
TOOL_EXECUTORS: dict[str, ToolExecutor] = {
    "schedule_task": schedule_task.execute,
}


def execute_tool_call(name: str, arguments: str) -> dict[str, object]:
    executor = TOOL_EXECUTORS.get(name)
    if executor is None:
        return {"ok": False, "error": "That action is not available."}
    return executor(arguments)
