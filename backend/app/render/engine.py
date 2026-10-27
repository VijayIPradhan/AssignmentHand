"""Handwriting engines turn text into positioned drawing primitives.

``HandwritingEngine`` is the extension point: the shipped ``GlyphCompositingEngine`` places glyphs
harvested from the user's samples. A stroke-based or ML synthesis engine can implement the same
interface (shape a run of text -> items + metrics) without changes to layout or PDF output.

All geometry is in millimetres. Within a ``Run``, x grows right and y grows *down*, with the baseline
at y=0 (so ascenders have negative y).
"""

import math
import random
from abc import ABC, abstractmethod
from collections import Counter
from dataclasses import dataclass, field

from reportlab.pdfbase.pdfmetrics import stringWidth

from app.handwriting.charset import SUBSTITUTIONS
from app.handwriting.glyphs import Glyph, GlyphLibrary
from app.render.fonts import font_has_char, glyph_fallback_fonts

PT_PER_MM = 72 / 25.4
FALLBACK_X_HEIGHT_EM = 0.53


@dataclass
class GlyphItem:
    glyph: Glyph
    x: float
    y: float  # baseline
    scale: float  # library mm -> page mm
    rot: float = 0.0  # degrees, counter-clockwise
    shear: float = 0.0  # tan(slant offset), positive leans right
    weight: float = 1.0  # relative stroke weight for this glyph (multiplied with the document setting)


@dataclass
class FallbackItem:
    ch: str
    x: float
    y: float
    size_mm: float
    shear: float = 0.0
    rot: float = 0.0
    font: str = ""


@dataclass
class TofuItem:
    """Visible placeholder for a character no source can draw (never silently substituted)."""

    x: float
    y: float
    w: float
    h: float


@dataclass
class LineItem:
    pts: list[tuple[float, float]]
    width: float


@dataclass
class ImageItem:
    data: bytes
    x: float
    y: float  # top
    w: float
    h: float


Item = GlyphItem | FallbackItem | TofuItem | LineItem | ImageItem


def shift_item(it: Item, dx: float, dy: float) -> Item:
    if isinstance(it, LineItem):
        return LineItem([(x + dx, y + dy) for x, y in it.pts], it.width)
    if isinstance(it, GlyphItem):
        return GlyphItem(it.glyph, it.x + dx, it.y + dy, it.scale, it.rot, it.shear, it.weight)
    if isinstance(it, FallbackItem):
        return FallbackItem(it.ch, it.x + dx, it.y + dy, it.size_mm, it.shear, it.rot, it.font)
    if isinstance(it, TofuItem):
        return TofuItem(it.x + dx, it.y + dy, it.w, it.h)
    return ImageItem(it.data, it.x + dx, it.y + dy, it.w, it.h)


@dataclass
class Run:
    items: list[Item] = field(default_factory=list)
    width: float = 0.0
    ascent: float = 0.0  # above baseline, positive
    descent: float = 0.0  # below baseline, positive

    def append(self, other: "Run", gap: float = 0.0, dy: float = 0.0) -> None:
        x = self.width + gap
        self.items += [shift_item(i, x, dy) for i in other.items]
        self.width = x + other.width
        self.ascent = max(self.ascent, other.ascent - dy)
        self.descent = max(self.descent, other.descent + dy)


@dataclass
class EngineStyle:
    x_height_mm: float = 2.8
    weight: float = 1.0
    slant_offset_deg: float = 0.0
    char_spacing: float = 1.0
    word_spacing: float = 1.0
    variation: float = 0.5


@dataclass
class UsageReport:
    fallback: Counter = field(default_factory=Counter)
    approximated: Counter = field(default_factory=Counter)
    unsupported: Counter = field(default_factory=Counter)


class HandwritingEngine(ABC):
    name: str = "abstract"
    style: EngineStyle
    usage: UsageReport

    @abstractmethod
    def shape(self, text: str, rng: random.Random, scale: float = 1.0) -> Run:
        """Shape a run of text (may contain spaces) at ``scale`` x the base handwriting size."""

    @abstractmethod
    def space_width(self, rng: random.Random, scale: float = 1.0) -> float: ...

    @abstractmethod
    def ascent(self, scale: float = 1.0) -> float: ...

    @abstractmethod
    def descent(self, scale: float = 1.0) -> float: ...

    @property
    @abstractmethod
    def stroke_mm(self) -> float: ...

    @abstractmethod
    def set_size(self, x_height_mm: float) -> None:
        """Change the base handwriting size (used by fit-to-space)."""

    @property
    def x_height(self) -> float:
        return self.style.x_height_mm


# Letters whose capital is the same shape as the lowercase form, so scaling is a faithful stand-in.
_SAME_SHAPE_CASE = set("covwxz")  # "s" and "u" are often written differently as capitals
_PUNCT_TIGHT = set(".,;:!?)]}'\"")
_OPEN = set("([{")


class GlyphCompositingEngine(HandwritingEngine):
    name = "glyph-compositing"

    def __init__(self, library: GlyphLibrary, style: EngineStyle):
        self.lib = library
        self.style = style
        self.usage = UsageReport()
        self.s = style.x_height_mm / max(library.x_height_mm, 0.1)
        self.shear = math.tan(math.radians(style.slant_offset_deg))
        self._last_variant: dict[str, int] = {}
        self._fonts = glyph_fallback_fonts()
        tops = [g.top_mm for ch in "bdfhklABDEFHKLT" for g in library.variants(ch)]
        bottoms = [-g.bottom_mm for ch in "gjpqy" for g in library.variants(ch)]
        self._asc = max(tops) if tops else library.cap_height_mm * 1.1
        self._desc = (sorted(bottoms)[len(bottoms) // 2] if bottoms else library.x_height_mm * 0.55) * 1.1

    def set_size(self, x_height_mm: float) -> None:
        self.style.x_height_mm = x_height_mm
        self.s = x_height_mm / max(self.lib.x_height_mm, 0.1)

    @property
    def stroke_mm(self) -> float:
        return self.lib.stroke_mm * self.s * self.style.weight

    def ascent(self, scale: float = 1.0) -> float:
        return self._asc * self.s * scale

    def descent(self, scale: float = 1.0) -> float:
        return self._desc * self.s * scale

    def _letter_gap(self, rng: random.Random, scale: float) -> float:
        v = self.style.variation
        base = self.lib.letter_gap_ratio * self.lib.x_height_mm * self.s * scale * self.style.char_spacing
        return max(0.0, base * (1 + rng.gauss(0, 0.15 * v)))

    def space_width(self, rng: random.Random, scale: float = 1.0) -> float:
        v = self.style.variation
        base = self.lib.word_gap_ratio * self.lib.x_height_mm * self.s * scale * self.style.word_spacing
        return max(base * 0.5, base * (1 + rng.gauss(0, 0.12 * v)))

    def _pick(self, ch: str, rng: random.Random) -> Glyph:
        variants = self.lib.variants(ch)
        if len(variants) == 1:
            return variants[0]
        last = self._last_variant.get(ch)
        choices = [i for i in range(len(variants)) if i != last]
        idx = rng.choice(choices)
        self._last_variant[ch] = idx
        return variants[idx]

    def _glyph_run(self, g: Glyph, rng: random.Random, scale: float, extra_scale: float = 1.0, dy: float = 0.0) -> Run:
        v = self.style.variation
        js = 1 + rng.gauss(0, 0.03 * v)
        k = self.s * scale * extra_scale * js
        rot = rng.gauss(0, 1.3 * v)
        jy = rng.gauss(0, 0.05 * self.lib.x_height_mm * self.s * scale * v) + dy
        # Scaling a glyph up also thickens its strokes; compensate so it matches the surrounding letters.
        item = GlyphItem(g, 0.0, jy, k, rot, self.shear, extra_scale**-0.8 if extra_scale > 1.05 else 1.0)
        return Run([item], g.width_mm * k, g.top_mm * k - jy, max(0.0, -g.bottom_mm * k + jy))

    def _char_run(self, ch: str, rng: random.Random, scale: float) -> Run:
        if self.lib.has(ch):
            return self._glyph_run(self._pick(ch, rng), rng, scale)
        sub = SUBSTITUTIONS.get(ch)
        if sub and self.lib.has(sub):
            return self._glyph_run(self._pick(sub, rng), rng, scale)
        if ch == "·" and self.lib.has("."):
            self.usage.approximated[ch] += 1
            return self._glyph_run(self._pick(".", rng), rng, scale, dy=-0.45 * self.x_height * scale)
        lower = ch.lower()
        if ch.isupper() and lower in _SAME_SHAPE_CASE and self.lib.has(lower):
            self.usage.approximated[ch] += 1
            ratio = self.lib.cap_height_mm / max(self.lib.x_height_mm, 0.1)
            return self._glyph_run(self._pick(lower, rng), rng, scale, extra_scale=ratio)
        font = next((f for f in self._fonts if font_has_char(f, ch)), None)
        if font is not None:
            self.usage.fallback[ch] += 1
            size = self.x_height * scale / FALLBACK_X_HEIGHT_EM
            w = stringWidth(ch, font, size * PT_PER_MM) / PT_PER_MM
            return Run([FallbackItem(ch, 0.0, 0.0, size, self.shear, 0.0, font)], w, size * 0.75, size * 0.22)
        self.usage.unsupported[ch] += 1
        w, h = self.x_height * scale * 0.8, self.x_height * scale * 1.2
        return Run([TofuItem(0.0, -h, w, h)], w, h, 0.0)

    def shape(self, text: str, rng: random.Random, scale: float = 1.0) -> Run:
        run = Run()
        prev = None
        for ch in text:
            if ch == " ":
                run.width += self.space_width(rng, scale)
                prev = " "
                continue
            if ch in ("\n", "\r", "\u200b"):
                continue
            cr = self._char_run(ch, rng, scale)
            if prev is None or prev == " ":
                gap = 0.0
            else:
                gap = self._letter_gap(rng, scale)
                if ch in _PUNCT_TIGHT or prev in _OPEN:
                    gap *= 0.55
            run.append(cr, gap)
            prev = ch
        run.ascent = max(run.ascent, self.ascent(scale) * 0.6)
        run.descent = max(run.descent, 0.0)
        return run
