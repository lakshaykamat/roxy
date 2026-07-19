MAX_TURNS = 20

history: list[dict] = []


def add(role: str, content: str):
    history.append({"role": role, "content": content})
    if len(history) > MAX_TURNS * 2:
        del history[:2]


def get() -> list[dict]:
    return history


def clear():
    history.clear()
