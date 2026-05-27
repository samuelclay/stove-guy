"""Global configuration for the Stove Guy virtual presentation camera."""
from __future__ import annotations

from pathlib import Path

# --- Paths -------------------------------------------------------------------
APP_DIR = Path(__file__).resolve().parent
ROOT_DIR = APP_DIR.parent                     # .../camera
DECKS_DIR = ROOT_DIR / "decks"
STATIC_DIR = ROOT_DIR / "static"

DECKS_DIR.mkdir(parents=True, exist_ok=True)

# --- Camera output -----------------------------------------------------------
# The virtual camera advertises one fixed size; every image is composited to it.
CAM_WIDTH = 1920
CAM_HEIGHT = 1080
CAM_FPS = 30

# --- Defaults ----------------------------------------------------------------
DEFAULT_DURATION_SEC = 5.0
DEFAULT_FIT = "cover"                          # "cover" | "contain"
DEFAULT_TRANSITION_MS = 300
DEFAULT_BACKGROUND = "#000000"

# --- Preview (the WYSIWYG mirror shown in the browser) -----------------------
PREVIEW_WIDTH = 640
PREVIEW_HEIGHT = 360
PREVIEW_FPS = 15
PREVIEW_JPEG_QUALITY = 70

# --- Server ------------------------------------------------------------------
HOST = "127.0.0.1"
PORT = 8000

# --- Image handling ----------------------------------------------------------
SUPPORTED_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".heic", ".heif", ".bmp", ".gif", ".tiff"}
THUMB_WIDTH = 360
