"""Presentation state machine.

Owns the live position in a deck and decides when to advance. Auto slides run a
countdown; manual slides hold until the user advances. Reaching the end holds
the last image. All visual changes are pushed to the camera engine.
"""
from __future__ import annotations

import threading
from typing import Callable, Optional

from . import deck as deck_mod
from . import frames
from . import hud
from .camera_engine import CameraEngine
from .deck import Deck, Slide
from .temperature import TemperatureModel

# status values
NO_DECK = "no_deck"
STANDBY = "standby"     # parked on first image, not yet started
PLAYING = "playing"
PAUSED = "paused"
ENDED = "ended"         # holding the last image


class Presentation:
    def __init__(self, engine: CameraEngine) -> None:
        self.engine = engine
        self.deck: Deck | None = None
        self.index = 0
        self.status = NO_DECK
        self.remaining: float | None = None
        self.mirror = False
        self.mirror_hud = False
        self.temp = TemperatureModel()
        self._lock = threading.Lock()
        # Fired on every live slide change; the server pushes a stovetop update
        # into the Tavus conversation. Receives an event dict.
        self.on_narrate: Optional[Callable[[dict], None]] = None
        # Optional predicate: return True to HOLD auto-advance (e.g. the persona
        # is still speaking). Only gates timer-driven advance, never manual.
        self.speaking_gate: Optional[Callable[[], bool]] = None

    # ---------------------------------------------------------------- helpers
    @property
    def _slides(self) -> list[Slide]:
        return self.deck.slides if self.deck else []

    @property
    def current(self) -> Slide | None:
        if self.deck and 0 <= self.index < len(self._slides):
            return self._slides[self.index]
        return None

    def _fade_ms(self, slide: Slide) -> int:
        tr = self.deck.eff_transition(slide)
        return 0 if tr.type == "cut" else int(tr.durationMs)

    def _frame_for(self, slide: Slide):
        path = deck_mod.resolve_image(self.deck.id, slide.image)
        return frames.render(path, fit=self.deck.eff_fit(slide), background=self.deck.background)

    def _show(self, index: int, use_transition: bool) -> None:
        self.index = index
        slide = self.current
        if slide is None:
            self.engine.set_target(frames.solid_frame(self.deck.background if self.deck else "#000000"), 0)
            return
        fade = self._fade_ms(slide) if use_transition else 0
        self.engine.set_target(self._frame_for(slide), fade)
        self._begin_temp_segment()
        self._emit_frame()

    def _emit_frame(self) -> None:
        """Emit a frame event on EVERY slide change. The server pushes a silent
        context update for awareness, and forces the persona to speak on the
        frames that warrant it (manual gates + burning). Flags + status let the
        server decide; the presentation stays transport-agnostic."""
        if self.on_narrate is None:
            return
        slide = self.current
        if slide is None:
            return
        burn = self.deck.thermal.burnThreshold if (self.deck and self.deck.thermal) else None
        target = slide.temperature
        is_burning = burn is not None and target is not None and target >= burn
        # Report the CURRENT interpolated reading (what the HUD shows), not the
        # target the pan is ramping toward. Target is kept only for classifying
        # burning/fire so warnings fire on the right frame even as the reading lags.
        if self.temp.enabled and self.temp.display is not None:
            current = round(self.temp.display)
        else:
            current = round(target) if target is not None else None
        event = {
            "label": slide.label,
            "temp": current,
            "target": target,
            "mode": slide.mode,
            "is_gate": slide.mode == "manual",
            "is_burning": is_burning,
            "status": self.status,
            "index": self.index,
            "cue": slide.cue,
        }
        try:
            self.on_narrate(event)
        except Exception:
            pass

    def _begin_temp_segment(self) -> None:
        slide = self.current
        if slide is None:
            return
        dur = self.deck.eff_duration(slide) if slide.mode == "auto" else None
        self.temp.begin_segment(slide.temperature, dur, slide.dip)

    def _arm_timer(self) -> None:
        slide = self.current
        if slide is not None and slide.mode == "auto":
            self.remaining = self.deck.eff_duration(slide)
        else:
            self.remaining = None

    # ----------------------------------------------------------------- public
    def load(self, deck: Deck) -> None:
        with self._lock:
            self.deck = deck
            self.index = 0
            self.remaining = None
            self.mirror = bool(deck.mirror)
            self.mirror_hud = bool(deck.mirrorHud)
            self.engine.set_mirror(self.mirror)
            self.temp.configure(deck.thermal)
            if not deck.thermal.enabled:
                self.engine.set_overlay(None)
            if not self._slides:
                self.status = STANDBY
                self.temp.reset(None)
                self.engine.set_target(frames.solid_frame(deck.background), 0)
            else:
                self.status = STANDBY
                self.temp.reset(self._slides[0].temperature)
                self._show(0, use_transition=False)   # standby = first image (cut)

    def start(self) -> None:
        with self._lock:
            if self.status in (STANDBY, PAUSED):
                self.status = PLAYING
                if self.remaining is None:
                    self._arm_timer()

    def next(self) -> None:
        with self._lock:
            if not self._slides:
                return
            if self.index + 1 < len(self._slides):
                self.status = PLAYING
                self._show(self.index + 1, use_transition=True)
                self._arm_timer()
            else:
                self.status = ENDED        # hold last image

    def prev(self) -> None:
        with self._lock:
            if not self._slides:
                return
            if self.index > 0:
                self.status = PLAYING
                self._show(self.index - 1, use_transition=True)
                self._arm_timer()

    def jump(self, index: int) -> None:
        with self._lock:
            if not self._slides:
                return
            index = max(0, min(index, len(self._slides) - 1))
            self.status = PLAYING
            self._show(index, use_transition=True)
            self._arm_timer()

    def pause(self) -> None:
        with self._lock:
            if self.status == PLAYING:
                self.status = PAUSED

    def resume(self) -> None:
        with self._lock:
            if self.status == PAUSED:
                self.status = PLAYING

    def toggle_pause(self) -> None:
        with self._lock:
            if self.status == PLAYING:
                self.status = PAUSED
            elif self.status == PAUSED:
                self.status = PLAYING
            elif self.status == STANDBY:
                self.status = PLAYING
                if self.remaining is None:
                    self._arm_timer()

    def replay(self) -> None:
        with self._lock:
            slide = self.current
            if slide is not None and slide.mode == "auto":
                self.remaining = self.deck.eff_duration(slide)
                if self.status in (PAUSED, ENDED, STANDBY):
                    self.status = PLAYING

    def stop(self) -> None:
        with self._lock:
            if not self.deck:
                return
            self.remaining = None
            self.status = STANDBY
            if self._slides:
                self.temp.reset(self._slides[0].temperature)
                self._show(0, use_transition=False)
            else:
                self.engine.set_target(frames.solid_frame(self.deck.background), 0)

    def set_mirror(self, value: bool) -> None:
        with self._lock:
            self.mirror = bool(value)
            self.engine.set_mirror(self.mirror)
            if self.deck is not None:
                self.deck.mirror = self.mirror
            slide = self.current
            if slide is not None:
                # re-render the live image for an instant cut with the new final transform
                self.engine.set_target(self._frame_for(slide), 0)

    def set_mirror_hud(self, value: bool) -> None:
        with self._lock:
            self.mirror_hud = bool(value)
            if self.deck is not None:
                self.deck.mirrorHud = self.mirror_hud

    def update_timing(self, slide_id: str, duration=None, mode=None, temperature=None) -> bool:
        """Change a slide's duration/mode/temperature in place, WITHOUT resetting
        position. If the edited slide is live, its countdown and temperature ramp
        are re-armed so the change takes effect immediately.
        """
        with self._lock:
            if not self.deck:
                return False
            slide = next((s for s in self._slides if s.id == slide_id), None)
            if slide is None:
                return False
            if mode in ("auto", "manual"):
                slide.mode = mode
            if duration is not None:
                slide.durationSec = float(duration)
            if temperature is not None:
                slide.temperature = float(temperature)
            if slide is self.current and self.status in (PLAYING, PAUSED, STANDBY):
                self.remaining = self.deck.eff_duration(slide) if slide.mode == "auto" else None
                if temperature is not None:
                    self.temp.begin_segment(slide.temperature, self.remaining, slide.dip)
            return True

    # --------------------------------------------------- Tavus persona bridge
    def _is_gate(self) -> bool:
        """True when the deck is parked on a manual step waiting to be released:
        STANDBY (the first/start slide) or PLAYING on a manual slide."""
        slide = self.current
        if slide is None:
            return False
        if self.status == STANDBY:
            return True
        return self.status == PLAYING and slide.mode == "manual"

    def advance(self) -> dict:
        """Release the current manual gate and let auto slides chain to the next
        one. A no-op if we're mid auto-segment (so an early/extra tool call can't
        skip a step) or already ended. Returns what happened + the gate lookahead.
        """
        gated = self._is_gate()
        ended = self.status == ENDED
        if gated:
            self.next()   # next() takes the lock itself
        result = self.gate_info()
        result["advanced"] = gated
        if not gated:
            result["skipped_reason"] = "ended" if ended else "mid_segment"
        return result

    def gate_info(self) -> dict:
        """Where we are plus the next manual step the deck will hold at: its label,
        coaching cue, and an ETA (seconds of auto-play between here and there).
        Built for the persona to narrate and pace itself."""
        with self._lock:
            slides = self._slides
            if not self.deck or not slides:
                return {"status": self.status, "finished": False, "next_gate": None}
            cur = self._slides[self.index] if 0 <= self.index < len(slides) else None
            awaiting = self.status == PLAYING and cur is not None and cur.mode == "manual"
            holding = cur is not None and cur.mode == "manual" and self.status in (PLAYING, STANDBY)

            gate = None
            gate_index = None
            eta = 0.0
            if holding:
                gate, gate_index, eta = cur, self.index, 0.0
            elif cur is not None:
                # time left on the slide we're on now, then each auto slide after it
                if cur.mode == "auto":
                    eta += self.remaining if self.remaining is not None else self.deck.eff_duration(cur)
                for j in range(self.index + 1, len(slides)):
                    s = slides[j]
                    if s.mode == "manual":
                        gate, gate_index = s, j
                        break
                    eta += self.deck.eff_duration(s)

            return {
                "status": self.status,
                "finished": self.status == ENDED,
                "now_showing": cur.label if cur else "",
                "now_index": self.index,
                "awaiting_user": awaiting,
                "pan_temp_f": round(self.temp.display) if (self.temp.enabled and self.temp.display is not None) else None,
                "temp_zone": self.temp.zone() if self.temp.enabled else None,
                "next_gate": None if gate is None else {
                    "label": gate.label,
                    "cue": gate.cue,
                    "index": gate_index,
                    "eta_seconds": round(eta, 1),
                },
            }

    def tick(self, dt: float) -> None:
        """Advance the countdown + temperature; called ~10x/sec by the ticker."""
        advance_now = False
        with self._lock:
            advancing = self.status == PLAYING
            self.temp.update(dt, advancing)
            if advancing:
                slide = self.current
                if slide is not None and slide.mode == "auto" and self.remaining is not None:
                    self.remaining -= dt
                    advance_now = self.remaining <= 0
            snap = self.temp.snapshot()
        # render the thermal HUD outside the lock (PIL work), then composite
        if snap is not None:
            rgba, x, y = hud.render(snap, self.engine.width, self.engine.height, self.mirror_hud)
            self.engine.set_overlay(rgba, x, y)
        # Hold the timer-driven advance while the persona is mid-sentence; the
        # timer stays expired, so we advance on the first tick after it finishes.
        if advance_now and self.speaking_gate is not None:
            try:
                if self.speaking_gate():
                    advance_now = False
            except Exception:
                pass
        if advance_now:
            self.next()   # next() takes the lock itself

    # ------------------------------------------------------------------ state
    def state(self) -> dict:
        with self._lock:
            slide = self.current
            awaiting = self.status == PLAYING and slide is not None and slide.mode == "manual"
            duration = (
                self.deck.eff_duration(slide)
                if (self.deck and slide is not None and slide.mode == "auto")
                else None
            )
            return {
                "status": self.status,
                "deckId": self.deck.id if self.deck else None,
                "deckName": self.deck.name if self.deck else None,
                "index": self.index,
                "slideCount": len(self._slides),
                "slideId": slide.id if slide else None,
                "label": slide.label if slide else "",
                "mode": slide.mode if slide else None,
                "awaitingManual": awaiting,
                "paused": self.status == PAUSED,
                "remaining": round(self.remaining, 2) if self.remaining is not None else None,
                "duration": duration,
                "mirror": self.mirror,
                "mirrorHud": self.mirror_hud,
                "temp": round(self.temp.display) if (self.temp.enabled and self.temp.display is not None) else None,
                "tempZone": self.temp.zone() if self.temp.enabled else None,
                "tempTarget": round(slide.temperature) if (slide and slide.temperature is not None) else None,
            }
