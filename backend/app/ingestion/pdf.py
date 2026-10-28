"""PDF validation and page-level extraction (text layer, tables, figures, OCR for scans).

Uploaded PDFs are untrusted: we only read them with pdfium/pdfminer (no JavaScript, no external
resources), enforce page and size limits, and never execute embedded content.
"""

import io
import logging
import re
from dataclasses import dataclass, field

import pdfplumber
import pypdfium2 as pdfium

from app.ingestion.ocr import OcrError, OcrProvider

log = logging.getLogger(__name__)

MATH_FONT_HINTS = ("cmmi", "cmsy", "cmex", "msbm", "symbol", "math", "stix", "mtextra", "euclid")
PUA = re.compile(r"[\ue000-\uf8ff]")  # private-use code points: glyphs with no Unicode mapping
CID = re.compile(r"\(cid:\d+\)")
PAGE_IMAGE_DPI = 110
OCR_DPI = 300
FIGURE_DPI = 150


class PdfValidationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass
class LineInfo:
    text: str
    page: int
    y0: float  # points from page top
    y1: float
    x0: float
    size: float = 0.0
    bold: bool = False
    confidence: float = 1.0
    kind: str = "text"  # text | table
    math: bool = False
    unmapped: bool = False


@dataclass
class FigureInfo:
    page: int
    bbox: tuple[float, float, float, float]  # x0, top, x1, bottom in points
    png: bytes


@dataclass
class PageInfo:
    index: int
    width: float
    height: float
    has_text: bool
    ocr: str | None = None
    confidence: float = 1.0
    vector_graphics: int = 0
    issues: list[str] = field(default_factory=list)
    image_png: bytes = b""


@dataclass
class DocExtraction:
    pages: list[PageInfo]
    lines: list[LineInfo]
    figures: list[FigureInfo]


def validate_pdf(data: bytes, max_pages: int) -> int:
    if not data.startswith(b"%PDF-"):
        raise PdfValidationError("not_pdf", "The file is not a PDF")
    try:
        doc = pdfium.PdfDocument(data)
    except pdfium.PdfiumError as exc:
        msg = str(exc).lower()
        if "password" in msg:
            raise PdfValidationError(
                "encrypted", "The PDF is password-protected; remove the password and re-upload"
            ) from exc
        raise PdfValidationError("malformed", "The PDF is damaged or could not be read") from exc
    try:
        n = len(doc)
    finally:
        doc.close()
    if n == 0:
        raise PdfValidationError("empty", "The PDF has no pages")
    if n > max_pages:
        raise PdfValidationError("too_many_pages", f"The PDF has {n} pages; the limit is {max_pages}")
    return n


def _png(pil) -> bytes:
    b = io.BytesIO()
    pil.save(b, format="PNG", optimize=True)
    return b.getvalue()


def _table_markdown(rows: list[list[str | None]]) -> str:
    clean = [[(c or "").replace("\n", " ").replace("|", "/").strip() for c in r] for r in rows]
    ncol = max(len(r) for r in clean)
    clean = [r + [""] * (ncol - len(r)) for r in clean]
    out = ["| " + " | ".join(clean[0]) + " |", "|" + "---|" * ncol]
    out += ["| " + " | ".join(r) + " |" for r in clean[1:]]
    return "\n".join(out)


def _is_data_table(rows: list[list[str | None]]) -> bool:
    """A real table has rows with several filled cells. Word processors also draw ruled or shaded boxes
    around a question and its blank answer area, which table detection reports as a grid of mostly
    empty cells with at most one filled cell per row: that is layout, and its text is read as lines."""
    return sum(1 for r in rows if sum(1 for c in r if (c or "").strip()) >= 2) >= 2


def _line_flags(chars: list[dict]) -> tuple[float, bool, bool]:
    sizes = [c.get("size", 0) for c in chars if c.get("text", "").strip()]
    fonts = [str(c.get("fontname", "")).lower() for c in chars]
    size = sorted(sizes)[len(sizes) // 2] if sizes else 0.0
    bold = bool(fonts) and sum("bold" in f or "black" in f for f in fonts) > len(fonts) / 2
    math = any(any(h in f for h in MATH_FONT_HINTS) for f in fonts)
    return size, bold, math


def extract_pdf(data: bytes, ocr: OcrProvider | None) -> DocExtraction:
    pages: list[PageInfo] = []
    lines: list[LineInfo] = []
    figures: list[FigureInfo] = []
    rdoc = pdfium.PdfDocument(data)
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for pi, page in enumerate(pdf.pages):
                pw, ph = float(page.width), float(page.height)
                rpage = rdoc[pi]
                info = PageInfo(pi, pw, ph, has_text=False)
                info.image_png = _png(rpage.render(scale=PAGE_IMAGE_DPI / 72).to_pil().convert("RGB"))
                page_lines: list[LineInfo] = []

                tables = []
                try:
                    for t in page.find_tables():
                        rows = t.extract()
                        if len(rows) >= 2 and max(len(r) for r in rows) >= 2 and _is_data_table(rows):
                            tables.append((t.bbox, rows))
                except Exception:  # pdfplumber table detection can fail on odd vector content
                    log.info("table detection failed", extra={"page": pi})

                def in_table(x0, top, x1, bottom, _tables=tables):
                    cy = (top + bottom) / 2
                    return any(b[1] - 1 <= cy <= b[3] + 1 and x0 < b[2] and x1 > b[0] for b, _ in _tables)

                text_chars = [c for c in page.chars if c.get("text", "").strip()]
                if len(text_chars) >= 15:
                    info.has_text = True
                    for tl in page.extract_text_lines(layout=False, strip=True, return_chars=True):
                        if in_table(tl["x0"], tl["top"], tl["x1"], tl["bottom"]):
                            continue
                        text = tl["text"]
                        size, bold, math = _line_flags(tl.get("chars", []))
                        unmapped = bool(PUA.search(text) or CID.search(text))
                        text = CID.sub("□", text)
                        page_lines.append(
                            LineInfo(
                                text,
                                pi,
                                tl["top"],
                                tl["bottom"],
                                tl["x0"],
                                size,
                                bold,
                                0.6 if unmapped else 1.0,
                                math=math,
                                unmapped=unmapped,
                            )
                        )
                    if any(ln.unmapped for ln in page_lines):
                        info.issues.append("some symbols could not be decoded from the PDF (shown as □)")
                    for bbox, rows in tables:
                        page_lines.append(LineInfo(_table_markdown(rows), pi, bbox[1], bbox[3], bbox[0], kind="table"))
                    if tables:
                        info.issues.append(f"{len(tables)} table(s) detected; check the converted table text")
                else:
                    if ocr is None:
                        info.ocr = "unavailable"
                        info.confidence = 0.0
                        info.issues.append("scanned page: no OCR engine is configured, please type the questions")
                    else:
                        png = _png(rpage.render(scale=OCR_DPI / 72).to_pil().convert("L"))
                        try:
                            res = ocr.ocr(png)
                        except OcrError as exc:
                            info.ocr = "failed"
                            info.confidence = 0.0
                            info.issues.append(f"OCR failed on this page ({exc}); please type the questions")
                            res = None
                        if res is not None:
                            info.ocr = res.engine
                            info.confidence = res.confidence
                            for ol in res.lines:
                                page_lines.append(
                                    LineInfo(ol.text, pi, ol.y0 * ph, ol.y1 * ph, ol.x0 * pw, confidence=ol.confidence)
                                )
                            if not res.lines:
                                info.issues.append("OCR found no text on this scanned page")
                            elif res.confidence < 0.75:
                                info.issues.append(f"low OCR confidence ({res.confidence:.0%}); please check this page")
                            if res.engine == "llm":
                                info.issues.append("transcribed by an AI model; verify wording and symbols")

                # Figures: embedded images that are not the whole-page scan
                if info.has_text:
                    for im in page.images:
                        x0, top, x1, bottom = (float(im[k]) for k in ("x0", "top", "x1", "bottom"))
                        area = (x1 - x0) * (bottom - top)
                        if area < 0.02 * pw * ph or area > 0.85 * pw * ph:
                            continue
                        crop = (max(0, x0), max(0, top), min(pw, x1), min(ph, bottom))
                        figures.append(FigureInfo(pi, crop, _render_crop(rpage, crop)))
                curves = len(page.curves) + len(page.lines)
                info.vector_graphics = curves
                if curves > 25 and not tables:
                    info.issues.append("page contains drawn diagrams; questions referring to them are marked")
                page_lines.sort(key=lambda ln: (round(ln.y0, 1), ln.x0))
                lines += page_lines
                pages.append(info)
    finally:
        rdoc.close()
    return DocExtraction(pages, lines, figures)


def _render_crop(rpage, crop: tuple[float, float, float, float]) -> bytes:
    scale = FIGURE_DPI / 72
    pil = rpage.render(scale=scale).to_pil().convert("RGB")
    x0, top, x1, bottom = crop
    box = (int(x0 * scale), int(top * scale), int(x1 * scale), int(bottom * scale))
    return _png(pil.crop(box))


def render_page_png(data: bytes, index: int, dpi: int = PAGE_IMAGE_DPI) -> bytes:
    doc = pdfium.PdfDocument(data)
    try:
        return _png(doc[index].render(scale=dpi / 72).to_pil().convert("RGB"))
    finally:
        doc.close()
