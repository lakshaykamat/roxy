# Roxy 🤖

A simple personal Telegram chatbot powered by OpenAI. Only one user can interact with it.

## Stack

- Python 3.14+
- [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot)
- [openai](https://github.com/openai/openai-python)
- [python-dotenv](https://github.com/theskumar/python-dotenv)

## Project Structure

```
roxy/
├── main.py                  # Entry point + user auth guard
├── src/
│   ├── config.py            # Env vars & constants
│   ├── prompts/
│   │   └── system.py        # Roxy's personality / system prompt
│   ├── utils/
│   │   └── history.py       # Conversation history (add / get / clear)
│   └── handlers/
│       ├── commands.py      # /start, /reset
│       └── chat.py          # Message → OpenAI → reply
├── .env.example
└── .gitignore
```

## Setup

### 1. Clone the repo

```bash
git clone https://github.com/lakshaykamat/roxy.git
cd roxy
```

### 2. Create a virtual environment

```bash
python3 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
```

### 3. Install dependencies

```bash
pip install python-telegram-bot openai python-dotenv
```

### 4. Configure environment variables

```bash
cp .env.example .env
```

Edit `.env`:

```env
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
OPENAI_API_KEY=your_openai_api_key_here
ALLOWED_USER_ID=123456789
```

| Variable | How to get it |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Message [@BotFather](https://t.me/BotFather) on Telegram → `/newbot` |
| `OPENAI_API_KEY` | [platform.openai.com/api-keys](https://platform.openai.com/api-keys) |
| `ALLOWED_USER_ID` | Message [@userinfobot](https://t.me/userinfobot) on Telegram |

### 5. Run

```bash
python main.py
```

## Commands

| Command | Description |
|---|---|
| `/start` | Greet Roxy and reset conversation |
| `/reset` | Wipe memory and start fresh |

## Customising Roxy

Edit `src/prompts/system.py` to change Roxy's personality, tone, or instructions.

Edit `src/config.py` to change the OpenAI model or tweak other constants.
