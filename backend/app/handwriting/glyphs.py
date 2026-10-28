"""Glyph bitmaps harvested from samples, their vector outlines, and the serialized glyph library.

Coordinates: a glyph's outline is expressed in millimetres with x=0 at the left edge of the ink and
y=0 on the baseline, y pointing up. Descenders therefore have negative y.
"""

import base64
import json
from dataclasses import dataclass, field

import cv2
import numpy as np

from app.handwriting.charset import ASCENDER_CHARS, X_HEIGHT_CHARS

LIBRARY_FORMAT = 1


@dataclass
class Glyph:
    char: str
    bitmap: np.ndarray  # uint8 mask, ink=255, tightly cropped
    px_per_mm: float
    baseline_px: float  # baseline y in bitmap rows (0 = top row); may lie outside the bitmap
    source: str = "template"
    sample_id: str = ""
    id: str = ""
    x_height_px: float = 0.0  # free-form glyphs: x-height of the source line, for scale normalisation
    _outlines: dict = field(default_factory=dict, repr=False, compare=False)

    @property
    def width_mm(self) -> float:
        return self.bitmap.shape[1] / self.px_per_mm

    @property
    def top_mm(self) -> float:
        return self.baseline_px / self.px_per_mm

    @property
    def bottom_mm(self) -> float:
        return (self.baseline_px - self.bitmap.shape[0]) / self.px_per_mm

    @property
    def height_mm(self) -> float:
        return self.bitmap.shape[0] / self.px_per_mm

    def to_json(self) -> dict:
        ok, buf = cv2.imencode(".png", self.bitmap)
        assert ok
        return {
            "id": self.id,
            "png": base64.b64encode(buf.tobytes()).decode(),
            "ppm": round(self.px_per_mm, 4),
            "baseline": round(self.baseline_px, 2),
            "source": self.source,
            "sample": self.sample_id,
        }

    @classmethod
    def from_json(cls, char: str, d: dict) -> "Glyph":
        arr = np.frombuffer(base64.b64decode(d["png"]), dtype=np.uint8)
        bmp = cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)
        return cls(
            char, bmp, float(d["ppm"]), float(d["baseline"]), d.get("source", ""), d.get("sample", ""), d.get("id", "")
        )


def crop_to_ink(mask: np.ndarray, pad: int = 2) -> tuple[np.ndarray, int, int] | None:
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        return None
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    h, w = mask.shape
    y0p, x0p = max(0, y0 - pad), max(0, x0 - pad)
    y1p, x1p = min(h, y1 + pad), min(w, x1 + pad)
    return mask[y0p:y1p, x0p:x1p].copy(), int(y0p), int(x0p)


def stroke_width_px(mask: np.ndarray) -> float:
    """Approximate pen width: for thin strokes, area ~= length*width and perimeter ~= 2*length."""
    contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
    perim = sum(cv2.arcLength(c, True) for c in contours)
    area = float((mask > 0).sum())
    return 2 * area / perim if perim > 0 else 0.0


def slant_deg(mask: np.ndarray) -> float | None:
    """Writing slant in degrees (positive leans right), by shear search.

    The shear that makes vertical strokes most upright maximises the peakiness of the column
    projection profile. Robust to letter shape asymmetry, unlike moment-based estimates."""
    if int((mask > 0).sum()) < 30:
        return None
    h, _w = mask.shape
    pad = int(h * 0.6) + 2
    m = cv2.copyMakeBorder((mask > 0).astype(np.float32), 0, 0, pad, pad, cv2.BORDER_CONSTANT, value=0)
    best, best_score = 0.0, -1.0
    for deg in np.arange(-30, 30.1, 1.5):
        k = np.tan(np.radians(deg))
        # x' = x - k*(h-1-y): undo a rightward lean of `deg`, anchored at the bottom row
        M = np.float32([[1, k, -k * (h - 1)], [0, 1, 0]])
        sh = cv2.warpAffine(m, M, (m.shape[1], h), flags=cv2.INTER_NEAREST, borderValue=0)
        col = sh.sum(axis=0)
        score = float((col * col).sum())
        if score > best_score + 1e-6:
            best, best_score = float(deg), score
    return best


def outlines(g: Glyph, weight: float = 1.0) -> list[list[tuple[float, float]]]:
    """Vector outlines (mm, baseline origin) for a glyph, optionally thickened (>1) or thinned (<1)."""
    weight = round(weight, 2)
    cached = g._outlines.get(weight)
    if cached is None:
        cached = g._outlines[weight] = _trace(g, weight)
    return cached


def _trace(g: Glyph, weight: float) -> list[list[tuple[float, float]]]:
    up = 3
    pad = 6
    m = cv2.copyMakeBorder(g.bitmap, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=0)
    m = cv2.resize(m.astype(np.float32), None, fx=up, fy=up, interpolation=cv2.INTER_LINEAR)
    m = cv2.GaussianBlur(m, (0, 0), up * 0.55)
    if weight != 1.0:
        sw = max(1.0, stroke_width_px((g.bitmap > 127).astype(np.uint8) * 255)) * up
        r = round(abs(weight - 1.0) * sw / 2)
        if r > 0:
            k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
            m = cv2.dilate(m, k) if weight > 1 else cv2.erode(m, k)
    binm = (m > 127).astype(np.uint8)
    contours, _ = cv2.findContours(binm, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
    ppm = g.px_per_mm * up
    off = pad * up
    polys = []
    for c in contours:
        if cv2.contourArea(c) < up * up * 2:
            continue
        approx = cv2.approxPolyDP(c, 0.35 * up, True)
        if len(approx) < 3:
            continue
        pts = [
            (round((p[0][0] - off) / ppm, 3), round((g.baseline_px * up - (p[0][1] - off)) / ppm, 3)) for p in approx
        ]
        polys.append(pts)
    return polys


@dataclass
class GlyphLibrary:
    glyphs: dict[str, list[Glyph]] = field(default_factory=dict)
    x_height_mm: float = 3.0
    cap_height_mm: float = 4.5
    stroke_mm: float = 0.45
    slant_deg: float = 0.0
    letter_gap_ratio: float = 0.18  # gap between letters / x-height
    word_gap_ratio: float = 0.9  # gap between words / x-height
    line_spacing_ratio: float = 3.2  # baseline-to-baseline / x-height (freeform only)
    joined_writing: bool = False  # the free-form samples were written joined (cursive)

    def add(self, g: Glyph) -> None:
        lst = self.glyphs.setdefault(g.char, [])
        g.id = g.id or f"{ord(g.char):x}-{len(lst)}"
        lst.append(g)

    def variants(self, ch: str) -> list[Glyph]:
        return self.glyphs.get(ch, [])

    def has(self, ch: str) -> bool:
        return bool(self.glyphs.get(ch))

    def coverage(self) -> dict[str, int]:
        return {ch: len(v) for ch, v in sorted(self.glyphs.items())}

    def compute_metrics(self) -> dict:
        """Derive size/shape metrics from the glyph set (overrides defaults where data exists)."""
        xh = [g.top_mm for ch in X_HEIGHT_CHARS for g in self.variants(ch) if g.top_mm > 0.5]
        caps = [g.top_mm for ch in "ABDEFHIKLMNPRTUVWXYZ" for g in self.variants(ch) if g.top_mm > 0.5]
        if xh:
            self.x_height_mm = float(np.median(xh))
        if caps:
            self.cap_height_mm = float(np.median(caps))
        elif xh:
            self.cap_height_mm = self.x_height_mm * 1.45
        sws = [stroke_width_px(g.bitmap) / g.px_per_mm for v in self.glyphs.values() for g in v if g.bitmap.size > 50]
        if sws:
            self.stroke_mm = float(np.median(sws))
        slants = [
            s
            for ch in ASCENDER_CHARS | set("lI1")
            for g in self.variants(ch)
            if (s := slant_deg(g.bitmap)) is not None and abs(s) < 35
        ]
        if slants:
            self.slant_deg = float(np.median(slants))
        return self.metrics()

    def metrics(self) -> dict:
        return {
            "x_height_mm": round(self.x_height_mm, 3),
            "cap_height_mm": round(self.cap_height_mm, 3),
            "stroke_mm": round(self.stroke_mm, 3),
            "slant_deg": round(self.slant_deg, 2),
            "letter_gap_ratio": round(self.letter_gap_ratio, 3),
            "word_gap_ratio": round(self.word_gap_ratio, 3),
            "line_spacing_ratio": round(self.line_spacing_ratio, 3),
            "joined_writing": self.joined_writing,
            "glyph_count": sum(len(v) for v in self.glyphs.values()),
            "char_count": len(self.glyphs),
        }

    def to_bytes(self) -> bytes:
        return json.dumps(
            {
                "format": LIBRARY_FORMAT,
                "metrics": self.metrics(),
                "glyphs": {ch: [g.to_json() for g in v] for ch, v in self.glyphs.items()},
            }
        ).encode()

    @classmethod
    def from_bytes(cls, data: bytes) -> "GlyphLibrary":
        d = json.loads(data)
        if d.get("format") != LIBRARY_FORMAT:
            raise ValueError("unsupported glyph library format")
        m = d["metrics"]
        lib = cls(
            x_height_mm=m["x_height_mm"],
            cap_height_mm=m["cap_height_mm"],
            stroke_mm=m["stroke_mm"],
            slant_deg=m["slant_deg"],
            letter_gap_ratio=m["letter_gap_ratio"],
            word_gap_ratio=m["word_gap_ratio"],
            line_spacing_ratio=m.get("line_spacing_ratio", 3.2),
            joined_writing=bool(m.get("joined_writing", False)),
        )
        for ch, lst in d["glyphs"].items():
            lib.glyphs[ch] = [Glyph.from_json(ch, gd) for gd in lst]
        return lib
