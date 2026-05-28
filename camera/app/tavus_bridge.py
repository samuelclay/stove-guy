"""Server-side Daily bridge into a Tavus CVI conversation.

CVI interaction events (``conversation.respond`` etc.) have no REST endpoint —
they travel over the Daily data channel. So to let the *server* push narration
into a live conversation (the persona then reacts in its own words), we join the
conversation's Daily room as a silent participant and send app-messages.

The whole module degrades gracefully if ``daily-python`` isn't installed.
"""
from __future__ import annotations

import json
import threading

try:
    from daily import CallClient, Daily, EventHandler

    _AVAILABLE = True
except Exception:  # pragma: no cover - daily-python optional
    _AVAILABLE = False


if _AVAILABLE:

    class _BridgeEvents(EventHandler):
        """Tracks the replica's speaking state (so the server can avoid
        auto-advancing while it's talking) and logs conversation events."""

        def __init__(self, bridge: "ConversationBridge") -> None:
            super().__init__()
            self._bridge = bridge

        def on_app_message(self, message, sender=None) -> None:
            try:
                if not isinstance(message, dict):
                    return
                if message.get("message_type") != "conversation":
                    return
                et = message.get("event_type", "?")
                if et == "conversation.replica.started_speaking":
                    self._bridge._replica_speaking = True
                elif et == "conversation.replica.stopped_speaking":
                    self._bridge._replica_speaking = False
                elif et == "conversation.tool_call":
                    props = message.get("properties") or {}
                    name = props.get("name")
                    raw = props.get("arguments")
                    args = None
                    if isinstance(raw, dict):
                        args = raw
                    elif isinstance(raw, str):
                        try:
                            args = json.loads(raw)
                        except Exception:
                            args = None
                    if name == "set_action" and isinstance(args, dict):
                        label = args.get("label")
                        if isinstance(label, str) and label.strip():
                            self._bridge._dynamic_action = label.strip()[:48]
                if any(k in et for k in ("utterance", "speaking", "tool", "error", "replica")):
                    snippet = str(message.get("properties", {}))[:240]
                    print(f"[bridge<-] {et} {snippet}", flush=True)
            except Exception:
                pass


class ConversationBridge:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._client = None
        self._conversation_id: str | None = None
        self._url: str | None = None
        self._joined = False
        self._inited = False
        self._replica_speaking = False
        self._dynamic_action: str | None = None
        self._handler = _BridgeEvents(self) if _AVAILABLE else None

    def replica_speaking(self) -> bool:
        """True while the persona is mid-utterance (between started_speaking and
        stopped_speaking). Used to hold auto-advance so the deck doesn't race
        ahead of the replica."""
        return bool(self._replica_speaking)

    def dynamic_action(self) -> str | None:
        """The last action label the persona pushed via the set_action tool —
        i.e. what it just told the user to do. The presentation UI uses this as
        the show-mode button label, so the button reflects what the replica
        actually suggested rather than a deck-baked label."""
        return self._dynamic_action

    def clear_dynamic_action(self) -> None:
        self._dynamic_action = None

    def status(self) -> dict:
        with self._lock:
            return {
                "available": _AVAILABLE,
                "connected": self._client is not None,
                "joined": self._joined,
                "conversationId": self._conversation_id,
                "url": self._url,
            }

    def connect(self, url: str, conversation_id: str) -> dict:
        if not _AVAILABLE:
            raise RuntimeError("daily-python is not installed in this venv")
        with self._lock:
            if not self._inited:
                Daily.init()
                self._inited = True
            if self._client is not None:
                try:
                    self._client.leave()
                except Exception:
                    pass
            self._client = None
            self._joined = False
            self._replica_speaking = False
            self._dynamic_action = None

            client = CallClient(event_handler=self._handler) if self._handler else CallClient()
            try:
                client.set_user_name("Stove Guy (camera)")
            except Exception:
                pass

            def _on_joined(*_args) -> None:
                with self._lock:
                    self._joined = True

            client.join(url, completion=_on_joined)
            self._client = client
            self._url = url
            self._conversation_id = conversation_id
            return {"connected": True, "conversationId": conversation_id, "url": url}

    def disconnect(self) -> None:
        with self._lock:
            if self._client is not None:
                try:
                    self._client.leave()
                except Exception:
                    pass
            self._client = None
            self._joined = False
            self._replica_speaking = False
            self._dynamic_action = None
            self._conversation_id = None
            self._url = None

    def respond(self, text: str) -> bool:
        """Push a ``conversation.respond`` — the persona reacts as if the user
        had just said ``text``. Best-effort; returns False if not connected."""
        return self._send("conversation.respond", {"text": text})

    def echo(self, text: str) -> bool:
        """Push a ``conversation.echo`` — the persona speaks ``text`` verbatim."""
        return self._send("conversation.echo", {"modality": "text", "text": text, "done": True})

    def append_context(self, text: str) -> bool:
        """Push a ``conversation.append_llm_context`` — silently makes the persona
        aware of ``text`` without forcing it to speak."""
        return self._send("conversation.append_llm_context", {"context": text})

    def _send(self, event_type: str, properties: dict) -> bool:
        with self._lock:
            client = self._client
            cid = self._conversation_id
        if client is None or not cid:
            return False
        msg = {
            "message_type": "conversation",
            "event_type": event_type,
            "conversation_id": cid,
            "properties": properties,
        }
        try:
            client.send_app_message(msg)
            return True
        except Exception:
            return False
