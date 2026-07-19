# Repository Guidelines

## Project Structure & Module Organization

`main.py` is the Telegram bot entry point: it creates the application, registers handlers, and enforces the single-user guard. Keep application configuration in `src/config.py`. Place Telegram-facing behavior in `src/handlers/`, prompt text in `src/prompts/`, and reusable support code in `src/utils/`. Conversation persistence currently lives in `src/utils/history.py` and writes to the local `roxy.db` database. Put automated tests in `tests/`, mirroring the area under test (for example, `tests/test_history.py`). Keep design notes and implementation plans under `docs/superpowers/`.

## Build, Test, and Development Commands

Create and activate a virtual environment, then install the runtime dependencies documented in the README:

```bash
python3 -m venv venv
source venv/bin/activate
pip install python-telegram-bot openai python-dotenv
python main.py
```

Run the complete test suite with:

```bash
python -m unittest discover -s tests -v
```

Use `.env.example` as the template for local configuration. Never commit `.env`, API keys, bot tokens, or a populated `roxy.db`.

## Coding Style & Naming Conventions

Use Python conventions: four-space indentation, `snake_case` for functions and variables, `UPPER_SNAKE_CASE` for module constants, and concise module names. Add type annotations where they clarify public values or interfaces.

Write simple, readable code that favors clarity over cleverness. Choose self-explanatory names and single-responsibility functions. Do not over-engineer for future requirements; keep business logic lean, extract utilities only when an operation is reused, and centralize shared types instead of scattering them. Create abstractions only when needed, avoid circular dependencies, handle errors idiomatically, and log meaningful failures. Prefer self-documenting code—if a comment must explain what code does, rewrite the code for clarity.

Use the shared error-handling utilities in `src/utils/errors.py` for all exception handling. Do not add direct `try`/`except` blocks outside that module; provide the operation and its recovery behavior to the appropriate utility instead.

## Testing Guidelines

Tests use the standard-library `unittest` framework. Name test modules `test_<area>.py`, test classes `<Area>Tests`, and test methods `test_<behavior>`. Add or update focused tests for every behavior change. Isolate filesystem or database effects with temporary paths, as `tests/test_history.py` does, and run the full suite before handing off changes.

## Commit & Pull Request Guidelines

Commit history uses concise Conventional Commit-style subjects such as `feat: persist chat messages in SQLite` and `docs: remove final reset reference`. Use an appropriate prefix (`feat`, `fix`, `docs`, `test`, or `refactor`) and an imperative summary. Pull requests should explain the behavior change, list test commands run, link the relevant issue when one exists, and include screenshots only for user-visible changes.

**Do not commit code or create commits unless the user explicitly asks you to do so.**
