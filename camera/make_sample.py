#!/usr/bin/env python3
"""Generate a demo deck: slides A, B, C auto-advance at 5s, D waits for you.

Run:  camera/.venv/bin/python camera/make_sample.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from app import config  # noqa: E402
from app import deck as deck_mod  # noqa: E402

SLIDES = [
    ("A", "#1f6feb", "auto", 5),
    ("B", "#2ea043", "auto", 5),
    ("C", "#a371f7", "auto", 5),
    ("D", "#ff6a3d", "manual", None),
]


def _font(size: int):
    for path in (
        "/System/Library/Fonts/SFNS.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/Library/Fonts/Arial.ttf",
    ):
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                pass
    return ImageFont.load_default()


def make_image(letter: str, color: str, w=1920, h=1080) -> Image.Image:
    img = Image.new("RGB", (w, h), color)
    d = ImageDraw.Draw(img)
    big = _font(520)
    small = _font(64)
    d.text((w / 2, h / 2 - 40), letter, font=big, fill="white", anchor="mm")
    d.text((w / 2, h - 130), "Stove Guy demo", font=small, fill=(255, 255, 255, 200), anchor="mm")
    return img


def main():
    # remove any prior demo so re-running is clean
    demo_id = "demo"
    deck_mod.delete_deck(demo_id)
    deck = deck_mod.create_deck("Demo — A/B/C auto, D manual")
    # create_deck slugifies; force the id to 'demo' for a stable URL
    deck_mod.delete_deck(deck.id)
    from app.deck import Deck

    deck = Deck(id=demo_id, name="Demo — A/B/C auto, D manual")
    images_dir = config.DECKS_DIR / demo_id / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    for i, (letter, color, mode, dur) in enumerate(SLIDES, start=1):
        fname = f"{i:03d}.png"
        make_image(letter, color).save(images_dir / fname)
        from app.deck import Slide

        slide = Slide(image=f"images/{fname}", label=f"Slide {letter}", mode=mode)
        if mode == "auto":
            slide.durationSec = dur
        deck.slides.append(slide)

    deck_mod.save_deck(deck)
    print(f"Created demo deck '{demo_id}' with {len(deck.slides)} slides.")
    print("Open http://127.0.0.1:8000 and present it.")


if __name__ == "__main__":
    main()
