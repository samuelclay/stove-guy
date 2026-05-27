"""The frame pump.

A dedicated thread continuously sends the current frame to the OBS virtual
camera at a fixed FPS (a camera must keep emitting even on a static image).
When the live image changes it crossfades (or cuts) to the new frame, and it
keeps a small JPEG of the latest displayed frame for the browser preview.
"""
from __future__ import annotations

import io
import threading
import time

import numpy as np
from PIL import Image

from . import config, frames

try:
    import pyvirtualcam
    from pyvirtualcam import PixelFormat
except Exception:  # pragma: no cover
    pyvirtualcam = None
    PixelFormat = None


class CameraEngine:
    def __init__(self) -> None:
        self.width = config.CAM_WIDTH
        self.height = config.CAM_HEIGHT
        self.fps = config.CAM_FPS

        self._lock = threading.Lock()
        self._displayed = frames.solid_frame("#000000")     # what's on screen now
        self._from = self._displayed                          # crossfade start
        self._target = self._displayed                        # crossfade end
        self._fade_start = 0.0
        self._fade_dur = 0.0                                  # seconds; 0 == no fade
        self._overlay = None                                  # (rgba_tile, x, y) | None
        self._mirror = False                                  # flip the fully composited frame

        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

        self.device_name: str | None = None
        self.device_error: str | None = None

        # Preview (WYSIWYG mirror for the browser)
        self._preview_jpeg: bytes = self._encode_preview(self._displayed)
        self._preview_counter = 0
        self._preview_every = max(1, round(self.fps / config.PREVIEW_FPS))

    # ----------------------------------------------------------------- API --
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="camera-pump", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def set_target(self, frame: np.ndarray, fade_ms: int = 0) -> None:
        """Switch the live image to ``frame``, crossfading over ``fade_ms``."""
        with self._lock:
            if fade_ms <= 0:
                self._displayed = frame
                self._from = frame
                self._target = frame
                self._fade_dur = 0.0
            else:
                self._from = self._displayed
                self._target = frame
                self._fade_start = time.monotonic()
                self._fade_dur = fade_ms / 1000.0

    def set_overlay(self, rgba: np.ndarray | None, x: int = 0, y: int = 0) -> None:
        """Set (or clear) an RGBA HUD tile alpha-blended onto every frame."""
        self._overlay = None if rgba is None else (rgba, int(x), int(y))

    def set_mirror(self, mirror: bool) -> None:
        """Mirror the final camera output after image + HUD compositing."""
        with self._lock:
            self._mirror = bool(mirror)

    @property
    def preview_jpeg(self) -> bytes:
        return self._preview_jpeg

    def status(self) -> dict:
        return {
            "deviceName": self.device_name,
            "deviceError": self.device_error,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
        }

    # -------------------------------------------------------------- internal --
    def _encode_preview(self, frame: np.ndarray) -> bytes:
        img = Image.fromarray(frame, "RGB").resize(
            (config.PREVIEW_WIDTH, config.PREVIEW_HEIGHT), Image.BILINEAR
        )
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=config.PREVIEW_JPEG_QUALITY)
        return buf.getvalue()

    def _composite_overlay(self, frame: np.ndarray, overlay) -> np.ndarray:
        rgba, x, y = overlay
        th, tw = rgba.shape[:2]
        fh, fw = frame.shape[:2]
        if x < 0 or y < 0 or x + tw > fw or y + th > fh:
            return frame
        out = frame.copy()
        region = out[y:y + th, x:x + tw].astype(np.float32)
        a = rgba[:, :, 3:4].astype(np.float32) / 255.0
        out[y:y + th, x:x + tw] = (region * (1.0 - a) + rgba[:, :, :3].astype(np.float32) * a).astype(np.uint8)
        return out

    def _compute_displayed(self) -> np.ndarray:
        with self._lock:
            if self._fade_dur > 0.0:
                t = (time.monotonic() - self._fade_start) / self._fade_dur
                if t >= 1.0:
                    self._displayed = self._target
                    self._from = self._target
                    self._fade_dur = 0.0
                    return self._displayed
                return frames.blend(self._from, self._target, t)
            return self._displayed

    def _finalize_frame(self, frame: np.ndarray, overlay) -> np.ndarray:
        # Mirror the PHOTO only, then overlay the HUD, so the HUD text stays
        # readable (and pinned top-right) regardless of the mirror toggle.
        with self._lock:
            mirror = self._mirror
        if mirror:
            frame = frames.flip_h(frame)
        if overlay is not None:
            frame = self._composite_overlay(frame, overlay)
        return frame

    def _open_camera(self):
        if pyvirtualcam is None:
            self.device_error = "pyvirtualcam not available"
            return None
        try:
            cam = pyvirtualcam.Camera(
                width=self.width,
                height=self.height,
                fps=self.fps,
                fmt=PixelFormat.RGB,
                print_fps=False,
            )
            self.device_name = cam.device
            self.device_error = None
            return cam
        except Exception as exc:  # OBS virtual camera not installed/approved yet
            self.device_error = (
                f"{type(exc).__name__}: {exc}. Is the OBS Virtual Camera installed "
                "and approved? (Install OBS, Start Virtual Camera once, approve the "
                "system extension, then quit OBS.)"
            )
            return None

    def _run(self) -> None:
        cam = self._open_camera()
        frame_interval = 1.0 / self.fps
        last_retry = time.monotonic()
        try:
            while not self._stop.is_set():
                frame = self._compute_displayed()
                ov = self._overlay
                frame = self._finalize_frame(frame, ov)

                # update preview every Nth frame to limit CPU
                self._preview_counter += 1
                if self._preview_counter >= self._preview_every:
                    self._preview_counter = 0
                    try:
                        self._preview_jpeg = self._encode_preview(frame)
                    except Exception:
                        pass

                if cam is not None:
                    try:
                        cam.send(frame)
                        cam.sleep_until_next_frame()
                        continue
                    except Exception as exc:
                        self.device_error = f"send failed: {exc}"
                        try:
                            cam.close()
                        except Exception:
                            pass
                        cam = None
                # no camera: keep the loop (and preview) alive, retry every ~2s
                time.sleep(frame_interval)
                if cam is None and time.monotonic() - last_retry > 2.0:
                    last_retry = time.monotonic()
                    cam = self._open_camera()
        finally:
            if cam is not None:
                try:
                    cam.close()
                except Exception:
                    pass


# Module-level singleton used by the server.
engine = CameraEngine()
