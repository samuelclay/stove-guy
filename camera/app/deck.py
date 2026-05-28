"""Deck model + on-disk library.

Layout on disk:
    decks/<deck-id>/
        deck.json          # the hand-editable spec you can also feed directly
        images/            # uploaded image files (referenced by relative path)
        thumbs/            # generated filmstrip thumbnails, one per slide id

Image-only folders are also accepted:
    decks/<deck-id>/images/001.png
    decks/<deck-id>/002.png
"""
from __future__ import annotations

import io
import json
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Literal, Optional

from PIL import Image
from pydantic import BaseModel, Field

# Register HEIC/HEIF support so iPhone / macOS Photos exports load via Pillow.
try:
    import pillow_heif  # type: ignore

    pillow_heif.register_heif_opener()
except Exception:  # pragma: no cover - optional but installed by default
    pass

from . import config


# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #
class Transition(BaseModel):
    type: Literal["crossfade", "cut"] = "crossfade"
    durationMs: int = config.DEFAULT_TRANSITION_MS


class Output(BaseModel):
    width: int = config.CAM_WIDTH
    height: int = config.CAM_HEIGHT
    fps: int = config.CAM_FPS


class Slide(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    image: str                                   # relative to deck dir, or absolute
    label: str = ""
    mode: Literal["auto", "manual"] = "auto"
    durationSec: Optional[float] = None          # used when mode == auto
    fit: Optional[Literal["cover", "contain"]] = None
    transition: Optional[Transition] = None
    temperature: Optional[float] = None          # IR-thermometer target temp for this frame
    dip: Optional[float] = None                  # deg the reading drops on entry, then recovers
    cue: Optional[str] = None                    # coaching line handed to the Tavus persona at a manual gate


class Defaults(BaseModel):
    durationSec: float = config.DEFAULT_DURATION_SEC
    fit: Literal["cover", "contain"] = config.DEFAULT_FIT
    transition: Transition = Field(default_factory=Transition)


class Thermal(BaseModel):
    """IR-thermometer HUD config for a recipe."""
    enabled: bool = False
    unit: str = "F"
    coldThreshold: Optional[float] = None         # below = "too cold / not ready"
    burnThreshold: Optional[float] = None         # above = "burning"
    coldLabel: str = "Too cold"
    burnLabel: str = "Burning above this"
    minTemp: Optional[float] = None               # sparkline y-axis range (auto if None)
    maxTemp: Optional[float] = None
    stoveTemp: Optional[float] = None             # the burner setting (fixed, set once) shown on the HUD dial
    stoveMin: float = 150.0                       # dial low end ("LOW")
    stoveMax: float = 550.0                       # dial high end ("HIGH")


class Deck(BaseModel):
    version: int = 1
    id: str
    name: str
    output: Output = Field(default_factory=Output)
    background: str = config.DEFAULT_BACKGROUND
    mirror: bool = False                          # horizontally flip the photo
    mirrorHud: bool = False                       # horizontally flip the HUD overlay (independently)
    thermal: Thermal = Field(default_factory=Thermal)
    defaults: Defaults = Field(default_factory=Defaults)
    slides: list[Slide] = Field(default_factory=list)

    # --- effective (merged-with-defaults) accessors ------------------------ #
    def eff_duration(self, slide: Slide) -> float:
        return float(slide.durationSec if slide.durationSec is not None else self.defaults.durationSec)

    def eff_fit(self, slide: Slide) -> str:
        return slide.fit or self.defaults.fit

    def eff_transition(self, slide: Slide) -> Transition:
        return slide.transition or self.defaults.transition


# --------------------------------------------------------------------------- #
# Storage helpers
# --------------------------------------------------------------------------- #
def _slugify(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", name.strip().lower()).strip("-")
    return s or "deck"


def _title_from_slug(slug: str) -> str:
    return re.sub(r"[-_]+", " ", slug).strip().title() or slug


def _stable_slide_id(index: int, image_path: Path) -> str:
    slug = _slugify(image_path.stem)
    return f"{index:03d}-{slug}"[:64]


def deck_dir(deck_id: str) -> Path:
    return config.DECKS_DIR / deck_id


def _images_dir(deck_id: str) -> Path:
    d = deck_dir(deck_id) / "images"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _thumbs_dir(deck_id: str) -> Path:
    d = deck_dir(deck_id) / "thumbs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _deck_json(deck_id: str) -> Path:
    return deck_dir(deck_id) / "deck.json"


def resolve_image(deck_id: str, image_ref: str) -> Path:
    """Resolve a slide's image reference (relative or absolute) to a real path."""
    p = Path(image_ref)
    if p.is_absolute():
        return p
    return deck_dir(deck_id) / image_ref


def _image_files_for_folder(deck_id: str) -> list[tuple[Path, str]]:
    """Return sortable image files for an image-only recipe folder."""
    root = deck_dir(deck_id)
    if not root.is_dir():
        return []

    image_root = root / "images"
    if image_root.is_dir():
        files = sorted(p for p in image_root.iterdir() if p.suffix.lower() in config.SUPPORTED_EXTS)
        return [(p, f"images/{p.name}") for p in files]

    files = sorted(p for p in root.iterdir() if p.suffix.lower() in config.SUPPORTED_EXTS)
    return [(p, p.name) for p in files]


def _deck_from_image_folder(deck_id: str) -> Optional[Deck]:
    images = _image_files_for_folder(deck_id)
    if not images:
        return None

    deck = Deck(id=deck_id, name=_title_from_slug(deck_id))
    for index, (path, image_ref) in enumerate(images, start=1):
        deck.slides.append(
            Slide(
                id=_stable_slide_id(index, path),
                image=image_ref,
                label=path.stem,
            )
        )
    return deck


# --------------------------------------------------------------------------- #
# CRUD
# --------------------------------------------------------------------------- #
def list_decks() -> list[dict]:
    out = []
    for child in sorted(config.DECKS_DIR.iterdir()) if config.DECKS_DIR.exists() else []:
        if not child.is_dir():
            continue
        jf = child / "deck.json"
        if jf.is_file():
            try:
                data = json.loads(jf.read_text())
                out.append(
                    {
                        "id": data.get("id", child.name),
                        "name": data.get("name", child.name),
                        "slideCount": len(data.get("slides", [])),
                        "updated": jf.stat().st_mtime,
                    }
                )
            except Exception:
                continue
        else:
            deck = _deck_from_image_folder(child.name)
            if deck is None:
                continue
            images = _image_files_for_folder(child.name)
            updated = max((path.stat().st_mtime for path, _ in images), default=child.stat().st_mtime)
            out.append(
                {
                    "id": deck.id,
                    "name": deck.name,
                    "slideCount": len(deck.slides),
                    "updated": updated,
                }
            )
    out.sort(key=lambda d: d["updated"], reverse=True)
    return out


def create_deck(name: str) -> Deck:
    base = _slugify(name)
    deck_id = base
    n = 2
    while deck_dir(deck_id).exists():
        deck_id = f"{base}-{n}"
        n += 1
    deck = Deck(id=deck_id, name=name)
    _images_dir(deck_id)
    _thumbs_dir(deck_id)
    save_deck(deck)
    return deck


def get_deck(deck_id: str) -> Deck:
    jf = _deck_json(deck_id)
    if not jf.is_file():
        deck = _deck_from_image_folder(deck_id)
        if deck is None:
            raise FileNotFoundError(deck_id)
        return deck
    return Deck.model_validate_json(jf.read_text())


def save_deck(deck: Deck) -> Deck:
    deck_dir(deck.id).mkdir(parents=True, exist_ok=True)
    _deck_json(deck.id).write_text(json.dumps(deck.model_dump(), indent=2))
    # Make sure every slide has a thumbnail.
    for slide in deck.slides:
        ensure_thumb(deck, slide)
    return deck


def delete_deck(deck_id: str) -> None:
    d = deck_dir(deck_id)
    if d.exists():
        shutil.rmtree(d)


def duplicate_deck(deck_id: str) -> Deck:
    src = get_deck(deck_id)
    new = create_deck(f"{src.name} (copy)")
    # Copy relative image refs into the same relative locations in the new deck.
    # Absolute refs are intentionally kept in place.
    for slide in src.slides:
        ref = Path(slide.image)
        if ref.is_absolute():
            continue
        src_path = deck_dir(deck_id) / ref
        dst_path = deck_dir(new.id) / ref
        if src_path.exists():
            dst_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_path, dst_path)
    # carry over settings + slides
    new.background = src.background
    new.defaults = src.defaults
    new.output = src.output
    new.slides = [s.model_copy(update={"id": uuid.uuid4().hex[:8]}) for s in src.slides]
    save_deck(new)
    return new


# --------------------------------------------------------------------------- #
# Image import
# --------------------------------------------------------------------------- #
def _next_image_name(deck_id: str, suffix: str) -> str:
    existing = list(_images_dir(deck_id).glob("*"))
    idx = len(existing) + 1
    return f"{idx:03d}{suffix}"


def add_image_bytes(deck: Deck, data: bytes, filename: str) -> Slide:
    """Import an uploaded image. HEIC/HEIF are converted to PNG on the way in."""
    ext = Path(filename).suffix.lower()
    img = Image.open(io.BytesIO(data))
    img.load()
    if ext in {".heic", ".heif"}:
        ext = ".png"
    if ext not in config.SUPPORTED_EXTS:
        ext = ".png"
    out_name = _next_image_name(deck.id, ext)
    out_path = _images_dir(deck.id) / out_name
    save_kwargs = {}
    if ext in {".jpg", ".jpeg"}:
        img = img.convert("RGB")
    img.save(out_path, **save_kwargs)
    slide = Slide(image=f"images/{out_name}", label=Path(filename).stem)
    deck.slides.append(slide)
    ensure_thumb(deck, slide)
    return slide


def add_from_path(deck: Deck, path_str: str) -> list[Slide]:
    """Add a single image by absolute path, or every image inside a folder."""
    p = Path(path_str).expanduser()
    if not p.exists():
        raise FileNotFoundError(path_str)
    targets: list[Path]
    if p.is_dir():
        targets = sorted(c for c in p.iterdir() if c.suffix.lower() in config.SUPPORTED_EXTS)
    else:
        if p.suffix.lower() not in config.SUPPORTED_EXTS:
            raise ValueError(f"Unsupported image type: {p.suffix}")
        targets = [p]
    added: list[Slide] = []
    for t in targets:
        slide = Slide(image=str(t.resolve()), label=t.stem)
        deck.slides.append(slide)
        ensure_thumb(deck, slide)
        added.append(slide)
    return added


# --------------------------------------------------------------------------- #
# Thumbnails
# --------------------------------------------------------------------------- #
def thumb_path(deck_id: str, slide_id: str) -> Path:
    return _thumbs_dir(deck_id) / f"{slide_id}.jpg"


def ensure_thumb(deck: Deck, slide: Slide) -> Optional[Path]:
    tp = thumb_path(deck.id, slide.id)
    src = resolve_image(deck.id, slide.image)
    marker = tp.with_suffix(".src")
    try:
        if not src.exists():
            return tp if tp.exists() else None
        # Cache key = which image + its mtime. Keying on the source (not just the
        # thumb's own mtime) means a slide that now points at a *different* image
        # (after inserting/reordering frames) invalidates the thumbnail even when
        # the new image file is older than the previously-cached thumb.
        sig = f"{src}|{int(src.stat().st_mtime)}"
        if tp.exists() and marker.exists() and marker.read_text() == sig:
            return tp
        img = Image.open(src)
        img.load()
        img = img.convert("RGB")
        w = config.THUMB_WIDTH
        h = max(1, round(img.height * w / img.width))
        img = img.resize((w, h), Image.LANCZOS)
        img.save(tp, "JPEG", quality=80)
        marker.write_text(sig)
        return tp
    except Exception:
        return None
