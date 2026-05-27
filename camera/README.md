# Stove Guy — Virtual Presentation Camera

Feed an ordered set of images into a **virtual camera** and drive them like a
slide deck. Each slide either auto-advances after N seconds or **waits for you
to press Next** (manual). Whatever is live is composited to 1080p and pushed
into the **OBS Virtual Camera**, so you can select it as a camera in Zoom,
Meet, FaceTime, QuickTime, etc.

A localhost web app lets you upload/order images, set timings, mark slides
manual, and enter a **presentation mode** with a live cursor showing exactly
which photo the camera is currently sending.

```
camera/
  app/                  # Python package
    config.py           # output size (1080p@30), defaults, paths
    deck.py             # deck JSON schema + on-disk library (CRUD, import, thumbs)
    frames.py           # cover/contain compositing + crossfade blend (numpy/PIL)
    camera_engine.py    # 30fps pyvirtualcam pump thread + crossfade + preview buffer
    presentation.py     # state machine: timers, manual holds, pause/advance/back
    server.py           # FastAPI: REST + /ws + MJPEG preview + static UI
  static/               # index.html, styles.css, app.js (single-page UI)
  decks/<id>/           # deck.json + images/ + thumbs/   (one folder per deck)
  smoke_test.py         # Phase 0: verify pyvirtualcam can drive the OBS device
  make_sample.py        # generates the "A/B/C auto, D manual" demo deck
  run.sh                # bootstrap venv + launch the server
  requirements.txt
```

## One-time setup

1. **Install OBS** (provides the virtual-camera device):
   ```bash
   brew install --cask obs
   ```
2. **Activate the OBS Virtual Camera system extension** (this is the one step
   that needs your click — macOS requires you to approve it):
   - Open **OBS** → click **Start Virtual Camera** (bottom-right).
   - macOS will say a system extension was blocked → open
     **System Settings → General → Login Items & Extensions → Camera Extensions**
     (or the prompt's **Privacy & Security** banner) and **enable “OBS”**.
   - You may be asked to **restart the Mac** the first time — do it.
   - After it's approved, **quit OBS** (or just Stop Virtual Camera). This app
     becomes the camera's producer; OBS itself does not need to be running.
3. **Verify** the device works:
   ```bash
   camera/.venv/bin/python camera/smoke_test.py
   ```
   Open Photo Booth or QuickTime → New Movie Recording → camera dropdown →
   pick **OBS Virtual Camera**. You should see a moving test pattern. Ctrl-C to stop.

> If `smoke_test.py` says the device isn't installed, the extension isn't
> approved yet (or needs a reboot). Repeat step 2.

## Run

```bash
camera/run.sh          # creates the venv on first run, then serves the app
```
Open **http://127.0.0.1:8000**. In your call app, select **OBS Virtual Camera**.

## Using it

- **Library** — create / present / edit / duplicate / delete decks.
- **Editor** — set defaults (duration, fit, crossfade, background); add images by
  drag-drop, Browse, or a local path/folder; drag slides to reorder; per slide
  toggle **Auto/Manual**, set seconds, and override fit. Changes autosave.
- **Present** — the big preview mirrors the real camera output. The filmstrip
  shows a **LIVE** cursor on the current slide with a countdown bar; manual
  slides show a pulsing "waiting for you →".

  | Key | Action |
  |-----|--------|
  | `Space` / `→` | Next (also starts) |
  | `←` | Previous |
  | `P` | Pause / resume countdown |
  | `R` | Replay current slide's timer |
  | `Esc` | Stop → standby (first image) |

  You can also click any thumbnail to jump straight to it.

Before you start, the camera shows the deck's **first image** (standby). When you
reach the end and advance again, it **holds the last image**.

## The deck JSON (`decks/<id>/deck.json`)

The file is plain, hand-editable JSON — this *is* the format you feed it.
The library page also discovers image-only recipe folders, so a folder like
`decks/omelette/images/001-empty-pan.png` through `010-finished.png` will show
up as a recipe even before you add a `deck.json`.

```jsonc
{
  "version": 1,
  "id": "demo",
  "name": "My Demo Deck",
  "output":   { "width": 1920, "height": 1080, "fps": 30 },
  "background": "#000000",                                  // letterbox/pad color
  "defaults": {
    "durationSec": 5,
    "fit": "cover",                                         // cover | contain
    "transition": { "type": "crossfade", "durationMs": 300 }
  },
  "slides": [
    { "id": "a", "image": "images/001.png", "mode": "auto",   "durationSec": 5 },
    { "id": "b", "image": "images/002.png", "mode": "auto",   "durationSec": 5 },
    { "id": "c", "image": "images/003.png", "mode": "auto",   "durationSec": 5 },
    { "id": "d", "image": "images/004.png", "mode": "manual" },               // holds for you
    { "id": "e", "image": "/abs/path/x.png", "mode": "auto", "durationSec": 8,
      "fit": "contain", "transition": { "type": "cut" } }                     // per-slide overrides
  ]
}
```

- `mode: "auto"` uses `durationSec` (falls back to `defaults.durationSec`);
  `mode: "manual"` waits for a Next.
- `image` is relative to the deck folder (uploads) **or** an absolute path
  (added from a folder, used in place — not copied).
- Any slide may override `fit` and `transition`.

Generate a demo deck any time:
```bash
camera/.venv/bin/python camera/make_sample.py
```

## Troubleshooting

- **Camera pill shows "camera offline"** — the OBS extension isn't approved or
  the Mac needs a reboot (see setup step 2). The web UI and preview still work
  without the device.
- **Approved the extension while the server was already running?** macOS caches
  the camera-device list per process, so a server started *before* activation
  won't see the new device. Just **restart the server** (re-run `run.sh`).
- **Frozen / black in the call app** — make sure OBS is **not** also running its
  virtual camera; only one producer can drive the device at a time.
- **HEIC didn't import** — `pillow-heif` is in `requirements.txt`; re-run
  `pip install -r requirements.txt` inside `.venv`.
