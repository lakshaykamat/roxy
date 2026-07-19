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
```

| Variable | Description |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | Create a bot through [@BotFather](https://t.me/BotFather) on Telegram. |
| `OPENAI_API_KEY` | Create an API key in the [OpenAI Platform](https://platform.openai.com/api-keys). |
| `ALLOWED_USER_ID` | Your numeric Telegram user ID. You can retrieve it with [@userinfobot](https://t.me/userinfobot). |

Start the bot:

```bash
uv run python main.py
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
