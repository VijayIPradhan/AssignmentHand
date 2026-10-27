"""Lay out parsed math (see ``markup.parse_math``) as handwriting: glyphs plus hand-drawn rules."""

import random

from app.render.engine import HandwritingEngine, LineItem, Run, shift_item
from app.render.markup import MFrac, MNode, MOver, MRow, MScript, MSpace, MSqrt, MSym, MTextRun

SCRIPT_SCALE = 0.68
FRAC_SCALE = 0.85
MIN_SCALE = 0.45


def _wobbly(x0: float, y0: float, x1: float, y1: float, rng: random.Random, amp: float, n: int = 6) -> list:
    pts = []
    for k in range(n + 1):
        t = k / n
        j = 0.0 if k in (0, n) else rng.gauss(0, amp)
        pts.append((x0 + (x1 - x0) * t, y0 + (y1 - y0) * t + j))
    return pts


def _place(run: Run, child: Run, x: float, dy: float) -> None:
    """Add ``child`` at (x, dy) relative to ``run``'s origin without advancing its width."""
    run.items += [shift_item(i, x, dy) for i in child.items]
    run.ascent = max(run.ascent, child.ascent - dy)
    run.descent = max(run.descent, child.descent + dy)


class MathLayout:
    def __init__(self, engine: HandwritingEngine, rng: random.Random):
        self.e = engine
        self.rng = rng

    def _amp(self) -> float:
        return 0.04 * self.e.x_height * self.e.style.variation

    def layout(self, node: MNode, scale: float = 1.0) -> Run:
        scale = max(scale, MIN_SCALE)
        xh = self.e.x_height * scale
        if isinstance(node, MRow):
            run = Run()
            prev_op = False
            for idx, child in enumerate(node.items):
                r = self.layout(child, scale)
                is_op = isinstance(child, MSym) and child.op
                gap = 0.0
                if idx > 0:
                    gap = 0.12 * xh
                    if is_op or prev_op:
                        gap = 0.38 * xh
                run.append(r, gap)
                prev_op = is_op
            if not node.items:
                run.ascent, run.descent = xh, 0.0
            return run
        if isinstance(node, MSym):
            return self.e.shape(node.ch, self.rng, scale)
        if isinstance(node, MTextRun):
            return self.e.shape(node.text, self.rng, scale)
        if isinstance(node, MSpace):
            return Run(width=max(0.0, node.em * xh * 1.6))
        if isinstance(node, MScript):
            base = self.layout(node.base, scale)
            run = Run()
            run.append(base)
            sx = base.width + 0.08 * xh
            widths = [0.0]
            if node.sup is not None:
                sup = self.layout(node.sup, scale * SCRIPT_SCALE)
                _place(run, sup, sx, -max(base.ascent - sup.ascent * 0.45, xh * 0.75))
                widths.append(sup.width)
            if node.sub is not None:
                sub = self.layout(node.sub, scale * SCRIPT_SCALE)
                _place(run, sub, sx, 0.35 * xh + sub.ascent * 0.2)
                widths.append(sub.width)
            run.width = sx + max(widths)
            return run
        if isinstance(node, MFrac):
            num = self.layout(node.num, scale * FRAC_SCALE)
            den = self.layout(node.den, scale * FRAC_SCALE)
            width = max(num.width, den.width) + 0.5 * xh
            axis = -0.5 * xh  # fraction bar height (negative = above baseline)
            gap = 0.25 * xh
            run = Run(width=width)
            _place(run, num, (width - num.width) / 2, axis - gap - num.descent)
            _place(run, den, (width - den.width) / 2, axis + gap + den.ascent)
            if node.bar:
                run.items.append(
                    LineItem(_wobbly(0.05 * xh, axis, width - 0.05 * xh, axis, self.rng, self._amp()), self.e.stroke_mm)
                )
            return run
        if isinstance(node, MSqrt):
            body = self.layout(node.body, scale)
            top = body.ascent + 0.25 * xh
            hook = 0.55 * xh
            run = Run()
            idx_w = 0.0
            if node.index is not None:
                ir = self.layout(node.index, scale * 0.55)
                run.append(ir, dy=-top * 0.55)
                idx_w = max(0.0, ir.width - hook * 0.5)
            x0 = idx_w
            d = body.descent * 0.6
            pts = [
                (x0, -0.35 * xh),
                (x0 + hook * 0.35, -0.48 * xh),
                (x0 + hook * 0.7, d),
                (x0 + hook * 1.25, -top),
                (x0 + hook * 1.35 + body.width + 0.2 * xh, -top + self.rng.gauss(0, self._amp())),
            ]
            run.items.append(LineItem(pts, self.e.stroke_mm))
            run.width = x0 + hook * 1.35
            run.append(body, gap=0.1 * xh)
            run.width += 0.25 * xh
            run.ascent = max(run.ascent, top + self.e.stroke_mm)
            run.descent = max(run.descent, d)
            return run
        if isinstance(node, MOver):
            body = self.layout(node.body, scale)
            y = -(body.ascent + 0.2 * xh)
            run = Run()
            run.append(body)
            w = body.width
            if node.kind == "bar":
                run.items.append(LineItem(_wobbly(0, y, w, y, self.rng, self._amp(), 3), self.e.stroke_mm))
            elif node.kind == "vec":
                a = 0.22 * xh
                run.items.append(LineItem(_wobbly(0, y, w, y, self.rng, self._amp(), 3), self.e.stroke_mm))
                run.items.append(LineItem([(w - a, y - a * 0.7), (w, y), (w - a, y + a * 0.7)], self.e.stroke_mm))
            elif node.kind == "hat":
                run.items.append(
                    LineItem(
                        [(w * 0.2, y + 0.1 * xh), (w * 0.5, y - 0.25 * xh), (w * 0.8, y + 0.1 * xh)], self.e.stroke_mm
                    )
                )
            else:  # dot
                c = w / 2
                run.items.append(LineItem([(c - 0.05 * xh, y), (c + 0.05 * xh, y)], self.e.stroke_mm * 1.6))
            run.ascent = max(run.ascent, -y + 0.3 * xh)
            return run
        raise TypeError(f"unknown math node {type(node).__name__}")
