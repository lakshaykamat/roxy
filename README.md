# Roxy

<p align="center">
  <img src="assets/roxy.png" alt="Roxy" width="260">
</p>

Roxy is a warm, witty, and discreet personal AI assistant for Telegram. She
offers concise, direct conversation in a familiar tone while keeping the
experience intentionally personal: only the configured Telegram user can
interact with her.

Powered by OpenAI, Roxy retains conversation history in a local SQLite
database so she can respond with continuity across messages. Her voice,
instructions, and model can be tailored to suit the way you work.

## Requirements

- [Python](https://www.python.org/) 3.14 or later
- [uv](https://docs.astral.sh/uv/)
- A Telegram bot token
- An OpenAI API key

## Quick start

Clone the repository and install the locked dependencies:

```bash
git clone https://github.com/lakshaykamat/roxy.git
cd roxy
uv sync
```

Create your local configuration file:

```bash
cp .env.example .env
```

Set the following values in `.env`:

```env
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
OPENAI_API_KEY=your_openai_api_key_here
ALLOWED_USER_ID=123456789
TASK_TIMEZONE=Asia/Kolkata
```

| Variable | Description |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | Create a bot through [@BotFather](https://t.me/BotFather) on Telegram. |
| `OPENAI_API_KEY` | Create an API key in the [OpenAI Platform](https://platform.openai.com/api-keys). |
| `ALLOWED_USER_ID` | Your numeric Telegram user ID. You can retrieve it with [@userinfobot](https://t.me/userinfobot). |
| `TASK_TIMEZONE` | IANA timezone used when a reminder has no timezone. Defaults to `Asia/Kolkata`. |

Start the bot:

```bash
uv run python main.py
```

While the bot is polling, its health endpoint is available at
`http://127.0.0.1:8000/health`:

```json
{"status": "ok"}
```

## Development

`uv` manages the project environment and reproduces dependencies from
`uv.lock`. After changing dependencies, run `uv lock` and commit the updated
lockfile with `pyproject.toml`.

Run the test suite with:

```bash
uv run python -m unittest discover -s tests -v
```

## Commands

| Command | Description |
| --- | --- |
| `/start` | Starts a conversation with Roxy. |
| `/tasks` | Lists active reminders. |
| `/done <id>` | Marks an active reminder complete. |

## Scheduled reminders

Ask Roxy to create a one-time or recurring reminder. She supports daily,
weekly, and monthly schedules and saves them in `roxy.db`. Run the Telegram bot
and reminder worker as separate long-running processes:

```bash
uv run python main.py
uv run python reminder_worker.py
```

Use a process manager such as systemd or Docker Compose to restart both
processes if they stop. Run one reminder worker unless you have validated the
SQLite lease behavior for multiple workers.

## Configuration and customization

- Update `src/prompts/system.py` to change Roxy's personality and behavior.
- Update `src/config.py` to select a different OpenAI model or adjust shared
  configuration.
- Conversation messages are stored in `roxy.db` in the project directory.

Keep `.env` and `roxy.db` private. Both are excluded from version control by
default.

## Project layout

```text
main.py                 Application entry point and Telegram access guard
src/config.py           Environment configuration
src/handlers/           Telegram command and chat handlers
src/prompts/system.py   Roxy's system prompt
src/utils/history.py    SQLite-backed conversation history
tests/                  Automated tests
```
