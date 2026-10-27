"""Typed (digital) export of questions and answers, and a side-by-side review copy."""

import io
import re
from xml.sax.saxutils import escape

from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.render.fonts import fallback_font
from app.render.layout import SectionInput
from app.render.markup import (
    DisplayMath,
    Figure,
    Heading,
    ListItem,
    Math,
    MFrac,
    MNode,
    MOver,
    MRow,
    MScript,
    MSpace,
    MSqrt,
    MSym,
    MTextRun,
    Text,
    parse_blocks,
    parse_inlines,
    parse_math,
)
from app.render.markup import (
    Paragraph as MPara,
)
from app.render.markup import (
    Table as MTable,
)

_SUP = str.maketrans("0123456789+-=()n", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿ")
_SUB = str.maketrans("0123456789+-=()", "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎")


def math_text(node: MNode) -> str:
    """Linear Unicode rendering of a math tree for typed output."""
    if isinstance(node, MRow):
        out = []
        for it in node.items:
            t = math_text(it)
            out.append(f" {t} " if isinstance(it, MSym) and it.op else t)
        return re.sub(r"\s+", " ", "".join(out)).strip()
    if isinstance(node, MSym):
        return node.ch
    if isinstance(node, MTextRun):
        return node.text
    if isinstance(node, MSpace):
        return " "
    if isinstance(node, MFrac):
        n, d = math_text(node.num), math_text(node.den)
        n = f"({n})" if len(n) > 1 else n
        d = f"({d})" if len(d) > 1 else d
        return f"{n}/{d}"
    if isinstance(node, MScript):
        b = math_text(node.base)
        if node.sub is not None:
            s = math_text(node.sub)
            b += s.translate(_SUB) if all(c in "0123456789+-=()" for c in s) else f"_({s})"
        if node.sup is not None:
            s = math_text(node.sup)
            b += s.translate(_SUP) if all(c in "0123456789+-=()n" for c in s) else f"^({s})"
        return b
    if isinstance(node, MSqrt):
        return f"√({math_text(node.body)})"
    if isinstance(node, MOver):
        return math_text(node.body) + {"bar": "̄", "vec": "⃗", "hat": "̂", "dot": "̇"}[node.kind]
    return ""


def _inline_xml(inlines) -> str:
    out = []
    for x in inlines:
        if isinstance(x, Math):
            out.append(f"<i>{escape(math_text(parse_math(x.src)[0]))}</i>")
        elif isinstance(x, Text):
            t = escape(x.text)
            out.append(f"<b>{t}</b>" if x.underline else t)
    return "".join(out)


def _styles():
    font = fallback_font()
    ss = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=ss["BodyText"], fontName=font, fontSize=10.5, leading=15, alignment=TA_LEFT)
    q = ParagraphStyle("q", parent=body, fontSize=11, leading=15, textColor=HexColor("#1f2937"), spaceBefore=10)
    h = ParagraphStyle("h", parent=body, fontSize=16, leading=20, spaceAfter=6)
    small = ParagraphStyle("s", parent=body, fontSize=9, leading=12, textColor=HexColor("#555555"))
    return body, q, h, small


def _answer_flowables(text: str, body) -> list:
    flow = []
    for blk in parse_blocks(text):
        if isinstance(blk, MPara):
            flow.append(Paragraph(_inline_xml(blk.inlines), body))
        elif isinstance(blk, Heading):
            flow.append(Paragraph(f"<b>{_inline_xml(blk.inlines)}</b>", body))
        elif isinstance(blk, ListItem):
            flow.append(
                Paragraph(f"{escape(blk.marker if blk.marker != '-' else '•')} {_inline_xml(blk.inlines)}", body)
            )
        elif isinstance(blk, DisplayMath):
            flow.append(Paragraph(f"<i>{escape(math_text(parse_math(blk.src)[0]))}</i>", body))
        elif isinstance(blk, MTable):
            data = [[Paragraph(_inline_xml(c), body) for c in row] for row in blk.rows]
            t = Table(data, hAlign="LEFT")
            t.setStyle(
                TableStyle([("GRID", (0, 0), (-1, -1), 0.5, HexColor("#888888")), ("VALIGN", (0, 0), (-1, -1), "TOP")])
            )
            flow.append(t)
        elif isinstance(blk, Figure):
            flow.append(Paragraph(f"[Figure {blk.index}]", body))
        flow.append(Spacer(1, 4))
    return flow


def digital_pdf(a, sections: list[SectionInput]) -> bytes:
    body, qstyle, h, small = _styles()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=a.title,
    )
    flow = [Paragraph(escape(a.title), h)]
    if a.subject:
        flow.append(Paragraph(escape(a.subject), small))
    flow.append(Spacer(1, 8))
    for sec in sections:
        marks = f" [{sec.marks:g}]" if sec.marks is not None else ""
        flow.append(
            Paragraph(
                f"<b>{escape(sec.number or '')}</b> {_inline_xml(parse_inlines(' '.join(sec.question_text.split())))}{marks}",
                qstyle,
            )
        )
        flow += _answer_flowables(sec.answer or "(no answer)", body)
    doc.build(flow)
    return buf.getvalue()


def review_pdf(a, sections: list[SectionInput], page_pngs: list[bytes]) -> bytes:
    """Landscape pages: handwritten page on the left, the typed text for comparison on the right."""
    body, qstyle, _h, _small = _styles()
    W, H = landscape(A4)
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(W, H))
    c.setTitle(f"{a.title} (review copy)")
    font = fallback_font()
    typed = []
    for sec in sections:
        typed.append(
            Paragraph(f"<b>{escape(sec.number or '')}</b> {escape(' '.join(sec.question_text.split()))}", qstyle)
        )
        typed += _answer_flowables(sec.answer or "(no answer)", body)
    from reportlab.platypus import Frame

    remaining = typed
    for i, png in enumerate(page_pngs or [b""]):
        if png:
            img = ImageReader(io.BytesIO(png))
            iw, ih = img.getSize()
            box_w, box_h = W / 2 - 20 * mm, H - 24 * mm
            sc = min(box_w / iw, box_h / ih)
            c.drawImage(img, 12 * mm, (H - ih * sc) / 2, iw * sc, ih * sc)
        c.setFont(font, 8)
        c.drawString(12 * mm, 6 * mm, f"Review copy - page {i + 1}")
        fr = Frame(W / 2 + 4 * mm, 12 * mm, W / 2 - 16 * mm, H - 24 * mm, showBoundary=0)
        remaining = _fill(fr, remaining, c)
        c.showPage()
    while remaining:
        fr = Frame(16 * mm, 12 * mm, W - 32 * mm, H - 24 * mm)
        remaining = _fill(fr, remaining, c)
        c.showPage()
    c.save()
    return buf.getvalue()


def _fill(frame, flowables: list, c) -> list:
    """Add flowables to ``frame`` until one does not fit; return the rest. A flowable too large for
    an empty frame is dropped so the loop always terminates."""
    rest = list(flowables)
    placed_any = False
    while rest:
        if frame.add(rest[0], c):
            rest.pop(0)
            placed_any = True
        elif not placed_any:
            rest.pop(0)
        else:
            break
    return rest
