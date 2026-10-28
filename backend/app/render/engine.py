"""Handwriting engines turn text into positioned drawing primitives.

``HandwritingEngine`` is the extension point: the shipped ``GlyphCompositingEngine`` places glyphs
harvested from the user's samples. A stroke-based or ML synthesis engine can implement the same
interface (shape a run of text -> items + metrics) without changes to layout or PDF output.

All geometry is in millimetres. Within a ``Run``, x grows right and y grows *down*, with the baseline
at y=0 (so ascenders have negative y).
"""

import dataclasses
import math
import random
from abc import ABC, abstractmethod
from collections import Counter
from dataclasses import dataclass, field

import numpy as np
from reportlab.pdfbase.pdfmetrics import stringWidth

from app.handwriting.charset import ASCENDER_CHARS, SUBSTITUTIONS, UPPER, X_HEIGHT_CHARS
from app.handwriting.glyphs import Glyph, GlyphLibrary, stroke_width_px
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
    color: str | None = None  # overrides the document ink colour (e.g. a signature in its own colour)


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
        return GlyphItem(it.glyph, it.x + dx, it.y + dy, it.scale, it.rot, it.shear, it.weight, it.color)
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
    joined: bool = False  # connect the letters of a word with drawn joining strokes (cursive)


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

# Size normalisation: letters whose top is the x-height line (descenders included), and the classes
# whose top is compared with the median of the class instead.
_BODY = X_HEIGHT_CHARS | set("gpqy")
_TALL_CLASSES = (set(ASCENDER_CHARS) - {"t"}, set(UPPER) | set("0123456789"))
_JOIN_LETTERS = set("abcdefghijklmnopqrstuvwxyz")
_NO_EXIT = set("gjqyz")  # end below the line (tails, the looped z): no join out of them
_TOP_EXIT = set("bovwr")  # leave from the top of the x-height band, not the baseline


def _joins(prev: str | None, ch: str) -> bool:
    return prev in _JOIN_LETTERS and ch in _JOIN_LETTERS and prev not in _NO_EXIT


def _bezier(p0, p1, p2, p3, n: int = 10) -> list[tuple[float, float]]:
    out = []
    for i in range(n + 1):
        t = i / n
        a, b, c, d = (1 - t) ** 3, 3 * t * (1 - t) ** 2, 3 * t * t * (1 - t), t**3
        out.append((a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0], a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1]))
    return out


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
        self._class_top = [
            float(np.median(ts)) if (ts := [g.top_mm for ch in cls for g in library.variants(ch)]) else None
            for cls in _TALL_CLASSES
        ]
        self._norm_cache: dict[int, tuple[float, float, float]] = {}
        self._anchor_cache: dict[tuple, tuple | None] = {}

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

    def _norm(self, g: Glyph) -> tuple[float, float, float]:
        """(size factor, stroke weight factor, stroke width in glyph mm) that make a glyph match the others.

        Letters collected from different photos come out at slightly different sizes and pen widths;
        each is scaled so its body matches the x-height (or its class's median height) and thinned or
        thickened towards the median stroke width."""
        hit = self._norm_cache.get(id(g))
        if hit is not None:
            return hit
        r = 1.0
        if g.top_mm > 0.3:
            if g.char in _BODY:
                r = self.lib.x_height_mm / g.top_mm
            else:
                for cls, med in zip(_TALL_CLASSES, self._class_top, strict=True):
                    if g.char in cls and med:
                        r = med / g.top_mm
        r = min(1.3, max(0.77, r))
        sw = stroke_width_px(g.bitmap) / g.px_per_mm if int((g.bitmap > 127).sum()) >= 60 else 0.0
        w = 1.0
        if sw > 0 and self.lib.stroke_mm > 0:
            w = round(min(1.5, max(0.65, self.lib.stroke_mm / (sw * r))) * 20) / 20
        hit = self._norm_cache[id(g)] = (r, w, sw or self.lib.stroke_mm / r)
        return hit

    def _side(self, g: Glyph):
        """Ink mask, x-height band (rows), x-height in px and stroke inset (mm) used to find join points."""
        m = g.bitmap > 127
        r, _w, sw = self._norm(g)
        xh_px = self.lib.x_height_mm / r * g.px_per_mm
        v = g.baseline_px - np.arange(m.shape[0])  # px above the baseline, per row
        band = (v >= -0.12 * xh_px) & (v <= 1.08 * xh_px)
        return m, v, band, xh_px, 0.35 * sw  # start inside the stroke so the round cap blends in

    def _exit(self, g: Glyph) -> tuple[float, float, float] | None:
        """Where a joining stroke leaves a glyph: the rightmost ink in the x-height band, at its lowest
        point (its top for o, v, w, b, r). Glyph mm (y up) plus the height as a share of the x-height."""
        key = ("exit", id(g))
        if key in self._anchor_cache:
            return self._anchor_cache[key]
        m, v, band, xh_px, inset = self._side(g)
        cols = np.nonzero((m & band[:, None]).any(axis=0))[0]
        out = None
        if len(cols):
            c = int(cols.max())
            ys = np.nonzero(m[:, c] & band)[0]
            y = int(ys.min() if g.char in _TOP_EXIT else ys.max())
            out = (c / g.px_per_mm - inset, v[y] / g.px_per_mm, float(v[y] / xh_px))
        self._anchor_cache[key] = out
        return out

    def _entry(self, g: Glyph, rel: float) -> tuple[float, float] | None:
        """Where a joining stroke enters a glyph: the leftmost ink near ``rel`` x-heights above the
        baseline (the height the stroke left the previous letter), so joins run level and do not cut
        across round letters."""
        rel = min(0.85, max(0.1, rel))
        key = ("entry", id(g), round(rel, 1))
        if key in self._anchor_cache:
            return self._anchor_cache[key]
        m, v, band, xh_px, inset = self._side(g)
        target = rel * xh_px
        cols = np.nonzero((m & band[:, None]).any(axis=0))[0]
        out = fallback = None
        first = int(cols.min()) if len(cols) else m.shape[1]
        for c in range(
            first, min(m.shape[1], first + max(2, int(0.25 * (cols.max() - first + 1))) if len(cols) else 0)
        ):
            ys = np.nonzero(m[:, c] & band)[0]
            if not len(ys):
                continue
            y = int(ys[np.argmin(np.abs(v[ys] - target))])
            pt = (c / g.px_per_mm + inset, v[y] / g.px_per_mm)
            fallback = fallback or pt
            if abs(v[y] - target) <= 0.3 * xh_px:
                out = pt
                break
        out = out or fallback
        self._anchor_cache[key] = out
        return out

    @staticmethod
    def _place(it: GlyphItem, u: float, v: float) -> tuple[float, float]:
        """A glyph-space point (mm, y up) on the page (mm, y down), with the item's rotation and shear."""
        r = math.radians(it.rot)
        cs, sn = math.cos(r), math.sin(r)
        k = it.scale
        return it.x + k * (cs * u + (cs * it.shear - sn) * v), it.y - k * (sn * u + (sn * it.shear + cs) * v)

    def _connector(self, a: GlyphItem, b: GlyphItem, scale: float) -> LineItem | None:
        ea = self._exit(a.glyph)
        nb = self._entry(b.glyph, ea[2]) if ea else None
        if ea is None or nb is None:
            return None
        p0, p3 = self._place(a, ea[0], ea[1]), self._place(b, *nb)
        xh = self.x_height * scale
        dx = p3[0] - p0[0]
        if dx < -0.4 * xh or dx > 2.2 * xh or abs(p3[1] - p0[1]) > 1.3 * xh:
            return None
        d = max(dx, 0.25 * xh)
        # A shallow pen swing: leave level, sag slightly, arrive level.
        p1 = (p0[0] + 0.4 * d, p0[1] + 0.05 * xh)
        p2 = (p3[0] - 0.4 * d, p3[1] + 0.05 * xh)
        # The letters are normalised towards the median pen width, so the join uses that width too.
        width = self.lib.stroke_mm * 0.5 * sum(it.scale / self._norm(it.glyph)[0] for it in (a, b)) * self.style.weight
        return LineItem(_bezier(p0, p1, p2, p3), width)

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
        # Joined writing keeps each letter steady on the line (the word as a whole wobbles instead,
        # see _tilt_words), so the joining strokes meet the letters cleanly.
        steady = 0.25 if self.style.joined else 1.0
        r, w, _sw = self._norm(g)
        js = 1 + rng.gauss(0, 0.03 * v)
        k = self.s * scale * extra_scale * js * r
        rot = rng.gauss(0, 1.3 * v * steady)
        jy = rng.gauss(0, 0.05 * self.lib.x_height_mm * self.s * scale * v * steady) + dy
        # Scaling a glyph up also thickens its strokes; compensate so it matches the surrounding letters.
        item = GlyphItem(g, 0.0, jy, k, rot, self.shear, w * (extra_scale**-0.8 if extra_scale > 1.05 else 1.0))
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
        joined = self.style.joined
        words: list[tuple[int, float]] = [(0, 0.0)]  # (first item index, start x) of each word
        links: list[tuple[int, int]] = []  # glyph item index pairs to connect
        last_glyph: int | None = None
        for ch in text:
            if ch == " ":
                run.width += self.space_width(rng, scale)
                prev = " "
                words.append((len(run.items), run.width))
                last_glyph = None
                continue
            if ch in ("\n", "\r", "\u200b"):
                continue
            cr = self._char_run(ch, rng, scale)
            link = joined and last_glyph is not None and _joins(prev, ch)
            if prev is None or prev == " ":
                gap = 0.0
            else:
                gap = self._letter_gap(rng, scale)
                if ch in _PUNCT_TIGHT or prev in _OPEN:
                    gap *= 0.55
            start = len(run.items)
            run.append(cr, gap)
            placed = start if len(run.items) == start + 1 and isinstance(run.items[start], GlyphItem) else None
            if link and placed is not None:
                links.append((last_glyph, placed))
            last_glyph = placed
            prev = ch
        if joined:
            self._tilt_words(run, words, rng, scale)
            for a, b in links:
                ln = self._connector(run.items[a], run.items[b], scale)
                if ln is not None:
                    run.items.append(ln)
        run.ascent = max(run.ascent, self.ascent(scale) * 0.6)
        run.descent = max(run.descent, 0.0)
        return run

    def _tilt_words(self, run: Run, words: list[tuple[int, float]], rng: random.Random, scale: float) -> None:
        """Give each word one small rotation and baseline offset, applied to all its letters together."""
        v = self.style.variation
        ends = [w[0] for w in words[1:]] + [len(run.items)]
        for (i0, x0), i1 in zip(words, ends, strict=True):
            if i1 <= i0:
                continue
            t = rng.gauss(0, 0.9 * v)
            dy0 = rng.gauss(0, 0.04 * self.x_height * scale * v)
            slope = math.tan(math.radians(t))
            for i in range(i0, i1):
                it = run.items[i]
                dy = dy0 - slope * (it.x - x0)  # a counter-clockwise tilt lifts the end of the word
                if isinstance(it, GlyphItem | FallbackItem):
                    run.items[i] = dataclasses.replace(it, y=it.y + dy, rot=it.rot + t)
                else:
                    run.items[i] = shift_item(it, 0.0, dy)
