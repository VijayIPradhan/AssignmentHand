"""Locate and register a Unicode TTF used for template labels and missing-glyph fallback."""

import logging
from functools import lru_cache
from pathlib import Path

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from app.config import get_settings

log = logging.getLogger(__name__)

_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "C:/Windows/Fonts/DejaVuSans.ttf",
    "C:/Windows/Fonts/arial.ttf",
]

FALLBACK_NAME = "AHFallback"

# Handwriting-style fonts that permit PDF embedding, tried first for characters missing from a profile.
_HANDWRITING_CANDIDATES = [
    "C:/Windows/Fonts/segoepr.ttf",  # Segoe Print
    "C:/Windows/Fonts/Inkfree.ttf",
    "/usr/share/fonts/truetype/kalam/Kalam-Regular.ttf",
    "/usr/share/fonts/truetype/patrick-hand/PatrickHand-Regular.ttf",
]


@lru_cache
def fallback_font() -> str:
    """Unicode font for template labels and typed exports. Helvetica (Latin-1 only) if none is found."""
    for cand in _CANDIDATES:
        if cand and Path(cand).is_file():
            try:
                pdfmetrics.registerFont(TTFont(FALLBACK_NAME, cand))
                return FALLBACK_NAME
            except Exception:  # pragma: no cover - corrupt font file
                log.warning("could not load fallback font", extra={"path": cand})
    log.warning("no Unicode TTF found; using Helvetica for fallback glyphs")
    return "Helvetica"


@lru_cache
def glyph_fallback_fonts() -> tuple[str, ...]:
    """Fonts tried, in order, for characters missing from a handwriting profile."""
    names: list[str] = []
    configured = get_settings().fallback_font_path
    for i, cand in enumerate(([configured] if configured else []) + _HANDWRITING_CANDIDATES):
        if cand and Path(cand).is_file():
            try:
                pdfmetrics.registerFont(TTFont(f"AHHand{i}", cand))
                names.append(f"AHHand{i}")
                break
            except Exception:
                log.warning("could not load handwriting fallback font", extra={"path": cand})
    names.append(fallback_font())
    return tuple(names)


def font_has_char(font_name: str, ch: str) -> bool:
    if font_name == "Helvetica":
        return ord(ch) < 256
    face = pdfmetrics.getFont(font_name).face
    return ord(ch) in face.charToGlyph
