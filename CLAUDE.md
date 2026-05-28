# CLAUDE.md — stove-guy

Tavus hackathon project for conversational cooking. The main component is a
**virtual presentation camera** in [`camera/`](camera/README.md): a localhost
FastAPI app + OBS-backed virtual camera that drives an ordered set of images
like a slide deck, plus a `/api/tavus/*` bridge a Tavus persona calls to advance
the cook.

## Running the server

From the repo root:

```bash
make            # start server -> camera/server.log, then tail -f it
make logs       # tail the log of an already-running server
make stop       # stop the server (pkill uvicorn)
```

`make` redirects the server's stdout/stderr to **`camera/server.log`**. The
server binds localhost only at http://127.0.0.1:8000.

## Reading the log (`camera/server.log`)

Everything the server emits goes to `camera/server.log`. Besides uvicorn's
access lines, every `/api/*` request and response is logged with **ANSI color**
by `camera/app/traffic_log.py`:

- `→ POST /api/tavus/advance` + the request body (pretty JSON).
- `← 200 /api/tavus/advance 45ms` + the response body.

The MJPEG `/api/preview` stream and the `/ws` WebSocket are skipped; non-text
responses show only a status line; bodies over 2000 chars are truncated.
`STOVE_TRAFFIC_LOG=0` disables it.

When reading the log programmatically, strip the color codes first:

```bash
sed $'s/\x1b\[[0-9;]*m//g' camera/server.log   # plain text for grep/parsing
tail -f camera/server.log                        # follow live (renders colors)
```

## Layout

- `camera/app/` — Python package (see `camera/README.md` for the module map).
- `camera/decks/<id>/` — one folder per deck: `deck.json` + `images/` + `thumbs/`.
- `camera/static/` — single-page UI (`index.html`, `styles.css`, `app.js`).
- `camera/run.sh` — bootstraps the venv and launches uvicorn.

See [`camera/README.md`](camera/README.md) for setup (OBS virtual-camera
extension), the deck JSON schema, and troubleshooting.
