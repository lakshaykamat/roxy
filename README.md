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

She extracts the title, amount, category, description, and date, selects the
best matching category from the supported list, and resolves relative dates
("yesterday", "last Friday", "this month") in your timezone. Deletion always
asks for explicit confirmation, and when a request matches more than one expense
she lists the candidates so you can pick one.

### Supported categories

Expenses must belong to one of these categories (Roxy selects the best fit
automatically from context):

| Category | Examples |
| --- | --- |
| Food | Coffee, restaurant, lunch, dinner, groceries |
| Fast Food | McDonald's, KFC, pizza delivery, takeaway |
| Health & Fitness | Gym, medicine, doctor, pharmacy |
| Housing | Rent, electricity, internet, furniture |
| Transportation | Uber, taxi, petrol, train fare |
| Financial | Insurance, loan payment, bank fee, investment |
| Family | Family gifts, children's expenses |
| Relationship | Partner gifts, anniversary |
| Personal Care | Salon, haircut, skincare, cosmetics |
| Electronics | Phone, laptop, headphones, gadgets |
| Clothing | Shirt, shoes, jeans, jacket |
| Entertainment | Netflix, cinema, Spotify, concert |
| Education | Books, courses, tuition, coaching |
| Travel | Flights, hotels, vacation, visa |
| Miscellaneous | Anything that doesn't fit another category |

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
      2. Uber — ₹320 — Transport — July 19
      Total: ₹770

You:  How much did I spend by category this month?
Roxy: This month:
      Food: ₹2,480
      Transport: ₹1,260
      Total: ₹3,740

You:  Change the coffee expense to ₹250.
Roxy: Updated Coffee from ₹180 to ₹250.

You:  Delete my latest Uber expense.
Roxy: I found "Uber ride — ₹620 — Transport — July 19". Should I permanently delete it?
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
curl http://127.0.0.1:8000/health
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
