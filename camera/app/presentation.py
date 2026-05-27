"""Presentation state machine.

Owns the live position in a deck and decides when to advance. Auto slides run a
countdown; manual slides hold until the user advances. Reaching the end holds
the last image. All visual changes are pushed to the camera engine.
"""
from __future__ import annotations

import threading

from . import deck as deck_mod
from . import frames
from .camera_engine import CameraEngine
from .deck import Deck, Slide

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
        self._lock = threading.Lock()

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
            if not self._slides:
                self.status = STANDBY
                self.engine.set_target(frames.solid_frame(deck.background), 0)
            else:
                self.status = STANDBY
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
                self._show(0, use_transition=False)
            else:
                self.engine.set_target(frames.solid_frame(self.deck.background), 0)

    def tick(self, dt: float) -> None:
        """Advance the countdown; called ~10x/sec by the server's ticker."""
        with self._lock:
            if self.status != PLAYING:
                return
            slide = self.current
            if slide is None or slide.mode != "auto" or self.remaining is None:
                return
            self.remaining -= dt
            if self.remaining <= 0:
                advance_now = True
            else:
                advance_now = False
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
            }
