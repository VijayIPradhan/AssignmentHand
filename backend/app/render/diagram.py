"""Hand-drawn diagrams inside answers.

An answer may contain a fenced block::

    ```diagram
    {"type": "plot", "x_label": "Feature 1", "y_label": "Feature 2",
     "series": [{"name": "Class A", "marker": "triangle", "points": [[1, 2], [2, 3]]},
                {"name": "Class B", "marker": "circle", "points": [[6, 6], [7, 5]]}],
     "points": [{"x": 5, "y": 4.5, "marker": "star", "label": "new point"}],
     "circles": [{"x": 5, "y": 4.5, "around_nearest": 3}],
     "caption": "KNN with k = 3: the new point is Class B"}
    ```

It is drawn like a student would: wobbly ink strokes at the pen width, slightly overshooting axes with
arrow heads, circles that do not quite close, and every word written with the user's own glyphs.

Supported (``type: "plot"``): axes with labels and optional ticks; series of points with markers
(triangle, circle, square, cross, plus, star, dot), optionally connected (line charts) or drawn as bars;
single points with labels; circles (radius in x units, or ``around_nearest: k`` to enclose the k nearest
plotted points); lines and arrows; free text labels; legend; caption.
"""

import itertools
import json
import math
import random
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

from app.render.engine import GlyphItem, HandwritingEngine, LineItem, Run, shift_item

Marker = Literal["triangle", "circle", "square", "cross", "plus", "star", "dot"]
MARKERS: tuple[str, ...] = ("triangle", "circle", "square", "cross", "plus", "star", "dot")
Pt = tuple[float, float]


class SeriesSpec(BaseModel):
    name: str = Field("", max_length=60)
    marker: Marker = "dot"
    points: list[tuple[float, float]] = Field(default_factory=list, max_length=200)
    connect: bool = False  # join the points in order (a line chart)
    bars: bool = False  # draw each point as a bar from the x axis (a bar chart)


class PointSpec(BaseModel):
    x: float
    y: float
    marker: Marker = "dot"
    label: str = Field("", max_length=60)


class CircleSpec(BaseModel):
    x: float
    y: float
    r: float | None = Field(None, gt=0)  # radius in x-axis units
    around_nearest: int | None = Field(None, ge=1, le=50)  # enclose the k nearest plotted points
    dashed: bool = False
    label: str = Field("", max_length=60)


class LineSpec(BaseModel):
    points: list[tuple[float, float]] = Field(min_length=2, max_length=100)
    arrow: bool = False
    dashed: bool = False
    label: str = Field("", max_length=60)


class LabelSpec(BaseModel):
    x: float
    y: float
    text: str = Field(max_length=80)


class PlotSpec(BaseModel):
    type: Literal["plot"] = "plot"
    title: str = Field("", max_length=80)
    caption: str = Field("", max_length=120)
    x_label: str = Field("", max_length=40)
    y_label: str = Field("", max_length=40)
    x_range: tuple[float, float] | None = None
    y_range: tuple[float, float] | None = None
    ticks: bool = False  # numbers along the axes
    size: Literal["small", "medium", "large"] = "medium"
    legend: bool = True
    series: list[SeriesSpec] = Field(default_factory=list, max_length=12)
    points: list[PointSpec] = Field(default_factory=list, max_length=50)
    circles: list[CircleSpec] = Field(default_factory=list, max_length=12)
    lines: list[LineSpec] = Field(default_factory=list, max_length=30)
    labels: list[LabelSpec] = Field(default_factory=list, max_length=30)

    @field_validator("x_range", "y_range")
    @classmethod
    def _range(cls, v):
        if v is not None and not v[1] > v[0]:
            raise ValueError("range must be [min, max] with max > min")
        return v


class DiagramError(ValueError):
    pass


def parse_spec(src: str) -> PlotSpec:
    try:
        data = json.loads(src)
    except json.JSONDecodeError as exc:
        raise DiagramError(f"the diagram is not valid JSON ({exc.msg} at line {exc.lineno})") from exc
    if not isinstance(data, dict):
        raise DiagramError("the diagram must be a JSON object")
    if data.get("type", "plot") != "plot":
        raise DiagramError(f'diagram type "{data.get("type")}" is not supported yet (only "plot")')
    try:
        spec = PlotSpec.model_validate(data)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"])
        raise DiagramError(f"diagram field {where}: {first['msg']}") from exc
    if not (spec.series or spec.points or spec.lines):
        raise DiagramError("the diagram has nothing to draw")
    return spec


@dataclass
class _Pen:
    """Draws hand-made strokes: gentle low-frequency wobble, no two strokes alike."""

    rng: random.Random
    width: float
    amp: float  # wobble amplitude (mm)

    def stroke(self, pts: list[Pt], closed: bool = False, dashed: bool = False) -> list[LineItem]:
        if closed:
            pts = [*pts, pts[0]]
        dense = _densify(pts, step=1.2)
        phase, freq = self.rng.uniform(0, 6.3), self.rng.uniform(0.15, 0.3)
        out: list[Pt] = []
        s = 0.0
        for i, (x, y) in enumerate(dense):
            if i:
                s += math.dist(dense[i - 1], (x, y))
            nx, ny = _normal(dense, i)
            j = self.amp * (math.sin(phase + s * freq) + 0.35 * math.sin(phase * 1.7 + s * freq * 2.3))
            out.append((x + nx * j, y + ny * j))
        if not dashed:
            return [LineItem(out, self.width)]
        items, seg, on, acc = [], [out[0]], True, 0.0
        for a, b in itertools.pairwise(out):
            acc += math.dist(a, b)
            if on:
                seg.append(b)
            if acc >= (1.6 if on else 1.1):
                if on and len(seg) > 1:
                    items.append(LineItem(seg, self.width))
                on, acc, seg = not on, 0.0, [b]
        if on and len(seg) > 1:
            items.append(LineItem(seg, self.width))
        return items

    def circle(self, c: Pt, r: float, dashed: bool = False) -> list[LineItem]:
        """A hand-drawn circle: slightly elliptical, and its end runs a little past its start."""
        start = self.rng.uniform(0, 2 * math.pi)
        squash = 1 + self.rng.uniform(-0.025, 0.025)
        n = max(16, int(2 * math.pi * r / 1.0))
        sweep = 2 * math.pi * (1.06 if not dashed else 1.0)
        pts = [
            (c[0] + r * squash * math.cos(start + sweep * k / n), c[1] + r / squash * math.sin(start + sweep * k / n))
            for k in range(n + 1)
        ]
        return self.stroke(pts, dashed=dashed)


def _box_dist(box: tuple[float, float, float, float], q: Pt) -> float:
    dx = max(box[0] - q[0], 0.0, q[0] - box[2])
    dy = max(box[1] - q[1], 0.0, q[1] - box[3])
    return math.hypot(dx, dy)


def _box_near_far(box: tuple[float, float, float, float], c: Pt) -> tuple[float, float]:
    """Nearest and farthest distance from ``c`` to a box."""
    near = _box_dist(box, c)
    far = max(math.dist(c, (x, y)) for x in (box[0], box[2]) for y in (box[1], box[3]))
    return near, far


def _densify(pts: list[Pt], step: float) -> list[Pt]:
    out = [pts[0]]
    for a, b in itertools.pairwise(pts):
        n = max(1, int(math.dist(a, b) / step))
        out += [(a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n) for k in range(1, n + 1)]
    return out


def _normal(pts: list[Pt], i: int) -> Pt:
    a, b = pts[max(0, i - 1)], pts[min(len(pts) - 1, i + 1)]
    dx, dy = b[0] - a[0], b[1] - a[1]
    n = math.hypot(dx, dy) or 1.0
    return -dy / n, dx / n


def _nice_ticks(lo: float, hi: float, n: int = 5) -> list[float]:
    raw = (hi - lo) / max(1, n)
    mag = 10 ** math.floor(math.log10(raw)) if raw > 0 else 1
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    first = math.ceil(lo / step) * step
    return [round(first + k * step, 10) for k in range(int((hi - first) / step + 1e-9) + 1)]


def _fmt(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else f"{v:g}"


class DiagramDrawer:
    def __init__(self, engine: HandwritingEngine, rng: random.Random, text_w: float, variation: float = 0.5):
        self.e = engine
        self.rng = rng
        self.text_w = text_w
        xh = engine.x_height
        self.pen = _Pen(rng, max(0.2, engine.stroke_mm * 0.95), amp=0.12 * xh * (0.4 + variation))
        self.xh = xh
        # Where markers and circles were placed (mm, before the final shift), for checks and tests.
        self.markers: list[tuple[str, Pt, float]] = []
        self.rings: list[tuple[Pt, float]] = []

    # ------------------------------------------------------------------ text
    def _text(self, text: str, x: float, y: float, scale: float = 0.85, anchor: str = "left") -> Run:
        """Handwritten text with its baseline at y; anchor left | center | right on x."""
        run = self.e.shape(text, self.rng, scale)
        dx = {"left": 0.0, "center": -run.width / 2, "right": -run.width}[anchor]
        out = Run([shift_item(it, x + dx, y) for it in run.items], run.width, run.ascent, run.descent)
        return out

    # ------------------------------------------------------------------ markers
    def _marker(self, kind: str, c: Pt, s: float) -> list[LineItem]:
        x, y = c
        p = self.pen
        if kind == "triangle":
            return p.stroke([(x, y - s), (x + s * 0.95, y + s * 0.7), (x - s * 0.95, y + s * 0.7)], closed=True)
        if kind == "circle":
            return p.circle(c, s * 0.8)
        if kind == "square":
            return p.stroke(
                [
                    (x - s * 0.75, y - s * 0.75),
                    (x + s * 0.75, y - s * 0.75),
                    (x + s * 0.75, y + s * 0.75),
                    (x - s * 0.75, y + s * 0.75),
                ],
                closed=True,
            )
        if kind == "cross":
            return [
                *p.stroke([(x - s * 0.8, y - s * 0.8), (x + s * 0.8, y + s * 0.8)]),
                *p.stroke([(x - s * 0.8, y + s * 0.8), (x + s * 0.8, y - s * 0.8)]),
            ]
        if kind == "plus":
            return [*p.stroke([(x - s, y), (x + s, y)]), *p.stroke([(x, y - s), (x, y + s)])]
        if kind == "star":
            r_out, r_in = s * 1.35, s * 0.55
            pts = [
                (
                    x + (r_out if k % 2 == 0 else r_in) * math.sin(k * math.pi / 5),
                    y - (r_out if k % 2 == 0 else r_in) * math.cos(k * math.pi / 5),
                )
                for k in range(10)
            ]
            return p.stroke(pts, closed=True)
        # dot: a small filled blob drawn as a short thick stroke
        return [LineItem([(x - s * 0.15, y), (x + s * 0.15, y + 0.01)], max(p.width * 2.6, s * 0.9))]

    # ------------------------------------------------------------------ plot
    def plot(self, spec: PlotSpec) -> Run:
        xh = self.xh
        # Sized in x-heights (so it shrinks with the handwriting when an answer is fitted to its space),
        # and never wider than the given share of the line.
        frac = {"small": 0.55, "medium": 0.7, "large": 0.9}[spec.size]
        in_xh = {"small": 30, "medium": 40, "large": 52}[spec.size]
        W = min(self.text_w * frac, in_xh * xh, self.text_w - 6 * xh)
        H = W * 0.62
        left_pad = (3.2 if spec.ticks else 1.6) * xh  # room for y ticks
        bottom_pad = (3.4 if spec.ticks else 3.0) * xh  # room for the x label (and ticks) below the axis
        # Data ranges: given, else the data's extent with a margin (axes start at 0 when the data is positive).
        xs, ys = self._all_xy(spec)
        x0, x1 = spec.x_range or self._auto(xs)
        y0, y1 = spec.y_range or self._auto(ys)
        ox, oy = left_pad, 0.0  # origin of the axes (y grows down; the x axis sits at y=0)

        def to(p: tuple[float, float]) -> Pt:
            return ox + (p[0] - x0) / (x1 - x0) * W, oy - (p[1] - y0) / (y1 - y0) * H

        items: list = []
        texts: list[Run] = []
        # Axes: drawn a little past the plotting area, with arrow heads.
        over = 2.2 * xh
        items += self.pen.stroke([(ox - 0.6 * xh, oy), (ox + W + over, oy)])
        items += self.pen.stroke([(ox, oy + 0.6 * xh), (ox, oy - H - over)])
        items += self._arrow_head((ox + W + over, oy), (1, 0))
        items += self._arrow_head((ox, oy - H - over), (0, -1))
        if spec.x_label:
            texts.append(self._text(spec.x_label, ox + W + over, oy + 2.0 * xh, 0.85, "right"))
        if spec.y_label:
            texts.append(self._text(spec.y_label, ox + 0.9 * xh, oy - H - over + 0.9 * xh, 0.85))
        if spec.ticks:
            for v in _nice_ticks(x0, x1):
                px, _ = to((v, y0))
                items += self.pen.stroke([(px, oy - 0.35 * xh), (px, oy + 0.35 * xh)])
                texts.append(self._text(_fmt(v), px, oy + 2.0 * xh, 0.7, "center"))
            for v in _nice_ticks(y0, y1):
                _, py = to((x0, v))
                items += self.pen.stroke([(ox - 0.35 * xh, py), (ox + 0.35 * xh, py)])
                texts.append(self._text(_fmt(v), ox - 0.8 * xh, py + 0.45 * xh, 0.7, "right"))
        else:
            texts.append(self._text("0", ox - 0.7 * xh, oy + 1.6 * xh, 0.7, "right"))

        ms = 0.75 * xh  # marker size
        placed: list[Pt] = []  # every plotted point (mm), for around_nearest circles
        for ser in spec.series:
            pts = [to(p) for p in ser.points]
            if ser.connect and len(pts) >= 2:
                items += self.pen.stroke(pts)
            for p in pts:
                if ser.bars:
                    bw = min(2.2 * xh, W / max(4, 2 * len(pts)))
                    items += self.pen.stroke(
                        [(p[0] - bw / 2, oy), (p[0] - bw / 2, p[1]), (p[0] + bw / 2, p[1]), (p[0] + bw / 2, oy)]
                    )
                else:
                    items += self._marker(ser.marker, p, ms)
                    self.markers.append((ser.marker, p, ms))
                placed.append(p)
        rings: list[tuple[Pt, float]] = []  # circles drawn (centre, radius)
        boxes: list[tuple[float, float, float, float]] = []  # legend and labels already placed
        point_labels: list[tuple[str, Pt, float]] = []
        for pt in spec.points:
            p = to((pt.x, pt.y))
            size = ms * (1.25 if pt.marker == "star" else 1.0)
            items += self._marker(pt.marker, p, size)
            self.markers.append((pt.marker, p, size))
            if pt.label:
                point_labels.append((pt.label, p, size + 0.6 * xh))
            placed.append(p)
        circle_labels: list[tuple[str, Pt, float]] = []
        for c in spec.circles:
            cp = to((c.x, c.y))
            if c.around_nearest:
                others = sorted(math.dist(cp, q) for q in placed if math.dist(cp, q) > 1e-6)
                if not others:
                    continue
                k = min(c.around_nearest, len(others))
                # The k nearest markers clearly inside (a hand-drawn circle wobbles and is not quite round),
                # the next point clearly outside: in the middle of the gap between them when it is tight.
                r = others[k - 1] + ms * 2.0
                if k < len(others):
                    r = min(r, max(others[k - 1] + ms * 1.2, (others[k - 1] + others[k]) / 2))
            else:
                r = (c.r or 1.0) / (x1 - x0) * W
            items += self.pen.circle(cp, r, dashed=c.dashed)
            rings.append((cp, r))
            self.rings.append((cp, r))
            if c.label:
                circle_labels.append((c.label, cp, r))
        line_labels: list[tuple[str, Pt]] = []
        for ln in spec.lines:
            pts = [to(p) for p in ln.points]
            items += self.pen.stroke(pts, dashed=ln.dashed)
            if ln.arrow:
                (ax, ay), (bx, by) = pts[-2], pts[-1]
                n = math.hypot(bx - ax, by - ay) or 1
                items += self._arrow_head(pts[-1], ((bx - ax) / n, (by - ay) / n))
            if ln.label:
                line_labels.append((ln.label, pts[len(pts) // 2]))

        if spec.legend and any(s.name for s in spec.series):
            entries = [s for s in spec.series if s.name]
            box_w = max(self.e.shape(s.name, random.Random(0), 0.8).width for s in entries) + 3.6 * xh
            box_h = len(entries) * 2.4 * xh
            corners = [  # top right, top left (beside the y axis), bottom right (above the x axis)
                (ox + W + over - 0.5 * xh - box_w, oy - H - over + 0.6 * xh),
                (ox + 1.5 * xh, oy - H - over + 2.6 * xh),
                (ox + W + over - 0.5 * xh - box_w, oy - box_h - 0.8 * xh),
            ]

            def crowd(c):
                bx = (c[0], c[1] - 1.6 * xh, c[0] + box_w, c[1] + box_h - 1.6 * xh)
                return self._overlaps(bx, placed, rings, boxes, ms), bx

            lx, ly = min(corners, key=lambda c: crowd(c)[0])
            boxes.append(crowd((lx, ly))[1])
            for i, s in enumerate(entries):
                y = ly + i * 2.4 * xh
                items += self._marker(s.marker if not s.bars else "square", (lx + 1.0 * xh, y - 0.4 * xh), ms * 0.9)
                texts.append(self._text(s.name, lx + 2.4 * xh, y, 0.8))

        for text, cp, r in circle_labels:  # just outside the circle, on one of its diagonals
            d = r * 0.7071 + 0.6 * xh
            cands = [
                (cp[0] + d, cp[1] - d, "left"),
                (cp[0] - d, cp[1] - d, "right"),
                (cp[0] + d, cp[1] + d + 1.2 * xh, "left"),
                (cp[0] - d, cp[1] + d + 1.2 * xh, "right"),
                (cp[0], cp[1] - r - 0.8 * xh, "center"),
            ]
            texts.append(self._place_label(text, cands, placed, rings, boxes, ms))
        for text, p, gap in point_labels:
            h = 1.2 * xh
            cands = [
                (p[0] + gap, p[1] - gap * 0.5, "left"),
                (p[0] - gap, p[1] - gap * 0.5, "right"),
                (p[0] + gap, p[1] + gap * 0.5 + h, "left"),
                (p[0] - gap, p[1] + gap * 0.5 + h, "right"),
                (p[0], p[1] - gap - 0.3 * xh, "center"),
                (p[0], p[1] + gap + h, "center"),
            ]
            others = [q for q in placed if q != p]
            w = self.e.shape(text, random.Random(0), 0.8).width
            if min(self._overlaps(self._label_box(c, w), others, rings, boxes, ms) for c in cands) == 0:
                texts.append(self._place_label(text, cands, others, rings, boxes, ms))
                continue
            # Crowded (e.g. inside a circle of neighbours): label outside, with a short pointer to the point.
            ring = next(((c, r) for c, r in rings if math.dist(c, p) < r), None)
            reach = (ring[1] - math.dist(ring[0], p) if ring else 0.0) + 2.4 * xh
            far = []
            for ang in (-45, -135, 45, 135, -90, 90):
                ux, uy = math.cos(math.radians(ang)), math.sin(math.radians(ang))
                tx, ty = p[0] + ux * reach, p[1] + uy * reach
                far.append((tx, ty + (1.0 * xh if uy > 0 else 0.0), "left" if ux >= 0 else "right"))
            label = self._place_label(text, far, others, rings, boxes, ms)
            texts.append(label)
            lb = boxes[-1]
            end = (min(max(p[0], lb[0]), lb[2]), min(max(p[1], lb[1]), lb[3]))  # nearest point of the label
            d = math.dist(p, end) or 1.0
            ux, uy = (end[0] - p[0]) / d, (end[1] - p[1]) / d
            a0 = (p[0] + ux * gap, p[1] + uy * gap)
            a1 = (end[0] - ux * 0.3 * xh, end[1] - uy * 0.3 * xh)
            if math.dist(a0, a1) > 0.8 * xh:
                items += self.pen.stroke([a1, a0])
                items += self._arrow_head(a0, (-ux, -uy))
        for text, (mx, my) in line_labels:
            cands = [
                (mx + 0.8 * xh, my - 0.8 * xh, "left"),
                (mx - 0.8 * xh, my - 0.8 * xh, "right"),
                (mx + 0.8 * xh, my + 2.0 * xh, "left"),
            ]
            texts.append(self._place_label(text, cands, placed, rings, boxes, ms))
        for lb in spec.labels:
            q = to((lb.x, lb.y))
            texts.append(self._text(lb.text, q[0], q[1], 0.8))

        top = -H - over - 1.2 * xh
        if spec.title:
            texts.append(self._text(spec.title, ox + W / 2, top - 0.6 * xh, 0.9, "center"))
            top -= 2.8 * xh
        bottom = bottom_pad
        if spec.caption:
            # A caption may run wider than a small drawing (up to the line), so it takes fewer lines.
            lines = self._wrap(spec.caption, min(self.text_w - 2 * xh, (W + over) * 1.6), 0.85)
            for k, line in enumerate(lines):
                texts.append(
                    self._text(line, ox + (W + over) / 2, bottom_pad + 1.6 * xh + k * 2.3 * xh, 0.85, "center")
                )
            bottom = bottom_pad + 1.6 * xh + (len(lines) - 1) * 2.3 * xh + 0.9 * xh
        run = Run(items, 0.0)
        for t in texts:
            run.items += t.items
        # Shift so the run's baseline (y = 0) is the diagram's bottom edge, x from 0.
        lefts = [min(x for x, _ in it.pts) if isinstance(it, LineItem) else it.x for it in run.items]
        min_x = min(0.0, *lefts) if lefts else 0.0
        dx, dy = -min_x, -bottom
        run.items = [shift_item(it, dx, dy) for it in run.items]
        self.shift = (dx, dy)
        rights = [
            max(x for x, _ in it.pts) if isinstance(it, LineItem) else it.x + it.glyph.width_mm * it.scale
            for it in run.items
            if isinstance(it, LineItem | GlyphItem)
        ]
        run.width = max(ox + W + over + dx, *rights) + 0.5 * xh  # a wide caption can reach past the axes
        run.ascent = -(top + dy) + 0.5 * xh
        run.descent = 0.6 * xh
        return run

    def _wrap(self, text: str, width: float, scale: float) -> list[str]:
        words, lines, cur = text.split(), [], ""
        measure = random.Random(0)
        for w in words:
            trial = f"{cur} {w}".strip()
            if cur and self.e.shape(trial, measure, scale).width > width:
                lines.append(cur)
                cur = w
            else:
                cur = trial
        return [*lines, cur] if cur else lines

    def _overlaps(self, box, points: list[Pt], rings: list, boxes: list, ms: float) -> int:
        """How many markers, circle outlines and placed labels a box touches."""
        n = sum(1 for q in points if _box_dist(box, q) < ms * 1.2)
        for c, r in rings:
            near, far = _box_near_far(box, c)
            if near <= r <= far:  # the outline passes through the box
                n += 1
        n += sum(1 for b in boxes if b[0] < box[2] and box[0] < b[2] and b[1] < box[3] and box[1] < b[3])
        return n

    def _place_label(self, text: str, cands: list, points: list[Pt], rings: list, boxes: list, ms: float) -> Run:
        """Write ``text`` at the candidate (x, baseline y, anchor) that touches the fewest things."""
        w = self.e.shape(text, random.Random(0), 0.8).width

        best = min(cands, key=lambda c: self._overlaps(self._label_box(c, w), points, rings, boxes, ms))
        boxes.append(self._label_box(best, w))
        return self._text(text, best[0], best[1], 0.8, best[2])

    def _label_box(self, c, w: float) -> tuple[float, float, float, float]:
        """Box (mm) of a label of width ``w`` written at candidate (x, baseline y, anchor)."""
        xh = self.xh
        left = {"left": c[0], "right": c[0] - w, "center": c[0] - w / 2}[c[2]]
        return (left - 0.3 * xh, c[1] - 1.3 * xh, left + w + 0.3 * xh, c[1] + 0.4 * xh)

    def _arrow_head(self, tip: Pt, d: Pt) -> list[LineItem]:
        s = 0.9 * self.xh
        bx, by = tip[0] - d[0] * s, tip[1] - d[1] * s
        nx, ny = -d[1] * s * 0.55, d[0] * s * 0.55
        return self.pen.stroke([(bx + nx, by + ny), tip, (bx - nx, by - ny)])

    @staticmethod
    def _all_xy(spec: PlotSpec) -> tuple[list[float], list[float]]:
        xs, ys = [], []
        for s in spec.series:
            for x, y in s.points:
                xs.append(x)
                ys.append(y)
            if s.bars:
                ys.append(0.0)
        for p in spec.points:
            xs.append(p.x)
            ys.append(p.y)
        for c in spec.circles:
            xs.append(c.x)
            ys.append(c.y)
        for ln in spec.lines:
            for x, y in ln.points:
                xs.append(x)
                ys.append(y)
        for lb in spec.labels:
            xs.append(lb.x)
            ys.append(lb.y)
        return xs or [0.0, 1.0], ys or [0.0, 1.0]

    @staticmethod
    def _auto(vals: list[float]) -> tuple[float, float]:
        lo, hi = min(vals), max(vals)
        if hi - lo < 1e-9:
            lo, hi = lo - 1, hi + 1
        pad = (hi - lo) * 0.15
        lo = 0.0 if lo >= 0 and lo - pad < (hi - lo) * 0.6 else lo - pad
        return lo, hi + pad
