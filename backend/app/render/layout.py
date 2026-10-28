"""Flow questions and answers onto pages using measured glyph geometry.

Lines snap to a baseline grid (the ruled lines, also used invisibly on blank paper). Tall lines
(fractions, tables, figures) take as many grid rows as they need. Pagination keeps headings with
the following text and avoids single orphan/widow lines in longer paragraphs.
"""

import hashlib
import io
import math
import random
from dataclasses import dataclass, field

from PIL import Image

from app.render.engine import (
    FallbackItem,
    GlyphItem,
    HandwritingEngine,
    ImageItem,
    LineItem,
    Run,
    TofuItem,
    shift_item,
)
from app.render.markup import (
    DisplayMath,
    Figure,
    Heading,
    Inline,
    ListItem,
    Math,
    Paragraph,
    Table,
    Text,
    parse_blocks,
    parse_math,
)
from app.render.mathlayout import MathLayout
from app.render.settings import RenderSettings

MIN_FIT_SCALE = 0.5


@dataclass
class AnswerSpace:
    """Blank region on a source PDF page reserved for this answer (mm from the top-left corner)."""

    page: int
    top: float
    bottom: float
    left: float
    # Bands (top, bottom) in mm inside the space that hold printed text (e.g. "[10 Marks]") - not written over
    blocked: list[tuple[float, float]] = field(default_factory=list)


@dataclass
class SectionInput:
    key: str
    number: str
    question_text: str
    answer: str
    marks: float | None = None
    figures: list[bytes] = field(default_factory=list)
    space: AnswerSpace | None = None


@dataclass
class Line:
    run: Run
    x: float = 0.0  # offset from left text edge
    block: int = 0
    index: int = 0  # line index within its block
    block_len: int = 1
    keep_with_next: bool = False
    space_before: int = 0  # extra blank grid rows before this line
    clearance: float = 0.6  # min mm between previous line's descent and this line's ascent
    top_border: list[LineItem] = field(default_factory=list)  # drawn if first on page / first row
    is_table_row: bool = False
    first_table_row: bool = False


@dataclass
class Page:
    number: int
    items: list = field(default_factory=list)
    width: float = 0.0
    height: float = 0.0
    source_page: int | None = None  # index into the source PDF when writing on the original pages
    sections: list[str] = field(default_factory=list)


@dataclass
class Frame:
    page: Page
    top: float  # baseline-grid origin and upper limit (mm)
    bottom: float
    left: float
    blocked: list[tuple[float, float]] = field(default_factory=list)  # bands to skip (mm)


@dataclass
class LayoutResult:
    pages: list[Page]
    warnings: list[dict]
    width_mm: float
    height_mm: float


def _seed(*parts) -> int:
    return int.from_bytes(hashlib.sha256("|".join(map(str, parts)).encode()).digest()[:8], "big")


@dataclass
class _Token:
    run: Run
    space_before: bool
    underline: bool = False
    splittable: str | None = None  # source text if it may be hyphenated


class Layouter:
    def __init__(self, engine: HandwritingEngine, settings: RenderSettings):
        self.e = engine
        self.s = settings
        self.page_w, self.page_h = settings.page_mm()
        m = settings.margins
        self.text_w = self.page_w - m.left - m.right
        self.pitch = settings.line_spacing_mm
        self.warnings: list[dict] = []
        self._block_counter = 0
        if self.e.x_height * 2.4 > self.pitch:
            self.warnings.append(
                {
                    "type": "line_overlap_risk",
                    "message": "Handwriting is large for the line spacing; ascenders and descenders may touch. "
                    "Reduce handwriting size or increase line spacing.",
                }
            )

    # ---------------------------------------------------------------- tokens & line breaking

    def _tokens(self, inlines: list[Inline], rng: random.Random, scale: float = 1.0) -> list[_Token]:
        toks: list[_Token] = []
        pending_space = False
        for part in inlines:
            if isinstance(part, Math):
                tree, unsupported = parse_math(part.src)
                for u in unsupported:
                    self._warn("unsupported_math", f"Math command {u} is not supported; it is written as-is")
                run = MathLayout(self.e, rng).layout(tree, scale)
                toks.append(_Token(run, pending_space if toks else False))
                pending_space = False
                continue
            text = part.text
            if not text:
                continue
            words = text.split(" ")
            for wi, w in enumerate(words):
                if wi > 0:
                    pending_space = True
                if not w:
                    if wi == 0 and text.startswith(" "):
                        pending_space = True
                    continue
                run = self.e.shape(w, rng, scale)
                sb = pending_space if toks else False
                toks.append(_Token(run, sb, part.underline, w))
                pending_space = False
            if text.endswith(" "):
                pending_space = True
        return toks

    def _split_token(self, tok: _Token, avail: float, rng: random.Random, scale: float) -> list[_Token]:
        """Hyphenate a word that is longer than a whole line."""
        text = tok.splittable or ""
        pieces, cur = [], ""
        for ch in text:
            trial = self.e.shape(cur + ch + "-", rng, scale)
            if cur and trial.width > avail:
                pieces.append(cur)
                cur = ch
            else:
                cur += ch
        if cur:
            pieces.append(cur)
        out = []
        for i, p in enumerate(pieces):
            label = p + ("-" if i < len(pieces) - 1 else "")
            out.append(_Token(self.e.shape(label, rng, scale), tok.space_before if i == 0 else False, tok.underline))
        return out

    def _break(
        self, toks: list[_Token], first_avail: float, avail: float, rng: random.Random, scale: float = 1.0
    ) -> list[Run]:
        lines: list[Run] = []
        cur = Run()
        cur_tokens = 0
        underline_spans: list[tuple[float, float]] = []
        i = 0
        toks = list(toks)

        def finish():
            nonlocal cur, cur_tokens, underline_spans
            for x0, x1 in underline_spans:
                y = self.e.descent(scale) * 0.35 + 0.25
                cur.items.append(
                    LineItem([(x0, y), ((x0 + x1) / 2, y + rng.gauss(0, 0.08)), (x1, y)], self.e.stroke_mm * 0.9)
                )
            lines.append(cur)
            cur, cur_tokens, underline_spans = Run(), 0, []

        while i < len(toks):
            tok = toks[i]
            width_limit = first_avail if not lines else avail
            gap = self.e.space_width(rng, scale) if (cur_tokens and tok.space_before) else 0.0
            if cur_tokens and cur.width + gap + tok.run.width > width_limit:
                finish()
                continue
            if not cur_tokens and tok.run.width > width_limit:
                if tok.splittable and len(tok.splittable) > 1:
                    toks[i : i + 1] = self._split_token(tok, width_limit, rng, scale)
                    tok = toks[i]
                else:
                    f = max(MIN_FIT_SCALE, width_limit / tok.run.width)
                    tok = _Token(_scaled(tok.run, f), tok.space_before, tok.underline)
                    if tok.run.width > width_limit + 0.5:
                        self._warn("overflow", "An expression is too wide for the line even when shrunk")
                    else:
                        self._warn("scaled_down", "A wide expression was shrunk to fit the line")
            x0 = cur.width + gap
            cur.append(tok.run, gap)
            if tok.underline:
                if underline_spans and abs(underline_spans[-1][1] - (x0 - gap)) < 1e-6:
                    underline_spans[-1] = (underline_spans[-1][0], cur.width)
                else:
                    underline_spans.append((x0, cur.width))
            cur_tokens += 1
            i += 1
        if cur_tokens or not lines:
            finish()
        return [self._drift(r, rng) for r in lines]

    def _drift(self, run: Run, rng: random.Random) -> Run:
        """Natural baseline wander along a line (applied to glyphs only)."""
        v = self.s.variation
        if v <= 0:
            return run
        amp = 0.09 * self.e.x_height * v
        wl = rng.uniform(55, 120)
        ph = rng.uniform(0, 2 * math.pi)
        slope = math.tan(math.radians(rng.gauss(0, 0.25 * v)))
        items = []
        for it in run.items:
            if isinstance(it, GlyphItem):
                dy = amp * math.sin(2 * math.pi * it.x / wl + ph) - slope * it.x
                items.append(shift_item(it, 0.0, dy))
            else:
                items.append(it)
        run.items = items
        return run

    # ---------------------------------------------------------------- blocks -> lines

    def _new_block(self) -> int:
        self._block_counter += 1
        return self._block_counter

    def _lines_from_inlines(
        self, inlines, rng, first_indent=0.0, indent=0.0, keep=False, prefix: Run | None = None, scale: float = 1.0
    ) -> list[Line]:
        toks = self._tokens(inlines, rng, scale)
        if prefix is not None:
            toks.insert(0, _Token(prefix, False))
            if len(toks) > 1:
                toks[1].space_before = True
        runs = self._break(toks, self.text_w - first_indent, self.text_w - indent, rng, scale)
        b = self._new_block()
        return [
            Line(r, first_indent if k == 0 else indent, b, k, len(runs), keep_with_next=keep)
            for k, r in enumerate(runs)
        ]

    def _heading_lines(self, sec: SectionInput, rng: random.Random) -> list[Line]:
        if self.s.paper == "source":
            # The question is already printed on the page; optionally start with e.g. "Ans."
            prefix = self.s.answer_prefix.strip()
            return self._lines_from_inlines([Text(prefix)], rng, keep=True) if prefix else []
        label = sec.number.strip()
        if label and label[-1] not in ".):":
            label += "."
        inlines: list[Inline] = []
        if label:
            inlines.append(Text(label, underline=self.s.underline_question_numbers))
        rest = []
        if self.s.include_question_text and sec.question_text.strip():
            # The first paragraph shares the line with the number; tables, lists or maths that follow
            # are laid out as blocks (kept with the answer) instead of being flattened into one line.
            blocks = parse_blocks(sec.question_text)
            if blocks and isinstance(blocks[0], Paragraph):
                inlines += [Text(" "), *blocks[0].inlines]
                blocks = blocks[1:]
            rest = blocks
        if self.s.show_marks and sec.marks is not None:
            mk = int(sec.marks) if float(sec.marks).is_integer() else sec.marks
            inlines.append(Text(f" [{mk}]"))
        if not inlines and not rest:
            return []
        lines = self._lines_from_inlines(inlines, rng, keep=True) if inlines else []
        for blk in rest:
            for ln in self._block_lines(blk, rng, sec):
                ln.keep_with_next = True
                lines.append(ln)
        return lines

    def _table_lines(self, tbl: Table, rng: random.Random) -> list[Line]:
        pad = 1.2
        ncol = len(tbl.rows[0])
        natural = [0.0] * ncol
        shaped_rows = []
        for row in tbl.rows:
            cells = []
            for c, cell in enumerate(row):
                toks = self._tokens(cell, rng)
                w = sum(t.run.width for t in toks) + self.e.space_width(rng) * max(0, len(toks) - 1)
                natural[c] = max(natural[c], w + 2 * pad, self.e.x_height * 2)
                cells.append(toks)
            shaped_rows.append(cells)
        total = sum(natural)
        if total > self.text_w:
            minw = [min(n, self.e.x_height * 6) for n in natural]
            free = self.text_w - sum(minw)
            extra = [n - m for n, m in zip(natural, minw, strict=True)]
            se = sum(extra) or 1.0
            widths = [m + max(0.0, free) * e / se for m, e in zip(minw, extra, strict=True)]
            if free < 0:
                self._warn("table_narrow", "Table has many columns; some cells are very narrow")
        else:
            widths = natural
        xs = [0.0]
        for w in widths:
            xs.append(xs[-1] + w)
        b = self._new_block()
        out: list[Line] = []
        amp = 0.05 * self.e.x_height * self.s.variation
        sw = self.e.stroke_mm * 0.8
        asc = self.pitch * 0.72
        for r, cells in enumerate(shaped_rows):
            row = Run()
            n_lines = 1
            for c, toks in enumerate(cells):
                avail = max(widths[c] - 2 * pad, self.e.x_height * 2)
                runs = self._break(toks, avail, avail, rng) if toks else [Run()]
                n_lines = max(n_lines, len(runs))
                for k, cr in enumerate(runs):
                    row.items += [shift_item(i, xs[c] + pad, k * self.pitch) for i in cr.items]
            desc = (n_lines - 1) * self.pitch + (self.pitch - asc)
            row.width = xs[-1]
            row.ascent, row.descent = asc, desc
            row.items.append(LineItem(_wob(0, desc, xs[-1], desc, rng, amp), sw))
            for x in xs:
                row.items.append(LineItem(_wob(x, -asc, x, desc, rng, amp, vertical=True), sw))
            top = [LineItem(_wob(0, -asc, xs[-1], -asc, rng, amp), sw)]
            if tbl.header and r == 0:
                row.items.append(LineItem(_wob(0, desc - 0.5, xs[-1], desc - 0.5, rng, amp), sw))
            out.append(
                Line(
                    row,
                    0.0,
                    b,
                    r,
                    len(shaped_rows),
                    clearance=0.0,
                    top_border=top,
                    is_table_row=True,
                    first_table_row=r == 0,
                    keep_with_next=r == 0,
                )
            )
        return out

    def _figure_lines(self, data: bytes | None, idx: int) -> list[Line]:
        if not data:
            self._warn("figure_missing", f"[[figure:{idx}]] does not match any figure from the source document")
            return []
        with Image.open(io.BytesIO(data)) as im:
            w_px, h_px = im.size
        w = min(self.text_w * 0.8, w_px / 150 * 25.4)
        h = w * h_px / max(w_px, 1)
        max_h = (self.page_h - self.s.margins.top - self.s.margins.bottom) * 0.6
        if h > max_h:
            w, h = w * max_h / h, max_h
        run = Run([ImageItem(data, 0.0, -h, w, h)], w, h + 1.0, 1.0)
        return [Line(run, (self.text_w - w) / 2, self._new_block())]

    def section_lines(self, sec: SectionInput, first: bool) -> list[Line]:
        rng = random.Random(_seed(self.s.seed, sec.key, sec.answer, sec.question_text))
        lines = self._heading_lines(sec, rng)
        if lines and not first:
            lines[0].space_before = self.s.question_spacing_lines
        for blk in parse_blocks(sec.answer):
            lines += self._block_lines(blk, rng, sec)
        if not sec.answer.strip():
            self._warn("empty_answer", f"Question {sec.number or '?'} has no answer text")
        return lines

    def _block_lines(self, blk, rng: random.Random, sec: SectionInput) -> list[Line]:
        lines: list[Line] = []
        if isinstance(blk, Paragraph):
            lines += self._lines_from_inlines(blk.inlines, rng, first_indent=self.s.paragraph_indent_mm)
        elif isinstance(blk, Heading):
            lines += self._lines_from_inlines([_ul(i) for i in blk.inlines], rng, keep=True)
        elif isinstance(blk, ListItem):
            base = 2.0 + blk.level * 6.0
            mrun = self.e.shape(blk.marker, rng)
            hang = base + max(mrun.width + self.e.space_width(rng), self.e.x_height * 1.8)
            pre = Run()
            pre.append(mrun)
            pre.width = hang - base
            lines += self._lines_from_inlines(blk.inlines, rng, first_indent=base, indent=hang, prefix=pre)
        elif isinstance(blk, DisplayMath):
            tree, unsupported = parse_math(blk.src)
            for u in unsupported:
                self._warn("unsupported_math", f"Math command {u} is not supported; it is written as-is")
            run = MathLayout(self.e, rng).layout(tree)
            if run.width > self.text_w:
                f = max(MIN_FIT_SCALE, self.text_w / run.width)
                run = _scaled(run, f)
                self._warn(
                    "scaled_down" if run.width <= self.text_w + 0.5 else "overflow",
                    "A displayed equation was shrunk to fit the line",
                )
            lines.append(Line(run, max(0.0, (self.text_w - run.width) / 2), self._new_block()))
        elif isinstance(blk, Table):
            lines += self._table_lines(blk, rng)
        elif isinstance(blk, Figure):
            data = sec.figures[blk.index - 1] if 0 < blk.index <= len(sec.figures) else None
            lines += self._figure_lines(data, blk.index)
        return lines

    # ---------------------------------------------------------------- pagination

    def _grid(self, y: float, top: float) -> float:
        k = math.ceil((y - top) / self.pitch - 1e-6)
        return top + max(1, k) * self.pitch

    def _place(self, line: Line, prev: tuple[float, float] | None, first_on_page: bool, top: float) -> float:
        """Baseline y for ``line`` given previous (baseline, descent) in the frame."""
        if prev is None:
            return self._grid(top + min(line.run.ascent, self.pitch * 0.9) + 0.5, top)
        pb, pd = prev
        rows = 1 + (0 if first_on_page else line.space_before)
        cand = pb + rows * self.pitch
        # Ordinary text advances one rule: ascenders/descenders of neighbouring lines may interleave,
        # as they do on real ruled paper. Only unusually tall content (fractions, tables, figures)
        # claims extra rows to avoid collisions.
        tall = line.run.ascent > self.e.ascent() * 1.2 + 0.5 or pd > self.e.descent() * 1.3 + 0.5
        if tall or line.is_table_row:
            cand = max(cand, pb + pd + line.clearance + line.run.ascent)
        return self._grid(cand, top)

    def _clear(self, b: float, ln: Line, fr: Frame) -> float:
        """Move a baseline down past any blocked band its line would touch."""
        moved = True
        while moved:
            moved = False
            for t, btm in fr.blocked:
                if b - ln.run.ascent < btm and b + ln.run.descent > t:
                    b = self._grid(btm + ln.run.ascent, fr.top)
                    moved = True
        return b

    def _fits(self, lines: list[Line], prev, first_on_page: bool, fr: Frame) -> bool:
        for ln in lines:
            b = self._clear(self._place(ln, prev, first_on_page, fr.top), ln, fr)
            if b + ln.run.descent > fr.bottom + 0.01:
                return False
            prev, first_on_page = (b, ln.run.descent), False
        return True

    def flow(self, lines: list[Line], first: Frame, make_extra) -> list[tuple[Frame, list]]:
        """Place lines into ``first``, then into extra frames from ``make_extra()``.

        Returns [(frame, items)] without modifying any page, so callers can retry (fit-to-space)."""
        frames: list[tuple[Frame, list]] = [(first, [])]
        prev: tuple[float, float] | None = None
        i = 0
        while i < len(lines):
            fr, items = frames[-1]
            ln = lines[i]
            first_line = prev is None
            if not first_line:
                group = [ln]
                if ln.keep_with_next:
                    j = i + 1
                    while j < len(lines) and lines[j].keep_with_next and lines[j].block == ln.block:
                        group.append(lines[j])
                        j += 1
                    if j < len(lines):
                        group += [x for x in lines[j : j + 2] if x.block == lines[j].block]
                elif ln.block_len >= 4 and ln.index == 0:
                    group = lines[i : i + 2]  # no orphan first line
                elif ln.block_len >= 4 and ln.index == ln.block_len - 2 and ln.index >= 2:
                    group = lines[i : i + 2]  # no widow last line
                if not self._fits(group, prev, False, fr):
                    frames.append((make_extra(), []))
                    prev = None
                    continue
            b = self._clear(self._place(ln, prev, first_line, fr.top), ln, fr)
            if b + ln.run.descent > fr.bottom + 0.01:
                fresh_page = fr.top <= self.s.margins.top + 0.01 and not items
                if not fresh_page:
                    # Nothing more fits here (or the frame is a small answer box): continue on a new page.
                    frames.append((make_extra(), []))
                    prev = None
                    continue
                self._warn("overflow", "A line or table row is taller than the printable page area")
            x = fr.left + ln.x
            items += [shift_item(it, x, b) for it in ln.run.items]
            if ln.is_table_row and (ln.first_table_row or first_line):
                items += [shift_item(it, x, b) for it in ln.top_border]
            prev = (b, ln.run.descent)
            i += 1
        return frames

    def _blank_page(self, number: int) -> Page:
        return Page(number, width=self.page_w, height=self.page_h)

    def _blank_frame(self, page: Page) -> Frame:
        m = self.s.margins
        return Frame(page, m.top, page.height - m.bottom, m.left)

    def paginate(self, lines: list[Line]) -> list[Page]:
        pages = [self._blank_page(1)]

        def extra():
            pages.append(self._blank_page(len(pages) + 1))
            return self._blank_frame(pages[-1])

        for fr, items in self.flow(lines, self._blank_frame(pages[0]), extra):
            fr.page.items += items
        return pages

    # ---------------------------------------------------------------- decorations & checks

    def decorate(self, pages: list[Page]) -> None:
        m = self.s.margins
        for p in pages:
            if p.source_page is not None:
                continue  # the original page has its own header and footer
            rng = random.Random(_seed(self.s.seed, "page", p.number))
            if self.s.header_text.strip():
                run = self.e.shape(self.s.header_text.strip(), rng, 0.85)
                y = max(self.e.ascent(0.85) + 2, m.top - self.pitch * 0.6)
                p.items += [shift_item(it, m.left, y) for it in run.items]
            fy = p.height - max(self.e.descent() + 2, m.bottom * 0.4)
            if self.s.footer_text.strip():
                run = self.e.shape(self.s.footer_text.strip(), rng, 0.8)
                p.items += [shift_item(it, m.left, fy) for it in run.items]
            if self.s.page_numbers:
                run = self.e.shape(str(p.number), rng, 0.9)
                x = p.width - m.right - run.width
                p.items += [shift_item(it, x, fy) for it in run.items]

    def check_bounds(self, pages: list[Page]) -> None:
        m = self.s.margins
        tol = 1.5
        for p in pages:
            x0, x1 = m.left - tol, p.width - m.right + tol
            for it in p.items:
                if isinstance(it, GlyphItem):
                    w = it.glyph.width_mm * it.scale
                    right = it.x + w + abs(it.shear) * it.glyph.top_mm * it.scale
                    if it.x < x0 - 3 or right > x1 + 3 or it.x < 0 or right > p.width:
                        self._warn("clipping", "Text extends beyond the margins", page=p.number)
                        break

    def _warn(self, kind: str, message: str, page: int | None = None) -> None:
        w = {"type": kind, "message": message}
        if page is not None:
            w["page"] = page
        if w not in self.warnings:
            self.warnings.append(w)

    def _finish(self, pages: list[Page]) -> LayoutResult:
        self.decorate(pages)
        self.check_bounds(pages)
        u = self.e.usage
        if u.fallback:
            self._warn(
                "missing_glyph",
                "Not in your handwriting samples, drawn with a printed fallback font: " + " ".join(sorted(u.fallback)),
            )
        if u.approximated:
            self._warn("approximated_glyph", "Approximated from similar letters: " + " ".join(sorted(u.approximated)))
        if u.unsupported:
            codes = ", ".join(f"U+{ord(c):04X}" for c in sorted(u.unsupported))
            self._warn("unsupported_character", f"Characters with no available glyph were drawn as boxes: {codes}")
        return LayoutResult(pages, self.warnings, pages[0].width, pages[0].height)

    def run(self, sections: list[SectionInput]) -> LayoutResult:
        lines: list[Line] = []
        for k, sec in enumerate(sections):
            lines += self.section_lines(sec, first=k == 0)
        pages = self.paginate(lines) if lines else [self._blank_page(1)]
        return self._finish(pages)

    def run_on_source(self, sections: list[SectionInput], page_sizes: list[tuple[float, float]]) -> LayoutResult:
        """Write each answer into the blank space below its question on the original pages.

        An answer that does not fit continues on extra pages inserted after its source page, with a
        warning. With ``fit_to_space`` the handwriting is first shrunk (down to a floor) to fit."""
        m = self.s.margins
        src = [Page(0, width=w, height=h, source_page=i) for i, (w, h) in enumerate(page_sizes)]
        extras: dict[int, list[Page]] = {i: [] for i in range(len(src))}
        trailing: list[Page] = []
        base_size, base_pitch = self.e.style.x_height_mm, self.pitch
        for sec in sections:
            sp = sec.space
            host = src[sp.page] if sp is not None and sp.page < len(src) else None
            ref = host or (src[-1] if src else self._blank_page(0))
            left = max(m.left, sp.left) if sp is not None else m.left
            self.text_w = ref.width - left - m.right
            scale = 1.0
            while True:
                self.e.set_size(base_size * scale)
                self.pitch = base_pitch * scale
                lines = self.section_lines(sec, first=True)
                new_pages: list[Page] = []

                def extra(new_pages=new_pages, ref=ref):
                    pg = Page(0, width=ref.width, height=ref.height)
                    new_pages.append(pg)
                    return self._blank_frame(pg)

                first = Frame(host, sp.top, sp.bottom, left, list(sp.blocked)) if host is not None else extra()
                placed = self.flow(lines, first, extra)
                overflow = len(new_pages) - (0 if host is not None else 1)
                if overflow <= 0 or not self.s.fit_to_space or scale <= self.s.min_fit_scale + 1e-6:
                    break
                scale = max(self.s.min_fit_scale, scale * 0.92)
            for fr, items in placed:
                fr.page.items += items
                fr.page.sections.append(sec.key)
            (extras[sp.page] if host is not None else trailing).extend(new_pages)
            label = sec.number or "?"
            if scale < 0.999:
                self._warn(
                    "shrunk_to_fit", f"Answer {label} was written {round((1 - scale) * 100)}% smaller to fit its space"
                )
            if host is not None and overflow > 0:
                self._warn(
                    "exceeds_space",
                    f"Answer {label} does not fit the space on page {sp.page + 1}; {overflow} extra page(s) "
                    "were added after it. Shorten the answer or reduce the handwriting size.",
                )
            if host is None:
                self._warn("no_answer_space", f"No answer space was found for question {label}; it is on a new page")
        self.e.set_size(base_size)
        self.pitch = base_pitch
        pages: list[Page] = []
        for i, p in enumerate(src):
            pages.append(p)
            pages += extras[i]
        pages += trailing
        for n, p in enumerate(pages, 1):
            p.number = n
        return self._finish(pages)


def _ul(i: Inline) -> Inline:
    return Text(i.text, True) if isinstance(i, Text) else i


def _wob(x0, y0, x1, y1, rng, amp, vertical=False) -> list[tuple[float, float]]:
    n = 4
    pts = []
    for k in range(n + 1):
        t = k / n
        j = 0.0 if k in (0, n) else rng.gauss(0, amp)
        if vertical:
            pts.append((x0 + j, y0 + (y1 - y0) * t))
        else:
            pts.append((x0 + (x1 - x0) * t, y0 + j))
    return pts


def _scaled(run: Run, f: float) -> Run:
    """Uniformly shrink a run about its origin."""
    items = []
    for it in run.items:
        if isinstance(it, GlyphItem):
            items.append(GlyphItem(it.glyph, it.x * f, it.y * f, it.scale * f, it.rot, it.shear, it.weight))
        elif isinstance(it, LineItem):
            items.append(LineItem([(x * f, y * f) for x, y in it.pts], it.width * max(f, 0.7)))
        elif isinstance(it, ImageItem):
            items.append(ImageItem(it.data, it.x * f, it.y * f, it.w * f, it.h * f))
        elif isinstance(it, FallbackItem):
            items.append(FallbackItem(it.ch, it.x * f, it.y * f, it.size_mm * f, it.shear, it.rot, it.font))
        elif isinstance(it, TofuItem):
            items.append(TofuItem(it.x * f, it.y * f, it.w * f, it.h * f))
    return Run(items, run.width * f, run.ascent * f, run.descent * f)
