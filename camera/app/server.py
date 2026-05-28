"""FastAPI server: deck library, editor persistence, image import, the
presentation control surface, the live state WebSocket, and the WYSIWYG MJPEG
mirror of the camera output. Bound to localhost only.
"""
from __future__ import annotations

import asyncio
import contextlib
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config
from . import deck as deck_mod
from . import tavus_bridge
from . import traffic_log
from .camera_engine import engine
from .deck import Deck
from .presentation import Presentation

presentation = Presentation(engine)

# --------------------------------------------------------------------------- #
# WebSocket hub
# --------------------------------------------------------------------------- #
_clients: set[WebSocket] = set()


def full_state() -> dict:
    return {"presentation": presentation.state(), "camera": engine.status()}


async def broadcast() -> None:
    if not _clients:
        return
    msg = full_state()
    dead = []
    for ws in list(_clients):
        try:
            await ws.send_json(msg)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _clients.discard(ws)


async def _ticker() -> None:
    """Drives auto-advance countdowns and pushes state ~10x/sec."""
    dt = 0.1
    while True:
        await asyncio.sleep(dt)
        presentation.tick(dt)
        await broadcast()


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    engine.start()
    task = asyncio.create_task(_ticker())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        engine.stop()


app = FastAPI(title="Stove Guy Virtual Camera", lifespan=lifespan)

if traffic_log.enabled():
    app.add_middleware(traffic_log.TrafficLogMiddleware)


# --------------------------------------------------------------------------- #
# Request models
# --------------------------------------------------------------------------- #
class CreateDeckReq(BaseModel):
    name: str


class FromPathReq(BaseModel):
    path: str


class LoadReq(BaseModel):
    deckId: str


class JumpReq(BaseModel):
    index: int


class TimingReq(BaseModel):
    slideId: str
    durationSec: Optional[float] = None
    mode: Optional[Literal["auto", "manual"]] = None
    temperature: Optional[float] = None


# --------------------------------------------------------------------------- #
# Deck library
# --------------------------------------------------------------------------- #
@app.get("/api/decks")
def api_list_decks():
    return deck_mod.list_decks()


@app.post("/api/decks")
def api_create_deck(req: CreateDeckReq):
    deck = deck_mod.create_deck(req.name)
    return deck.model_dump()


def _load_deck_or_404(deck_id: str) -> Deck:
    try:
        return deck_mod.get_deck(deck_id)
    except FileNotFoundError:
        raise HTTPException(404, f"deck '{deck_id}' not found")


@app.get("/api/decks/{deck_id}")
def api_get_deck(deck_id: str):
    return _load_deck_or_404(deck_id).model_dump()


@app.put("/api/decks/{deck_id}")
def api_save_deck(deck_id: str, deck: Deck):
    if deck.id != deck_id:
        raise HTTPException(400, "deck id mismatch")
    deck_mod.save_deck(deck)
    # if we're editing the deck that's currently loaded, refresh it live
    if presentation.deck and presentation.deck.id == deck_id:
        presentation.load(deck_mod.get_deck(deck_id))
    return deck.model_dump()


@app.delete("/api/decks/{deck_id}")
def api_delete_deck(deck_id: str):
    deck_mod.delete_deck(deck_id)
    return {"ok": True}


@app.post("/api/decks/{deck_id}/duplicate")
def api_duplicate_deck(deck_id: str):
    _load_deck_or_404(deck_id)
    return deck_mod.duplicate_deck(deck_id).model_dump()


# --------------------------------------------------------------------------- #
# Images
# --------------------------------------------------------------------------- #
@app.post("/api/decks/{deck_id}/images")
async def api_upload_images(deck_id: str, files: list[UploadFile]):
    deck = _load_deck_or_404(deck_id)
    for f in files:
        data = await f.read()
        try:
            deck_mod.add_image_bytes(deck, data, f.filename or "image.png")
        except Exception as exc:
            raise HTTPException(400, f"could not import {f.filename}: {exc}")
    deck_mod.save_deck(deck)
    return deck.model_dump()


@app.post("/api/decks/{deck_id}/images/from-path")
def api_add_from_path(deck_id: str, req: FromPathReq):
    deck = _load_deck_or_404(deck_id)
    try:
        added = deck_mod.add_from_path(deck, req.path)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(400, str(exc))
    if not added:
        raise HTTPException(400, "no supported images found at that path")
    deck_mod.save_deck(deck)
    return deck.model_dump()


@app.get("/api/decks/{deck_id}/thumb/{slide_id}")
def api_thumb(deck_id: str, slide_id: str):
    deck = _load_deck_or_404(deck_id)
    slide = next((s for s in deck.slides if s.id == slide_id), None)
    if slide is None:
        raise HTTPException(404, "slide not found")
    tp = deck_mod.ensure_thumb(deck, slide)
    if not tp or not tp.exists():
        raise HTTPException(404, "thumbnail unavailable")
    return FileResponse(tp, media_type="image/jpeg")


# --------------------------------------------------------------------------- #
# Presentation control
# --------------------------------------------------------------------------- #
@app.post("/api/present/load")
async def api_present_load(req: LoadReq):
    deck = _load_deck_or_404(req.deckId)
    presentation.load(deck)
    await broadcast()
    return presentation.state()


def _require_deck():
    if presentation.deck is None:
        raise HTTPException(409, "no deck loaded")


@app.post("/api/present/timing")
async def api_present_timing(req: TimingReq):
    """Live-edit a slide's duration/mode and persist to the deck JSON,
    without resetting the presentation position."""
    _require_deck()
    if not presentation.update_timing(req.slideId, req.durationSec, req.mode, req.temperature):
        raise HTTPException(404, "slide not found")
    deck_mod.save_deck(presentation.deck)
    await broadcast()
    return presentation.state()


@app.post("/api/present/{action}")
async def api_present_action(action: str, body: Optional[JumpReq] = None):
    _require_deck()
    if action == "start":
        presentation.start()
    elif action == "next":
        presentation.next()
    elif action == "prev":
        presentation.prev()
    elif action == "pause":
        presentation.pause()
    elif action == "resume":
        presentation.resume()
    elif action == "toggle":
        presentation.toggle_pause()
    elif action == "replay":
        presentation.replay()
    elif action == "stop":
        presentation.stop()
    elif action == "mirror":
        presentation.set_mirror(not presentation.mirror)
        if presentation.deck is not None:
            deck_mod.save_deck(presentation.deck)
    elif action == "mirrorhud":
        presentation.set_mirror_hud(not presentation.mirror_hud)
        if presentation.deck is not None:
            deck_mod.save_deck(presentation.deck)
    elif action == "jump":
        if body is None:
            raise HTTPException(400, "jump requires {index}")
        presentation.jump(body.index)
    else:
        raise HTTPException(404, f"unknown action '{action}'")
    await broadcast()
    return presentation.state()


@app.get("/api/present/state")
def api_present_state():
    return full_state()


# --------------------------------------------------------------------------- #
# Tavus persona bridge
#
# Inverted (push) flow: the operator drives the deck on this server; the persona
# REACTS. On each manual gate or burning frame the presentation fires
# on_narrate, and we push a "stovetop update" into the live conversation over
# the Daily data channel (conversation.respond) so the persona notices and
# coaches in its own words. Connect the room once via /api/tavus/connect.
#
# (/api/tavus/advance is kept for the older persona-pulls-the-deck flow.)
# --------------------------------------------------------------------------- #
STOVE_DECK_ID = "omelette"

bridge = tavus_bridge.ConversationBridge()


# Tiered escalation:
#   burning  (>= burnThreshold) → urgent "pull it off"
#   fire     (>= 730°F or "fire" in label) → hilarious panic, smother with a lid
#   inferno  (>= 800°F or "engulf"/"stove on fire" in label) → DEAD SERIOUS:
#            call 911, run for your life. No jokes.
FIRE_F = 730.0
INFERNO_F = 800.0
START_LINE = "Let's get cooking!"
_prev_status: Optional[str] = None


def _narration_text(ev: dict) -> str:
    label = ev.get("label") or "the pan"
    temp = ev.get("temp")          # CURRENT interpolated reading (matches the HUD)
    target = ev.get("target")      # stage target — used ONLY to classify burn/fire
    tpart = f", about {temp}°F" if temp is not None else ""
    lname = label.lower()
    is_inferno = ("engulf" in lname) or ("stove on fire" in lname) or (target is not None and target >= INFERNO_F)
    is_fire = (not is_inferno) and (("fire" in lname) or (target is not None and target >= FIRE_F))
    if is_inferno:
        return (
            f"[Stovetop camera] {label}{tpart} — THE WHOLE STOVE IS ENGULFED, "
            "the kitchen is going up in flames — call 911 and run for your life!"
        )
    if is_fire:
        return f"[Stovetop camera] {label}{tpart} — there are real FLAMES in the pan, it's on FIRE!"
    if ev.get("is_burning"):
        return f"[Stovetop camera] {label}{tpart} — it's past the burn point and charring; get it off the heat."
    if ev.get("is_gate") and ev.get("cue"):
        # action step → hand the persona the full instruction
        return f"[Stovetop camera] {label}{tpart}. It's time: {ev['cue']}"
    # routine frame → terse
    return f"[Stovetop camera] {label}{tpart}."


_prev_tier: Optional[str] = None
_silent_since_speak = 0
CADENCE_N = 2   # speak at least once every Nth non-manual frame


def _on_narrate(ev: dict) -> None:
    """Speak at meaningful moments — action gates, the first frame that enters
    the burning or fire tier, and on a cadence (every Nth non-manual frame) so
    the persona stays alive without repeating on every single frame. Everything
    else is silent context."""
    global _prev_status, _prev_tier, _silent_since_speak
    status = ev.get("status")
    started = status == "playing" and _prev_status != "playing"
    _prev_status = status

    if started:
        sent = bridge.echo(START_LINE)
        _prev_tier = None
        _silent_since_speak = 0
        print(f"[narrate:start] sent={sent} :: {START_LINE}", flush=True)
        return

    text = _narration_text(ev)
    if status != "playing":
        sent = bridge.append_context(text)
        _prev_tier = None
        _silent_since_speak = 0
        print(f"[narrate:context] sent={sent} :: {text}", flush=True)
        return

    label = (ev.get("label") or "").lower()
    target = ev.get("target")
    is_inferno = ("engulf" in label) or ("stove on fire" in label) or (target is not None and target >= INFERNO_F)
    is_fire = (not is_inferno) and (("fire" in label) or (target is not None and target >= FIRE_F))
    is_burn = (not is_inferno and not is_fire) and bool(ev.get("is_burning"))
    is_gate = bool(ev.get("is_gate"))
    if is_inferno: tier = "inferno"
    elif is_fire: tier = "fire"
    elif is_burn: tier = "burning"
    elif is_gate: tier = "gate"
    else: tier = "normal"

    tier_entry = (
        (is_inferno and _prev_tier != "inferno") or
        (is_fire and _prev_tier != "fire") or
        (is_burn and _prev_tier != "burning")
    )
    if is_gate or tier_entry:
        speak = True
    else:
        _silent_since_speak += 1
        speak = _silent_since_speak >= CADENCE_N

    if speak:
        sent = bridge.respond(text)
        mode = "respond"
        _silent_since_speak = 0
    else:
        sent = bridge.append_context(text)
        mode = "context"
    _prev_tier = tier
    print(f"[narrate:{mode}] sent={sent} :: {text}", flush=True)


presentation.on_narrate = _on_narrate
# Hold timer-driven advance while the persona is still speaking (no-op when the
# bridge isn't connected — replica_speaking() is then always False).
presentation.speaking_gate = bridge.replica_speaking


class AdvanceReq(BaseModel):
    reason: Optional[str] = None   # free-text the persona fills in; logged, not required


def _ensure_stove_deck() -> None:
    if presentation.deck is None:
        try:
            presentation.load(deck_mod.get_deck(STOVE_DECK_ID))
        except FileNotFoundError:
            raise HTTPException(404, f"deck '{STOVE_DECK_ID}' not found")


def _coach_line(info: dict) -> str:
    if info.get("finished"):
        return "The omelette's done — take it off the heat and plate it up."
    gate = info.get("next_gate")
    if not gate or not gate.get("cue"):
        return "Looking good — keep an eye on it."
    cue = gate["cue"]
    if info.get("awaiting_user"):
        return cue                                   # we're holding here now: act now
    eta = gate.get("eta_seconds") or 0
    if eta >= 3:
        return f"In about {round(eta)} seconds: {cue}"
    return cue


@app.post("/api/tavus/advance")
async def api_tavus_advance(req: AdvanceReq | None = None):
    """Persona tool target. Releases the current manual step (or starts the cook
    on the first call) and returns the next step to coach. Safe to call when not
    at a gate — it just reports state without skipping ahead."""
    _ensure_stove_deck()
    result = presentation.advance()
    result["coach"] = _coach_line(result)
    result["reason_received"] = req.reason if req else None
    await broadcast()
    return result


@app.get("/api/tavus/state")
def api_tavus_state():
    """Read-only view of the same payload — handy for debugging the tunnel."""
    _ensure_stove_deck()
    info = presentation.gate_info()
    info["coach"] = _coach_line(info)
    return info


@app.post("/api/tavus/reset")
async def api_tavus_reset():
    """Reload the stove deck and park on the start step (standby)."""
    presentation.load(deck_mod.get_deck(STOVE_DECK_ID))
    await broadcast()
    info = presentation.gate_info()
    info["coach"] = _coach_line(info)
    return info


# ----- push flow: server -> conversation (Daily data channel) -------------- #
class ConnectReq(BaseModel):
    conversationUrl: str
    conversationId: str


@app.post("/api/tavus/connect")
def api_tavus_connect(req: ConnectReq):
    """Join the conversation's Daily room so the server can push stovetop
    updates into it. Call once after creating a conversation."""
    try:
        return bridge.connect(req.conversationUrl, req.conversationId)
    except RuntimeError as exc:
        raise HTTPException(500, str(exc))


@app.post("/api/tavus/disconnect")
def api_tavus_disconnect():
    bridge.disconnect()
    return bridge.status()


@app.get("/api/tavus/bridge")
def api_tavus_bridge():
    return bridge.status()


@app.post("/api/tavus/say")
def api_tavus_say(req: AdvanceReq | None = None):
    """Manually push a respond into the conversation (debugging the bridge)."""
    text = (req.reason if req else None) or "[Stovetop camera] test update from the stove server."
    return {"sent": bridge.respond(text), "text": text}


# --------------------------------------------------------------------------- #
# Live preview (WYSIWYG mirror of the camera output)
# --------------------------------------------------------------------------- #
@app.get("/api/preview")
def api_preview():
    async def gen():
        delay = 1.0 / config.PREVIEW_FPS
        while True:
            jpeg = engine.preview_jpeg
            yield (
                b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                + str(len(jpeg)).encode()
                + b"\r\n\r\n"
                + jpeg
                + b"\r\n"
            )
            await asyncio.sleep(delay)

    return StreamingResponse(gen(), media_type="multipart/x-mixed-replace; boundary=frame")


# --------------------------------------------------------------------------- #
# WebSocket
# --------------------------------------------------------------------------- #
@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    _clients.add(ws)
    try:
        await ws.send_json(full_state())
        while True:
            await ws.receive_text()   # keepalive; commands go through REST
    except WebSocketDisconnect:
        pass
    finally:
        _clients.discard(ws)


# --------------------------------------------------------------------------- #
# Static UI
# --------------------------------------------------------------------------- #
@app.get("/", response_class=HTMLResponse)
def index():
    return (config.STATIC_DIR / "index.html").read_text()


app.mount("/static", StaticFiles(directory=str(config.STATIC_DIR)), name="static")
