# SQLite Message Storage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist Roxy's recent conversation messages in a local SQLite database.

**Architecture:** `src/utils/history.py` owns the database schema and the small read/write interface used by handlers. It creates `roxy.db` on demand, writes messages synchronously with `sqlite3`, and reads the newest 40 messages before restoring chronological order for OpenAI.

**Tech Stack:** Python 3.14+, standard-library `sqlite3`, python-telegram-bot, OpenAI Python SDK.

## Global Constraints

- Use Python's built-in `sqlite3`; add no dependencies.
- Keep the most recent 20 conversation turns, or 40 messages, in OpenAI context.
- Remove `/reset`; `/start` must not delete saved history.
- Prefer clear single-purpose functions and language-native conventions.

---

### Task 1: SQLite-backed history utility

**Files:**
- Modify: `src/utils/history.py`
- Test: `tests/test_history.py`

**Interfaces:**
- Produces: `add(role: str, content: str) -> None` and `get() -> list[dict[str, str]]`.
- Produces: `DATABASE_PATH: Path`, overridable in tests.

- [ ] **Step 1: Write the failing tests**

```python
from src.utils import history


def test_get_returns_saved_messages_in_order(tmp_path, monkeypatch):
    monkeypatch.setattr(history, "DATABASE_PATH", tmp_path / "roxy.db")
    history.add("user", "Hello")
    history.add("assistant", "Hi")
    assert history.get() == [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi"},
    ]


def test_get_limits_history_to_forty_messages(tmp_path, monkeypatch):
    monkeypatch.setattr(history, "DATABASE_PATH", tmp_path / "roxy.db")
    for number in range(41):
        history.add("user", str(number))
    assert [message["content"] for message in history.get()] == [str(number) for number in range(1, 41)]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m unittest discover -s tests -p 'test_history.py' -v`

Expected: FAIL because the current in-memory implementation does not define `DATABASE_PATH`.

- [ ] **Step 3: Replace the list with the minimal SQLite implementation**

```python
DATABASE_PATH = Path("roxy.db")
MAX_MESSAGES = 40


def add(role: str, content: str) -> None:
    with sqlite3.connect(DATABASE_PATH) as connection:
        connection.execute(CREATE_MESSAGES_TABLE)
        connection.execute("INSERT INTO messages (role, content) VALUES (?, ?)", (role, content))


def get() -> list[dict[str, str]]:
    with sqlite3.connect(DATABASE_PATH) as connection:
        connection.execute(CREATE_MESSAGES_TABLE)
        rows = connection.execute(
            "SELECT role, content FROM messages ORDER BY id DESC LIMIT ?", (MAX_MESSAGES,)
        ).fetchall()
    return [{"role": role, "content": content} for role, content in reversed(rows)]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m unittest discover -s tests -p 'test_history.py' -v`

Expected: PASS with two passing tests.

- [ ] **Step 5: Commit the utility and tests**

```bash
git add src/utils/history.py tests/test_history.py
git commit -m "feat: persist chat history in SQLite"
```

### Task 2: Remove reset behavior and update the bot entry points

**Files:**
- Modify: `main.py`
- Modify: `src/handlers/commands.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: `history.add()` and `history.get()` remain unchanged for `src/handlers/chat.py`.
- Produces: only the `/start` command is registered.

- [ ] **Step 1: Write the failing source-level regression check**

```python
from pathlib import Path


def test_reset_command_is_not_registered_or_documented():
    assert 'CommandHandler("reset"' not in Path("main.py").read_text()
    assert "def reset" not in Path("src/handlers/commands.py").read_text()
    assert "| `/reset`" not in Path("README.md").read_text()
```

- [ ] **Step 2: Run the check to verify it fails**

Run: `python -m unittest discover -s tests -p 'test_commands.py' -v`

Expected: FAIL because `/reset` is still registered and documented.

- [ ] **Step 3: Remove reset and preserve history on start**

```python
# main.py
from src.handlers.commands import start

app.add_handler(CommandHandler("start", allowed_only(start)))

# src/handlers/commands.py
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Hey! I'm Roxy 👋 What's on your mind?")
```

Remove the `/reset` table row and the phrase that says `/start` resets the conversation from `README.md`.

- [ ] **Step 4: Run the check and syntax verification**

Run: `python -m unittest discover -s tests -p 'test_commands.py' -v && python -m compileall main.py src`

Expected: PASS and successful compilation.

- [ ] **Step 5: Commit command and documentation changes**

```bash
git add main.py src/handlers/commands.py README.md tests/test_commands.py
git commit -m "feat: remove reset command"
```
