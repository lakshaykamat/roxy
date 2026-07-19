# SQLite Message Storage Design

## Goal

Persist Roxy's Telegram conversation history locally so it remains available after the bot restarts.

## Scope

- Replace the in-memory message list with a SQLite database.
- Store each user and assistant message with its role, content, and creation time.
- Send only the most recent 20 conversation turns to OpenAI.
- Remove the `/reset` command and its documentation.
- Make `/start` greet the user without deleting stored messages.

## Approach

Use Python's built-in `sqlite3` module. The application will create a local `roxy.db` file automatically on startup or first message access, with no new packages or configuration required.

The history utility will have focused functions to initialize the database, save a message, and retrieve recent messages in chronological order. SQLite connections will be opened for each operation and closed immediately afterward, keeping the functions straightforward and avoiding shared mutable state.

## Data Model

The database contains one `messages` table:

| Column | Type | Purpose |
| --- | --- | --- |
| `id` | integer | Primary key and chronological ordering |
| `role` | text | OpenAI message role: `user` or `assistant` |
| `content` | text | Message body |
| `created_at` | text | UTC creation timestamp |

## Message Flow

1. A user sends a text message.
2. The chat handler saves it to SQLite.
3. The handler loads the most recent 40 messages, representing 20 user-assistant turns.
4. The system prompt and loaded history are sent to OpenAI.
5. The assistant reply is saved to SQLite and sent to Telegram.

## Error Handling

Database setup and access use the standard `sqlite3` error types. Failures will be logged with context and allowed to propagate so the bot does not silently produce incomplete conversation history. OpenAI and Telegram error behavior remains unchanged.

## Verification

- Run a lightweight local test against a temporary database to confirm initialization, insertion, chronological retrieval, and the 40-message limit.
- Run a syntax/import check after integrating the handlers.
- Confirm `/reset` is no longer registered or documented.
