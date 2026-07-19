#!/usr/bin/env bash

set -euo pipefail

shutdown() {
    kill -TERM "$bot_pid" "$worker_pid" 2>/dev/null || true
}

python main.py &
bot_pid=$!
python reminder_worker.py &
worker_pid=$!

trap shutdown SIGINT SIGTERM

set +e
wait -n "$bot_pid" "$worker_pid"
exit_code=$?
set -e

shutdown
wait "$bot_pid" "$worker_pid" 2>/dev/null || true

exit "$exit_code"
