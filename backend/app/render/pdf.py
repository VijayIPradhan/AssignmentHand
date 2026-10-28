"""Draw a laid-out document to PDF. Glyphs are vector outlines defined once per variant as form
XObjects and placed with a transform, so output stays small and sharp at any zoom."""

import io
import math

import pypdfium2 as pdfium
from reportlab.lib.colors import HexColor
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import FILL_EVEN_ODD, Canvas

from app.handwriting.glyphs import Glyph, outlines
from app.render.engine import PT_PER_MM, FallbackItem, GlyphItem, ImageItem, LineItem, TofuItem
from app.render.fonts import fallback_font
from app.render.layout import LayoutResult
from app.render.settings import RenderSettings


def _paper(c: Canvas, s: RenderSettings, w_mm: float, h_mm: float) -> None:
    c.setFillColor(HexColor(s.paper_color))
    c.rect(0, 0, w_mm * PT_PER_MM, h_mm * PT_PER_MM, stroke=0, fill=1)
    m = s.margins
    paper = s.extra_page_paper if s.paper == "source" else s.paper
    if paper == "ruled":
        c.setStrokeColor(HexColor(s.rule_color))
        c.setLineWidth(0.45)
        y = m.top
        while y <= h_mm - m.bottom + 0.01:
            Y = (h_mm - y) * PT_PER_MM
            c.line(0, Y, w_mm * PT_PER_MM, Y)
            y += s.line_spacing_mm
    elif paper == "grid":
        c.setStrokeColor(HexColor(s.rule_color))
        c.setLineWidth(0.3)
        step = 5.0
        x = 0.0
        while x <= w_mm:
            c.line(x * PT_PER_MM, 0, x * PT_PER_MM, h_mm * PT_PER_MM)
            x += step
        y = 0.0
        while y <= h_mm:
            c.line(0, y * PT_PER_MM, w_mm * PT_PER_MM, y * PT_PER_MM)
            y += step
    if s.margin_line and paper != "blank":
        c.setStrokeColor(HexColor("#e2a3a3"))
        c.setLineWidth(0.6)
        X = (m.left - 3.0) * PT_PER_MM
        c.line(X, 0, X, h_mm * PT_PER_MM)


def _form_name(g: Glyph, weight: float) -> str:
    return f"G{ord(g.char):X}_{g.id.replace('-', '_')}_{round(weight * 100)}"


def _ensure_form(c: Canvas, defined: set[str], g: Glyph, weight: float) -> str:
    name = _form_name(g, weight)
    if name in defined:
        return name
    c.beginForm(name, lowerx=-20, lowery=-20, upperx=40, uppery=40)
    polys = outlines(g, weight)
    if polys:
        p = c.beginPath()
        for poly in polys:
            p.moveTo(*poly[0])
            for pt in poly[1:]:
                p.lineTo(*pt)
            p.close()
        c.drawPath(p, stroke=0, fill=1, fillMode=FILL_EVEN_ODD)
    c.endForm()
    defined.add(name)
    return name


def _transform(c: Canvas, x_mm: float, y_mm: float, h_mm: float, k: float, rot_deg: float, shear: float) -> None:
    r = math.radians(rot_deg)
    cs, sn = math.cos(r), math.sin(r)
    c.transform(
        k * cs, k * sn, k * (cs * shear - sn), k * (sn * shear + cs), x_mm * PT_PER_MM, (h_mm - y_mm) * PT_PER_MM
    )


def render_pdf(
    result: LayoutResult, s: RenderSettings, title: str = "Assignment", weight: float | None = None
) -> bytes:
    weight = s.stroke_weight if weight is None else weight
    buf = io.BytesIO()
    c = Canvas(
        buf, pagesize=(result.width_mm * PT_PER_MM, result.height_mm * PT_PER_MM), pageCompression=1, invariant=1
    )
    c.setTitle(title)
    c.setCreator("AssignmentHand")
    c.setAuthor("")
    c.setSubject("")
    ink = HexColor(s.ink_color)
    font = fallback_font()
    defined: set[str] = set()
    for page in result.pages:
        W, H = page.width, page.height
        c.setPageSize((W * PT_PER_MM, H * PT_PER_MM))
        if page.source_page is None:
            _paper(c, s, W, H)  # source pages keep their own background (composited later)
        c.setFillColor(ink)
        c.setStrokeColor(ink)
        for it in page.items:
            if isinstance(it, GlyphItem):
                name = _ensure_form(c, defined, it.glyph, round(weight * it.weight, 2))
                c.saveState()
                if it.color:
                    c.setFillColor(HexColor(it.color))
                _transform(c, it.x, it.y, H, it.scale * PT_PER_MM, it.rot, it.shear)
                c.doForm(name)
                c.restoreState()
            elif isinstance(it, LineItem):
                c.saveState()
                c.setLineWidth(max(0.2, it.width * PT_PER_MM))
                c.setLineCap(1)
                c.setLineJoin(1)
                p = c.beginPath()
                p.moveTo(it.pts[0][0] * PT_PER_MM, (H - it.pts[0][1]) * PT_PER_MM)
                for x, y in it.pts[1:]:
                    p.lineTo(x * PT_PER_MM, (H - y) * PT_PER_MM)
                c.drawPath(p, stroke=1, fill=0)
                c.restoreState()
            elif isinstance(it, FallbackItem):
                c.saveState()
                _transform(c, it.x, it.y, H, 1.0, it.rot, it.shear)
                c.setFont(it.font or font, it.size_mm * PT_PER_MM)
                c.drawString(0, 0, it.ch)
                c.restoreState()
            elif isinstance(it, TofuItem):
                c.saveState()
                c.setLineWidth(0.5)
                c.rect(
                    it.x * PT_PER_MM,
                    (H - it.y - it.h) * PT_PER_MM,
                    it.w * PT_PER_MM,
                    it.h * PT_PER_MM,
                    stroke=1,
                    fill=0,
                )
                c.restoreState()
            elif isinstance(it, ImageItem):
                c.drawImage(
                    ImageReader(io.BytesIO(it.data)),
                    it.x * PT_PER_MM,
                    (H - it.y - it.h) * PT_PER_MM,
                    it.w * PT_PER_MM,
                    it.h * PT_PER_MM,
                    preserveAspectRatio=True,
                    mask="auto",
                )
        c.showPage()
    c.save()
    return buf.getvalue()


def rasterize(pdf_bytes: bytes, dpi: float = 110, max_pages: int | None = None) -> list[bytes]:
    pdf = pdfium.PdfDocument(pdf_bytes)
    out = []
    try:
        n = len(pdf) if max_pages is None else min(len(pdf), max_pages)
        for i in range(n):
            pil = pdf[i].render(scale=dpi / 72).to_pil().convert("RGB")
            b = io.BytesIO()
            pil.save(b, format="PNG", optimize=True)
            out.append(b.getvalue())
    finally:
        pdf.close()
    return out


def compose_with_source(hand_pdf: bytes, source_pdf: bytes, result: LayoutResult) -> bytes:
    """Overlay the handwriting layer onto the original assignment pages (vector on vector)."""
    from pypdf import PdfReader, PdfWriter

    hand = PdfReader(io.BytesIO(hand_pdf))
    src = PdfReader(io.BytesIO(source_pdf))
    out = PdfWriter()
    for i, page in enumerate(result.pages):
        layer = hand.pages[i]
        if page.source_page is None:
            out.add_page(layer)
            continue
        out.add_page(src.pages[page.source_page])
        base = out.pages[-1]
        llx, lly = float(base.mediabox.left), float(base.mediabox.bottom)
        if llx or lly:
            from pypdf import Transformation

            base.merge_transformed_page(layer, Transformation().translate(llx, lly))
        else:
            base.merge_page(layer)
    out.add_metadata({"/Title": "Assignment", "/Creator": "AssignmentHand"})
    buf = io.BytesIO()
    out.write(buf)
    return buf.getvalue()
