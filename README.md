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

Roxy also understands Telegram voice notes. She transcribes them internally and
handles them exactly like typed messages, including Hindi, English, Hinglish,
reminders, and expense requests. The transcript is not automatically shown in
her reply.

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
DASHBOARD_PASSWORD=choose_a_long_unique_password
DASHBOARD_SESSION_SECRET=generate_a_long_random_secret
```

| Variable | Description |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | Create a bot through [@BotFather](https://t.me/BotFather) on Telegram. |
| `OPENAI_API_KEY` | Create an API key in the [OpenAI Platform](https://platform.openai.com/api-keys). |
| `WEB_SEARCH_MODEL` | Optional OpenAI model used for cited web research. Defaults to Roxy's OpenAI model. |
| `OPENAI_TRANSCRIPTION_MODEL` | Optional OpenAI model for Telegram voice-note transcription. Defaults to `gpt-4o-mini-transcribe`. |
| `ALLOWED_USER_ID` | Your numeric Telegram user ID. You can retrieve it with [@userinfobot](https://t.me/userinfobot). |
| `TASK_TIMEZONE` | IANA timezone used when a reminder has no timezone. Defaults to `Asia/Kolkata`. |

Start the bot:

```bash
uv run python main.py
```

While the bot is polling, its health endpoint is available at
`http://127.0.0.1:8888/health`:

```json
{"status": "ok"}
```

## Dashboard

Roxy provides a read-only dashboard at http://127.0.0.1:8888/. Set
`DASHBOARD_PASSWORD` and a long random `DASHBOARD_SESSION_SECRET` in `.env`,
then sign in through `/login`.

For network access, terminate HTTPS at a reverse proxy and set
`DASHBOARD_SECURE_COOKIES=true`. Do not expose the dashboard directly to the
internet over plain HTTP. The dashboard shows aggregate activity and
operational state only; it never displays retained chat history.

After signing in, open `/brain` for active saved items and their source links.
The matching authenticated JSON data is available at `/brain-data`.

The Brain page can archive an active item immediately. Permanent deletion
requires an explicit in-page confirmation and the item's exact active title.

## Brain sources and research

Ask Roxy to save a thought, a public link, or a list of links. Each request and
each link are saved directly in the local Brain.

You can also ask natural-language questions such as “find current SQLite FTS5
guidance.” Roxy's web research returns cited results but does not save them
unless you explicitly ask.

Normal chat uses one model completion. A tool action uses two completions: one
to request the tool and one to reply after its result. Multi-step tool loops use
one additional completion per tool-result round.

Health and readiness endpoints remain public for orchestration. Restrict their
network access at your deployment boundary when appropriate.

## Development

`uv` manages the project environment and reproduces dependencies from
`uv.lock`. After changing dependencies, run `uv lock` and commit the updated
lockfile with `pyproject.toml`.

Run the test suite with:

```bash
uv run python -m unittest discover -s tests -v
```

## Keyboard actions

Use `/start` once to show Roxy's persistent keyboard. After that, use its buttons:

| Button | Description |
| --- | --- |
| 📅 My tasks | Lists active reminders and shows a Done button for each one. |
| 🧠 My brain | Lists the 20 newest active brain items. |
| 📦 Export my data | Sends a JSON export of local messages, brain items, and scheduled deliveries. |
| 🗑 Delete my data | Opens a keyboard confirmation before permanently deleting local data. |
| ℹ️ Help | Explains the available keyboard actions. |

## Privacy and service health

Roxy can capture durable ideas, facts, preferences, people, projects, goals,
decisions, references, and reflections, including sensitive durable facts. It
does not capture casual chat, expenses alone, or content marked “don't save.”
Direct save requests
still work.
Tasks are brain items, while their notification attempts are stored separately.
`/ready` returns `503` when the bot database is unavailable; `/health` is
liveness only.

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

## Expense tracking

Roxy can record and review your spending through the
[expense tracker API](https://busty-expense-tracker-api.vercel.app) using plain
conversation. Talk to her naturally:

- "I spent ₹450 on dinner tonight."
- "Add 1200 for groceries yesterday."
- "Show my expenses for July."
- "How much did I spend by category this month?"
- "Change the coffee expense to ₹250."
- "Delete my latest Uber expense." (Roxy confirms before deleting.)

She extracts the title, amount, category, description, and date, retrieves the
current supported categories from the expense tracker, selects the best match,
and resolves relative dates
("yesterday", "last Friday", "this month") in your timezone. Deletion always
asks for explicit confirmation, and when a request matches more than one expense
she lists the candidates so you can pick one.

### Supported categories

Roxy gets the current category list from the tracker’s `GET /api/v1/expenses/categories`
endpoint. This endpoint is the source of truth, so category changes in the
tracker are available without a bot update.

### Configuration

Expense tracking is **optional**. Roxy only offers the expense tools when
`EXPENSE_TRACKER_API_KEY` is set; without it she runs as before (chat and
reminders only) and never advertises a feature she cannot use.

To enable it, set these values in `.env` (see `.env.example`):

```env
EXPENSE_TRACKER_API_KEY=your_api_key_here
EXPENSE_TRACKER_BASE_URL=https://busty-expense-tracker-api.vercel.app
DEFAULT_CURRENCY=INR
```

| Variable | Description |
| --- | --- |
| `EXPENSE_TRACKER_API_KEY` | Sent as the `x-api-key` header on every request. Never logged. |
| `EXPENSE_TRACKER_BASE_URL` | Optional. Defaults to the hosted API URL above. |
| `DEFAULT_CURRENCY` | Optional. Currency used when formatting amounts. Defaults to `INR`. Amounts are stored as plain numbers; Roxy never converts currencies. |

Amounts are stored without a currency field, so `DEFAULT_CURRENCY` only affects
how Roxy displays them.

### Example conversations

```text
You:  I spent ₹450 on dinner tonight.
Roxy: Added ₹450 for Dinner under Food for July 20.

You:  Show my expenses for July.
Roxy: Your latest expenses for July:
      1. Dinner — ₹450 — Food — July 20
      2. Uber — ₹320 — Transportation — July 19
      Total: ₹770

You:  How much did I spend by category this month?
Roxy: This month:
      Food: ₹2,480
      Fast Food: ₹1,100
      Transportation: ₹1,260
      Total: ₹4,840

You:  Change the coffee expense to ₹250.
Roxy: Updated Coffee from ₹180 to ₹250.

You:  Delete my latest Uber expense.
Roxy: I found "Uber ride — ₹620 — Transportation — July 19". Should I permanently delete it?
You:  Yes.
Roxy: Deleted "Uber ride" for ₹620.
```

## Docker

Docker Compose runs the Telegram bot and the reminder worker together in one
container. If either process exits, the container exits and Compose restarts
it. The SQLite database is stored in the named `roxy_data` volume.

Create `.env` as shown above, then start Roxy:

```bash
docker compose up --build -d
```

Check its health and logs:

```bash
docker compose ps
curl http://127.0.0.1:8888/health
docker compose logs -f roxy
```

To stop it without deleting the persisted database:

```bash
docker compose down
```

To remove the database as well, run `docker compose down --volumes`.

## Configuration and customization

- Update `src/prompts/system.py` to change Roxy's personality and behavior.
- Update `src/config.py` to select a different OpenAI model or adjust shared
  configuration.
- Native runs store conversation messages in `roxy.db` in the project
  directory. Docker runs store them in the `roxy_data` volume at
  `/app/data/roxy.db`.

Keep `.env` and `roxy.db` private. Both are excluded from version control by
default.

## Project layout

```text
main.py                 Application entry point and Telegram access guard
src/config.py           Environment configuration
src/handlers/           Telegram command and chat handlers
src/prompts/system.py   Roxy's system prompt
src/tools/              LLM tool definitions and handlers (reminders, expenses)
src/services/           Expense tracker HTTP client, models, and errors
src/utils/history.py    SQLite-backed conversation history
src/utils/dates.py      Relative-date parsing for expenses
tests/                  Automated tests
```

The expense integration keeps responsibilities separate:

```text
src/services/expense_tracker_client.py  Async httpx client (connection reuse, timeouts)
src/services/expense_models.py          Typed models, validation, and matching
src/services/expense_errors.py          Application-specific exceptions
src/tools/expenses.py                   LLM tool schemas and handlers
src/utils/expense_formatting.py         Currency and response formatting
src/utils/expense_state.py              Conversation state (matches, delete confirmation)
```
