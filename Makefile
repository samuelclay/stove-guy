# Stove Guy — dev helpers
SHELL := /bin/bash
LOG   := camera/server.log

.DEFAULT_GOAL := run

# Start the camera server (logging to $(LOG)) and tail the log.
# Ctrl-C stops the tail and the server.
.PHONY: run
run:
	@echo "Starting Stove Guy server -> $(LOG)"
	@trap 'kill $$SERVER_PID 2>/dev/null' EXIT INT TERM; \
	./camera/run.sh > "$(LOG)" 2>&1 & \
	SERVER_PID=$$!; \
	tail -n +1 -f "$(LOG)"

# Tail the log of an already-running server.
.PHONY: logs
logs:
	@tail -n +1 -f "$(LOG)"

# Stop any running server.
.PHONY: stop
stop:
	@pkill -f "uvicorn app.server:app" && echo "stopped" || echo "not running"
