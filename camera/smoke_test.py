#!/usr/bin/env python3
"""Phase 0: prove pyvirtualcam can drive the OBS Virtual Camera on this machine.

Run it, then open Photo Booth or QuickTime (New Movie Recording -> camera
dropdown) and select "OBS Virtual Camera". You should see an animated test
pattern with a moving bar and a frame counter. Ctrl-C to stop.
"""
import sys
import time

import numpy as np

try:
    import pyvirtualcam
    from pyvirtualcam import PixelFormat
except Exception as exc:
    print(f"pyvirtualcam import failed: {exc}")
    sys.exit(2)

W, H, FPS = 1920, 1080, 30


def make_frame(i: int) -> np.ndarray:
    frame = np.zeros((H, W, 3), dtype=np.uint8)
    # animated diagonal gradient
    x = np.linspace(0, 255, W, dtype=np.uint8)
    frame[:, :, 0] = (x + i * 3) % 256          # R sweeps
    frame[:, :, 1] = np.linspace(0, 255, H, dtype=np.uint8)[:, None]  # G vertical
    frame[:, :, 2] = (128 + i) % 256             # B pulses
    # a moving vertical white bar so motion is obvious
    bar = (i * 12) % W
    frame[:, max(0, bar - 8): bar + 8, :] = 255
    return frame


def main() -> int:
    try:
        cam = pyvirtualcam.Camera(width=W, height=H, fps=FPS, fmt=PixelFormat.RGB, print_fps=False)
    except Exception as exc:
        print("\n❌ Could not open the virtual camera.")
        print(f"   {type(exc).__name__}: {exc}\n")
        print("   Most likely the OBS Virtual Camera system extension isn't approved yet.")
        print("   Fix: open OBS, click 'Start Virtual Camera' once, approve the system")
        print("   extension in System Settings -> Privacy & Security, then quit OBS and retry.")
        return 1

    print(f"✅ Opened virtual camera device: {cam.device}")
    print(f"   {W}x{H} @ {FPS}fps. Open Photo Booth / QuickTime and pick 'OBS Virtual Camera'.")
    print("   Ctrl-C to stop.")
    i = 0
    try:
        while True:
            cam.send(make_frame(i))
            cam.sleep_until_next_frame()
            i += 1
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        cam.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
