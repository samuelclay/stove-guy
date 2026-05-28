"""Colorised request/response logging for the API surface.

A tiny pure-ASGI middleware that prints, for every ``/api/*`` call, the request
body that came in and the JSON body that went back out — so tailing the log
shows what's flowing between the browser / Tavus persona and the server.

ANSI colour is written straight into the log file; ``tail -f`` passes the codes
through so the terminal renders them. Streaming endpoints (the MJPEG preview)
and the WebSocket are left untouched.
"""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Awaitable, Callable

# --- ANSI -------------------------------------------------------------------
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
CYAN = "\033[36m"
GREY = "\033[90m"

_METHOD_COLOR = {
    "GET": BLUE,
    "POST": YELLOW,
    "PUT": YELLOW,
    "PATCH": YELLOW,
    "DELETE": RED,
}

MAX_BODY = 2000   # chars; bodies past this are truncated in the log


def _status_color(status: int) -> str:
    if status >= 500:
        return RED
    if status >= 400:
        return YELLOW
    if status >= 300:
        return CYAN
    return GREEN


def _format_body(raw: bytes, content_type: str) -> str | None:
    """Pretty-print a body for the log, or None if there's nothing to show."""
    if not raw:
        return None
    if "multipart/form-data" in content_type:
        return f"<multipart form, {len(raw)} bytes>"
    text = raw.decode("utf-8", "replace")
    if "application/json" in content_type or text[:1] in "{[":
        try:
            text = json.dumps(json.loads(text), indent=2, ensure_ascii=False)
        except ValueError:
            pass
    if len(text) > MAX_BODY:
        text = text[:MAX_BODY] + f"\n… (+{len(text) - MAX_BODY} more chars)"
    return text


def _emit_block(text: str) -> None:
    for line in text.splitlines():
        print(f"{GREY}    {line}{RESET}", file=sys.stdout)


class TrafficLogMiddleware:
    """Logs request/response bodies for ``/api/*`` (minus the MJPEG stream)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive: Callable, send: Callable):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope["method"]
        path = scope.get("path", "")
        if not path.startswith("/api/") or path == "/api/preview":
            await self.app(scope, receive, send)
            return

        req_headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}

        # Buffer the whole request body so we can both log it and replay it
        # untouched to the route handler.
        body = b""
        more = True
        while more:
            message = await receive()
            if message["type"] == "http.request":
                body += message.get("body", b"")
                more = message.get("more_body", False)
            else:  # http.disconnect
                more = False

        mc = _METHOD_COLOR.get(method, CYAN)
        print(f"{BOLD}{mc}→ {method}{RESET} {CYAN}{path}{RESET}", file=sys.stdout)
        req_body = _format_body(body, req_headers.get("content-type", ""))
        if req_body:
            _emit_block(req_body)
        sys.stdout.flush()

        replayed = False

        async def replay():
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return {"type": "http.disconnect"}

        started = time.perf_counter()
        status = 0
        resp_ctype = ""
        capture = False
        captured = bytearray()

        async def send_wrapper(message):
            nonlocal status, resp_ctype, capture
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = {k.decode().lower(): v.decode() for k, v in message.get("headers", [])}
                resp_ctype = headers.get("content-type", "")
                capture = "application/json" in resp_ctype or resp_ctype.startswith("text/")
            elif message["type"] == "http.response.body":
                if capture and len(captured) < MAX_BODY * 4:
                    captured.extend(message.get("body", b""))
                if not message.get("more_body", False):
                    ms = (time.perf_counter() - started) * 1000
                    sc = _status_color(status)
                    print(
                        f"{sc}← {status}{RESET} {CYAN}{path}{RESET} {DIM}{ms:.0f}ms{RESET}",
                        file=sys.stdout,
                    )
                    res_body = _format_body(bytes(captured), resp_ctype) if capture else None
                    if res_body:
                        _emit_block(res_body)
                    sys.stdout.flush()
            await send(message)

        await self.app(scope, replay, send_wrapper)


def enabled() -> bool:
    """Off only if explicitly disabled via STOVE_TRAFFIC_LOG=0."""
    return os.environ.get("STOVE_TRAFFIC_LOG", "1") != "0"
