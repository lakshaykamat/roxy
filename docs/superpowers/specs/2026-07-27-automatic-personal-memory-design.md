# Automatic Personal Memory Design

## Goal

Automatically save durable facts the user shares while keeping ordinary chat out
of the Brain. Emit complete application logs for received text, sent replies,
tool calls, and tool results.

## Scope

For each text message, Roxy performs a structured memory decision independently
from normal tool routing. It extracts zero or more facts only when they describe
the user: identity, preferences, work, relationships, projects, goals, habits,
or long-lived personal context. It skips greetings, questions, temporary status,
requests, small talk, and assistant content.

Each accepted fact is persisted with `capture_mode="automatic"` and a
message-derived capture key, making repeated delivery/retry idempotent. Automatic
memory respects the existing paused-capture setting. A failed decision or save is
logged and never delays or prevents the normal chat reply.

Logs record full user text, full assistant reply, intent choice, tool name and
arguments, and complete tool result. These logs are deliberately sensitive and
must stay local to the process log destination.

## Data Flow

`submit_chat_text` stores the conversational message, queues the normal reply,
and starts a best-effort automatic-memory task. `src.core.llm` requests a strict
JSON response containing a list of memory candidates. A memory service validates
the response and saves each candidate through the established analyzed Brain save
path. The chat/debounce path is unchanged except for full-content logging.

## Error Handling and Testing

All failure handling uses `src.core.errors`. The memory task returns without
affecting a reply when classification or persistence fails. Tests cover a
durable fact, no-memory chat, multiple facts, duplicate delivery, paused capture,
failure isolation, and all required log entries.
