import json

from src.utils import memory
from src.utils.errors import try_catch

DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "save_memory",
            "description": "Save a user-approved memory.",
            "parameters": {
                "type": "object",
                "properties": {"content": {"type": "string"}, "kind": {"type": "string"}},
                "required": ["content"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_memories",
            "description": "Search user-approved memories.",
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_memory",
            "description": "Delete one saved memory.",
            "parameters": {
                "type": "object",
                "properties": {"id": {"type": "integer"}},
                "required": ["id"],
                "additionalProperties": False,
            },
        },
    },
]


def _arguments(arguments: str) -> dict[str, object]:
    values = json.loads(arguments)
    if not isinstance(values, dict):
        raise ValueError("Tool arguments must be an object.")
    return values


def _error_result(error: BaseException) -> dict[str, object]:
    return {"ok": False, "error": str(error)}


async def save_memory(arguments: str) -> dict[str, object]:
    def save() -> dict[str, object]:
        values = _arguments(arguments)
        item = memory.create_memory(values.get("content", ""), str(values.get("kind", "fact")))
        return {
            "ok": True,
            "memory": {"id": item.id, "kind": item.kind, "content": item.content},
        }

    return try_catch(save, handle_error=_error_result)


async def search_memories(arguments: str) -> dict[str, object]:
    def search() -> dict[str, object]:
        values = _arguments(arguments)
        items = memory.find_relevant_memories(str(values.get("text", "")))
        return {
            "ok": True,
            "memories": [
                {"id": item.id, "kind": item.kind, "content": item.content}
                for item in items
            ],
        }

    return try_catch(search, handle_error=_error_result)


async def delete_memory(arguments: str) -> dict[str, object]:
    def delete() -> dict[str, object]:
        memory_id = _arguments(arguments).get("id")
        if not isinstance(memory_id, int) or isinstance(memory_id, bool):
            raise ValueError("Memory ID must be a whole number.")
        return {"ok": memory.delete_memory(memory_id), "id": memory_id}

    return try_catch(delete, handle_error=_error_result)
