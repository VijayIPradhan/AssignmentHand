"""Turn extracted page lines into assignment questions.

Heuristic and deliberately conservative: anything uncertain is flagged ``needs_review`` with a reason
so the user confirms it in the question editor. Not every paragraph becomes a question.
"""

import itertools
import re
from dataclasses import dataclass, field

from app.ingestion.pdf import FigureInfo, LineInfo, PageInfo

# "Q1", "Q.1", "Question 1", "Q1a" - a sub-part letter must be lowercase and attached ("Q1 Explain" is not 1E)
Q_RE = re.compile(
    r"^\s*(?:Q(?:uestion)?|Ques|Qn)\s*\.?\s*(\d{1,3})(?-i:([a-z]))?(?![A-Za-z])\s*[.):\-–]?\s*(.*)$", re.I
)
NUM_RE = re.compile(r"^\s*(\d{1,3})\s*[.)]\s+(\S.*)$")
NUM_BARE_RE = re.compile(r"^\s*(\d{1,3})\s*[.)]\s*$")
ROMAN = r"(?:viii|vii|vi|iv|ix|v|iii|ii|i|x)"
SUB_RE = re.compile(rf"^\s*\(?\s*([a-h]|{ROMAN})\s*\)\s*(.*)$")
SUB_DOT_RE = re.compile(r"^\s*([a-h])\.\s+(\S.*)$")
SECTION_RE = re.compile(r"^\s*(section|part)\s+([A-Z0-9]{1,3})\b(.*)$", re.I)
MARKS_RES = [
    re.compile(r"[\(\[]\s*(\d+(?:\.\d+)?)\s*(?:marks?|mks?|pts?|points?|m)\s*[\)\]]", re.I),
    re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s*marks?\b\.?\s*$", re.I),
    re.compile(r"\[\s*(\d+(?:\.\d+)?)\s*\]\s*$"),
]
PAGE_NO_RE = re.compile(r"^\s*(?:page\s*)?[-–]?\s*\d{1,3}\s*(?:of\s*\d{1,3})?\s*[-–]?\s*$", re.I)
QUESTION_WORDS = re.compile(
    r"^(explain|describe|discuss|define|calculate|compute|find|derive|prove|show|state|list|compare|"
    r"evaluate|analy[sz]e|outline|justify|determine|write|solve|what|why|how|which|when|who|draw|identify|"
    r"illustrate|differentiate|distinguish|give|suggest|assess|critically|construct|design)\b",
    re.I,
)


@dataclass
class QuestionDraft:
    number: str
    text: str
    marks: float | None = None
    instructions: str | None = None
    context: str | None = None
    pages: list[int] = field(default_factory=list)
    confidence: float = 1.0
    reasons: list[str] = field(default_factory=list)
    has_visual: bool = False
    excluded: bool = False
    figure_indexes: list[int] = field(default_factory=list)
    y_range: list[tuple[int, float, float]] = field(default_factory=list)  # (page, y0, y1)
    x0: float = 0.0  # left edge of the question text, points
    answer_space: dict | None = None  # {"page", "top", "bottom", "x0", "fraction"} in points

    @property
    def needs_review(self) -> bool:
        return bool(self.reasons)


@dataclass
class Segmentation:
    questions: list[QuestionDraft]
    preamble: str
    notes: list[str]


def _norm(t: str) -> str:
    # Ignore digits and spacing so "Page 1 of 2" / "Page 2 of 2" and stray characters still match.
    return re.sub(r"[\d\s_]+", "", t.lower())


EDGE = 0.10


def strip_headers_footers(lines: list[LineInfo], pages: list[PageInfo]) -> tuple[list[LineInfo], list[LineInfo]]:
    """Remove running headers/footers and page-template text repeated on every page.

    Returns (kept lines, removed lines that bound answer space from below, e.g. footers and signature
    lines). Question lines are never treated as template text ("Q1 ..." and "Q2 ..." may differ only
    in their number). A repeated block printed before the first question on the first page (e.g. an
    instructions box on every answer sheet) keeps that first copy so it can become the preamble; every
    other copy, and any repeated block below a question, is page furniture and is dropped."""
    heights = {p.index: p.height for p in pages}
    n_pages = len(pages)
    need = max(2, (n_pages + 1) // 2)

    def is_question(ln: LineInfo) -> bool:
        return ln.kind == "text" and bool(Q_RE.match(ln.text) or NUM_RE.match(ln.text))

    pages_with: dict[str, set[int]] = {}
    first_q_y: dict[int, float] = {}
    for ln in lines:
        if is_question(ln):
            first_q_y[ln.page] = min(first_q_y.get(ln.page, ln.y0), ln.y0)
            continue
        k = _norm(ln.text)
        if len(k) >= 3:
            pages_with.setdefault(k, set()).add(ln.page)
    repeated = {k for k, ps in pages_with.items() if n_pages >= 2 and len(ps) >= need}
    first_page = {k: min(ps) for k, ps in pages_with.items()}
    out, footers = [], []
    for ln in lines:
        if is_question(ln):
            out.append(ln)
            continue
        h = heights[ln.page]
        at_edge = ln.y0 < EDGE * h or ln.y1 > (1 - EDGE) * h
        k = _norm(ln.text)
        below_question = ln.page in first_q_y and ln.y0 > first_q_y[ln.page]
        if at_edge and (k in repeated or PAGE_NO_RE.match(ln.text)):
            if ln.y1 > 0.5 * h:
                footers.append(ln)
            continue
        if k in repeated:
            if below_question:
                footers.append(ln)
                continue
            if ln.page != first_page[k]:
                continue
        out.append(ln)
    return out, footers


def extract_marks(text: str) -> tuple[str, float | None]:
    for rx in MARKS_RES:
        m = rx.search(text)
        if m:
            val = float(m.group(1))
            if 0 < val <= 200:
                return (text[: m.start()] + text[m.end() :]).strip(" \t-–:"), val
    return text, None


def _join(lines: list[LineInfo]) -> str:
    parts: list[str] = []
    for ln in lines:
        t = ln.text.strip()
        if not t:
            continue
        if ln.kind == "table":
            parts.append("\n\n" + t + "\n\n")
        elif parts and parts[-1].endswith("-") and not parts[-1].endswith(" -"):
            parts[-1] = parts[-1][:-1] + t  # re-join hyphenated line breaks
        else:
            parts.append(t)
    out = ""
    for p in parts:
        if p.startswith("\n") or out.endswith("\n") or not out:
            out += p
        else:
            out += " " + p
    return re.sub(r"\n{3,}", "\n\n", out).strip()


@dataclass
class _Start:
    idx: int
    number: str
    rest: str
    sub: str | None = None


def _main_starts(lines: list[LineInfo]) -> tuple[list[_Start], str]:
    q = []
    for i, ln in enumerate(lines):
        if ln.kind != "text":
            continue
        m = Q_RE.match(ln.text)
        if m and (m.group(3) or i + 1 < len(lines)):
            q.append(_Start(i, m.group(1), m.group(3) or "", m.group(2)))
    if q:
        return q, "labelled"
    starts: list[_Start] = []
    last = 0
    for i, ln in enumerate(lines):
        if ln.kind != "text":
            continue
        m = NUM_RE.match(ln.text) or NUM_BARE_RE.match(ln.text)
        if not m:
            continue
        n = int(m.group(1))
        rest = m.group(2) if m.re is NUM_RE else ""
        # Accept plausible next numbers only; this keeps numbered lists inside a question intact.
        if (last == 0 and n <= 3) or (0 < n - last <= 3) or n == last:
            starts.append(_Start(i, str(n), rest))
            last = n
    if len(starts) >= 2 or (len(starts) == 1 and QUESTION_WORDS.match(starts[0].rest or "")):
        return starts, "numbered"
    return [], "none"


def _split_subparts(body: list[LineInfo]) -> list[tuple[str | None, list[LineInfo]]]:
    """Split a question's lines at (a)/(b)/(i) markers. Returns [(label|None, lines)]."""
    parts: list[tuple[str | None, list[LineInfo]]] = [(None, [])]
    seen: list[str] = []
    for ln in body:
        m = SUB_RE.match(ln.text) if ln.kind == "text" else None
        if not m and ln.kind == "text":
            m = SUB_DOT_RE.match(ln.text)
        label = m.group(1).lower() if m else None
        if label is not None:
            ok = (not seen and label in ("a", "i")) or (seen and _follows(seen[-1], label)) or label in seen
            if ok:
                seen.append(label)
                rest = m.group(2) or ""
                parts.append(
                    (
                        label,
                        [
                            LineInfo(
                                rest,
                                ln.page,
                                ln.y0,
                                ln.y1,
                                ln.x0,
                                ln.size,
                                ln.bold,
                                ln.confidence,
                                math=ln.math,
                                unmapped=ln.unmapped,
                            )
                        ]
                        if rest
                        else [],
                    )
                )
                continue
        parts[-1][1].append(ln)
    return parts


_ROMANS = ["i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x"]


def _follows(prev: str, cur: str) -> bool:
    if prev in _ROMANS and cur in _ROMANS and _ROMANS.index(cur) == _ROMANS.index(prev) + 1:
        return True
    return len(prev) == 1 and len(cur) == 1 and ord(cur) == ord(prev) + 1


def _ranges(lines: list[LineInfo]) -> list[tuple[int, float, float]]:
    by_page: dict[int, list[float]] = {}
    for ln in lines:
        r = by_page.setdefault(ln.page, [ln.y0, ln.y1])
        r[0], r[1] = min(r[0], ln.y0), max(r[1], ln.y1)
    return [(p, a, b) for p, (a, b) in sorted(by_page.items())]


def _finish(d: QuestionDraft, lines: list[LineInfo]) -> QuestionDraft:
    d.pages = sorted({ln.page for ln in lines})
    d.y_range = _ranges(lines)
    if lines:
        d.x0 = min(ln.x0 for ln in lines)
    if lines:
        d.confidence = min(ln.confidence for ln in lines)
    if any(ln.unmapped for ln in lines):
        d.reasons.append("contains symbols that could not be decoded")
    if any(ln.math for ln in lines):
        d.reasons.append("contains a formula; check that symbols were extracted correctly")
    if any(ln.kind == "table" for ln in lines):
        d.reasons.append("contains a table converted to text")
    if d.confidence < 0.8:
        d.reasons.append(f"low text confidence ({d.confidence:.0%})")
    words = len(d.text.split())
    if words < 3:
        d.reasons.append("very short; may be a heading rather than a question")
    elif words > 450:
        d.reasons.append("very long; may contain several questions")
    return d


def segment(lines: list[LineInfo], pages: list[PageInfo], figures: list[FigureInfo]) -> Segmentation:
    lines, footers = strip_headers_footers(lines, pages)
    notes: list[str] = []
    starts, scheme = _main_starts(lines)
    drafts: list[QuestionDraft] = []

    if scheme == "none":
        notes.append("No question numbering was found; paragraphs are suggested as questions for you to confirm.")
        drafts = _paragraph_fallback(lines)
        preamble = ""
    else:
        preamble_lines = lines[: starts[0].idx]
        section_instr: str | None = None
        pre = []
        for ln in preamble_lines:
            if ln.kind == "text" and SECTION_RE.match(ln.text) and len(ln.text.split()) <= 14:
                section_instr = ln.text.strip()
            else:
                pre.append(ln)
        preamble = _join(pre)
        for k, st in enumerate(starts):
            end = starts[k + 1].idx if k + 1 < len(starts) else len(lines)
            first = lines[st.idx]
            body_lines = []
            if st.rest:
                body_lines.append(
                    LineInfo(
                        st.rest,
                        first.page,
                        first.y0,
                        first.y1,
                        first.x0,
                        first.size,
                        first.bold,
                        first.confidence,
                        math=first.math,
                        unmapped=first.unmapped,
                    )
                )
            trailing_section = None
            for ln in lines[st.idx + 1 : end]:
                sm = SECTION_RE.match(ln.text) if ln.kind == "text" else None
                if sm and len(ln.text.split()) <= 14:
                    trailing_section = ln.text.strip()
                    continue
                body_lines.append(ln)
            parts = _split_subparts(body_lines)
            stem_lines = parts[0][1]
            if len(parts) > 1:
                stem_text, stem_marks = extract_marks(_join(stem_lines))
                for label, plines in parts[1:]:
                    text, marks = extract_marks(_join(plines))
                    num = f"{st.number}{st.sub or ''}({label})"
                    d = QuestionDraft(num, text, marks, section_instr, stem_text or None)
                    if stem_marks and marks is None:
                        d.context = (stem_text + f" [{stem_marks:g} marks in total]").strip()
                    drafts.append(_finish(d, stem_lines + plines))
            else:
                text, marks = extract_marks(_join(stem_lines))
                drafts.append(
                    _finish(QuestionDraft(st.number + (st.sub or ""), text, marks, section_instr), stem_lines)
                )
            if trailing_section:
                section_instr = trailing_section
        _check_numbering(drafts, [s.number for s in starts])

    _attach_visuals(drafts, figures, pages)
    _answer_regions(drafts, pages, footers)
    return Segmentation(drafts, preamble, notes)


def _answer_regions(drafts: list[QuestionDraft], pages: list[PageInfo], footers: list[LineInfo]) -> None:
    """Record the blank space after each question on its last page (for writing on the source PDF).

    The space runs from the end of the question to the next question on the same page, else to the
    page footer (or bottom margin)."""
    heights = {p.index: p.height for p in pages}
    footer_top: dict[int, float] = {}
    for f in footers:
        footer_top[f.page] = min(footer_top.get(f.page, f.y0), f.y0)
    starts = [(d.y_range[0][0], d.y_range[0][1]) for d in drafts if d.y_range]
    for d in drafts:
        if not d.y_range:
            continue
        page, _y0, y1 = d.y_range[-1]
        h = heights[page]
        nxt = [y for p, y in starts if p == page and y > y1 + 1]
        bottom = min(nxt) - 6 if nxt else footer_top.get(page, h * 0.95) - 6
        d.answer_space = {
            "page": page,
            "top": y1 + 4,
            "bottom": bottom,
            "x0": d.x0,
            "fraction": max(0.0, (bottom - y1) / h),
        }


def _check_numbering(drafts: list[QuestionDraft], mains: list[str]) -> None:
    nums = [int(n) for n in mains]
    seen: set[int] = set()
    prev = None
    first_by_main: dict[int, QuestionDraft] = {}
    for d in drafts:
        m = re.match(r"\d+", d.number)
        if m:
            first_by_main.setdefault(int(m.group()), d)
    for n in nums:
        d = first_by_main.get(n)
        if d is None:
            continue
        if n in seen:
            d.reasons.append(f"duplicate question number {n}")
        elif prev is not None and n != prev + 1:
            d.reasons.append(f"numbering jumps from {prev} to {n}; a question may be missing")
        elif prev is None and n != 1:
            d.reasons.append(f"numbering starts at {n}")
        seen.add(n)
        prev = n


def _paragraph_fallback(lines: list[LineInfo]) -> list[QuestionDraft]:
    paras: list[list[LineInfo]] = []
    gaps = [b.y0 - a.y1 for a, b in itertools.pairwise(lines) if a.page == b.page and b.y0 > a.y1]
    # Lower quartile approximates the normal line gap even when most gaps are paragraph breaks.
    med = sorted(gaps)[len(gaps) // 4] if gaps else 4.0
    for ln in lines:
        if paras and paras[-1][-1].page == ln.page and ln.y0 - paras[-1][-1].y1 <= max(2.0, med * 1.8):
            paras[-1].append(ln)
        else:
            paras.append([ln])
    out = []
    n = 0
    for p in paras:
        text, marks = extract_marks(_join(p))
        if len(text.split()) < 4:
            continue
        n += 1
        looks_like_q = text.rstrip().endswith("?") or bool(QUESTION_WORDS.match(text))
        d = QuestionDraft(str(n), text, marks)
        d = _finish(d, p)
        d.confidence = min(d.confidence, 0.5)
        d.reasons.insert(0, "no numbering found; confirm this is a question")
        d.excluded = not looks_like_q
        out.append(d)
    return out


def _attach_visuals(drafts: list[QuestionDraft], figures: list[FigureInfo], pages: list[PageInfo]) -> None:
    for fi, fig in enumerate(figures):
        cy = (fig.bbox[1] + fig.bbox[3]) / 2
        best = None
        for d in drafts:
            for page, y0, _y1 in d.y_range:
                if page == fig.page and y0 - 40 <= cy:
                    best = d  # last question starting above the figure on that page
        if best is None:
            best = next((d for d in drafts if fig.page in d.pages), None)
        if best is not None:
            best.has_visual = True
            best.figure_indexes.append(fi)
    graphic_pages = {p.index for p in pages if p.vector_graphics > 25}
    for d in drafts:
        if re.search(r"\b(figure|fig\.|diagram|graph|shown|below|above|table)\b", d.text, re.I) and (
            set(d.pages) & graphic_pages or d.figure_indexes
        ):
            d.has_visual = True
        if d.has_visual:
            d.reasons.append("refers to a figure or diagram; the AI sees only the text and attached images")
