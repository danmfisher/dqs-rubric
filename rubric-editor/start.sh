#!/bin/bash
# FlexGen Rubric Editor — startup script
# Double-click or run from Terminal: bash start.sh

DIR="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="$DIR/.server.pid"
cd "$DIR"

# ── Kill any previous instance ──────────────────────────────
# 1. Use saved PID from last run, if it exists
if [ -f "$PID_FILE" ]; then
  OLD_PID=$(cat "$PID_FILE")
  if kill -0 "$OLD_PID" 2>/dev/null; then
    echo "  Stopping previous instance (PID $OLD_PID)…"
    kill "$OLD_PID" 2>/dev/null
    wait "$OLD_PID" 2>/dev/null   # wait for full exit so socket is released
  fi
  rm -f "$PID_FILE"
fi

# 2. Check whether anything else is still holding the port.
#    We only auto-kill what we know we own (tracked via PID file above).
#    Anything else requires explicit confirmation.
ORPHAN=$(lsof -ti tcp:3737 2>/dev/null)
if [ -n "$ORPHAN" ]; then
  ORPHAN_CMD=$(ps -p "$ORPHAN" -o args= 2>/dev/null)
  echo ""
  echo "  Port 3737 is still held by an unknown process:"
  echo "    PID $ORPHAN — $ORPHAN_CMD"
  echo ""
  printf "  Kill it and continue? [y/N] "
  read -r CONFIRM </dev/tty
  if [ "$CONFIRM" = "y" ] || [ "$CONFIRM" = "Y" ]; then
    kill "$ORPHAN" 2>/dev/null
    sleep 0.5
  else
    echo "  Aborting. Free port 3737 manually and try again."
    exit 1
  fi
fi

# ── Start server ────────────────────────────────────────────
echo ""
echo "  Starting FlexGen Rubric Editor…"
echo ""

python3 server.py &
SERVER_PID=$!
echo "$SERVER_PID" > "$PID_FILE"

sleep 1

# Verify it actually came up before opening the browser
if ! kill -0 "$SERVER_PID" 2>/dev/null; then
  echo "  ERROR: server failed to start. Check server.py output above."
  rm -f "$PID_FILE"
  exit 1
fi

# Open browser
if command -v open >/dev/null 2>&1; then
  open http://localhost:3737
elif command -v xdg-open >/dev/null 2>&1; then
  xdg-open http://localhost:3737
fi

echo "  Server running at http://localhost:3737 (PID $SERVER_PID)"
echo "  Press Enter to stop."
read

# ── Graceful shutdown ───────────────────────────────────────
kill "$SERVER_PID" 2>/dev/null
wait "$SERVER_PID" 2>/dev/null   # block until socket is released
rm -f "$PID_FILE"
echo "  Server stopped."
