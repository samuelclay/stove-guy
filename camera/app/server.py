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
    if not presentation.update_timing(req.slideId, req.durationSec, req.mode):
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
