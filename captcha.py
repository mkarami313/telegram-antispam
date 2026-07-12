"""Image captcha generation, shared by the child bots.

The character set deliberately excludes visually ambiguous glyphs
(``0/O``, ``1/I/L``) so the challenge stays solvable even at low image quality.
"""

from __future__ import annotations

import io
import random
import string
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

import config

log = config.setup_logging("antispam.captcha")

# Uppercase letters + digits, minus the confusable ones.
CAPTCHA_ALPHABET = "".join(
    c for c in (string.ascii_uppercase + string.digits) if c not in "O0I1L"
)
CAPTCHA_LENGTH = 6


def new_code() -> str:
    """Return a fresh random captcha string."""
    return "".join(random.choices(CAPTCHA_ALPHABET, k=CAPTCHA_LENGTH))


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    for name in ("arial.ttf", "DejaVuSans-Bold.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def generate_image(code: str) -> Optional[io.BytesIO]:
    """Render ``code`` into a noisy PNG and return it as a BytesIO, or ``None``."""
    try:
        font_size = 40
        font = _load_font(font_size)

        char_spacing = 35
        width = len(code) * char_spacing + 40
        height = font_size + 40
        image = Image.new("RGB", (width, height), (255, 255, 255))
        draw = ImageDraw.Draw(image)

        for i, char in enumerate(code):
            char_image = Image.new("RGBA", (char_spacing + 20, height + 20), (0, 0, 0, 0))
            char_draw = ImageDraw.Draw(char_image)
            char_draw.text((5, 5), char, font=font, fill=(0, 0, 0, 255))
            angle = random.randint(-25, 25)
            rotated = char_image.rotate(angle, expand=True, resample=Image.BICUBIC)
            paste_x = 15 + i * (char_spacing - 3)
            paste_y = random.randint(5, 15)
            image.paste(rotated, (paste_x, paste_y), rotated)

        for _ in range(6):
            draw.line(
                (
                    random.randint(0, width),
                    random.randint(0, height),
                    random.randint(0, width),
                    random.randint(0, height),
                ),
                fill=(150, 150, 150),
                width=2,
            )

        for _ in range(80):
            draw.point(
                (random.randint(0, width - 1), random.randint(0, height - 1)),
                fill=(0, 0, 0),
            )

        buf = io.BytesIO()
        image.save(buf, "PNG")
        buf.seek(0)
        return buf
    except Exception as exc:  # pragma: no cover - defensive
        log.error("Error generating captcha: %s", exc)
        return None
