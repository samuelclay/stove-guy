"""Simulated IR-thermometer reading.

The reading ramps linearly from its current value to the active slide's target
temperature over that slide's timer (so it moves in real time and stays
continuous across slides), with small random jitter on top — larger when cool,
tapering as it gets hot. A rolling history feeds the HUD sparkline.
"""
from __future__ import annotations

import random
from collections import deque

JITTER_INTERVAL = 1.0      # re-roll the random offset at most this often (s)
JITTER_EASE = 4.0          # how fast the displayed jitter chases its target
MANUAL_EASE = 3.0          # ramp time (s) for manual slides / unknown durations
SAMPLE_DT = 0.5            # how often a point is recorded for the sparkline
HISTORY_SECONDS = 50.0
HIST_MAX = int(HISTORY_SECONDS / SAMPLE_DT)


def _amp(temp: float) -> float:
    """Jitter amplitude: ~4 deg when cool, ~1 deg once hot."""
    lo, hi = 200.0, 420.0
    if temp <= lo:
        return 4.0
    if temp >= hi:
        return 1.0
    return 4.0 - 3.0 * (temp - lo) / (hi - lo)


class TemperatureModel:
    def __init__(self) -> None:
        self.enabled = False
        self.unit = "F"
        self.cold: float | None = None
        self.burn: float | None = None
        self.cold_label = "Too cold"
        self.burn_label = "Burning above this"
        self.tmin: float | None = None
        self.tmax: float | None = None

        self.base: float | None = None       # smooth interpolated trend
        self.display: float | None = None     # base + jitter (what's shown)
        self.history: deque[float] = deque(maxlen=HIST_MAX)

        self._from = 0.0
        self._to: float | None = None
        self._dur = 0.0
        self._elapsed = 0.0
        self._jit = 0.0
        self._jit_target = 0.0
        self._jit_timer = 0.0
        self._sample_timer = 0.0

    # ---------------------------------------------------------------- config
    def configure(self, thermal) -> None:
        self.enabled = bool(thermal.enabled)
        self.unit = thermal.unit or "F"
        self.cold = thermal.coldThreshold
        self.burn = thermal.burnThreshold
        self.cold_label = thermal.coldLabel
        self.burn_label = thermal.burnLabel
        self.tmin = thermal.minTemp
        self.tmax = thermal.maxTemp

    def reset(self, target: float | None) -> None:
        self.history.clear()
        self.base = target
        self.display = target
        self._from = target if target is not None else 0.0
        self._to = target
        self._dur = 0.0
        self._elapsed = 0.0
        self._jit = 0.0
        self._jit_target = 0.0
        self._jit_timer = 0.0
        self._sample_timer = 0.0

    def begin_segment(self, target: float | None, duration: float | None) -> None:
        """Start ramping from the current reading toward ``target``."""
        if target is None:
            return
        if self.base is None:
            self.reset(target)
            return
        self._from = self.base
        self._to = target
        self._dur = duration if (duration and duration > 0) else MANUAL_EASE
        self._elapsed = 0.0

    # ---------------------------------------------------------------- update
    def update(self, dt: float, advancing: bool) -> None:
        if self._to is None:
            return
        if self.base is None:
            self.base = self._to

        if advancing and self._dur > 0 and self._elapsed < self._dur:
            self._elapsed += dt
            frac = min(1.0, self._elapsed / self._dur)
            self.base = self._from + (self._to - self._from) * frac

        self._jit_timer += dt
        if self._jit_timer >= JITTER_INTERVAL:
            self._jit_timer = 0.0
            amp = _amp(self.base)
            self._jit_target = random.uniform(-amp, amp)
        self._jit += (self._jit_target - self._jit) * min(1.0, dt * JITTER_EASE)

        self.display = self.base + self._jit

        self._sample_timer += dt
        if self._sample_timer >= SAMPLE_DT:
            self._sample_timer = 0.0
            self.history.append(self.display)

    # ------------------------------------------------------------------ read
    def zone(self, temp: float | None = None) -> str:
        t = self.display if temp is None else temp
        if t is None:
            return "good"
        if self.cold is not None and t < self.cold:
            return "cold"
        if self.burn is not None and t >= self.burn:
            return "burn"
        return "good"

    def snapshot(self) -> dict | None:
        if not self.enabled or self.display is None:
            return None
        return {
            "temp": self.display,
            "unit": self.unit,
            "cold": self.cold,
            "burn": self.burn,
            "coldLabel": self.cold_label,
            "burnLabel": self.burn_label,
            "tmin": self.tmin,
            "tmax": self.tmax,
            "zone": self.zone(),
            "history": list(self.history),
        }
