"""Printable handwriting collection sheet and its geometry.

The sheet has four solid registration squares in the corners and a small binary page code at the top
centre. Detection (see ``segment.py``) uses the squares to undo rotation and perspective, then reads
the code to identify the page and its orientation. Each cell holds one character; a baseline is
marked by ticks outside the cell so that ink can be measured relative to it.
"""

import io
import math
from dataclasses import dataclass

from reportlab.lib.colors import Color
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from app.handwriting.charset import template_cells
from app.render.fonts import fallback_font

PAGE_W_MM = 210.0
PAGE_H_MM = 297.0

MARKER_SIZE_MM = 8.0
MARKER_CENTER_INSET_MM = 12.0

CODE_BITS = 4
CODE_BOX_MM = 4.0
CODE_PITCH_MM = 6.0
CODE_Y_MM = 12.0  # centre of code boxes, from top

GRID_X0_MM = 20.0
GRID_Y0_MM = 32.0
COLS = 10
ROWS = 12
COL_PITCH_MM = 17.0
ROW_PITCH_MM = 20.0
CELL_W_MM = 15.5
CELL_H_MM = 16.0
BASELINE_FROM_TOP_MM = 11.0
CELLS_PER_PAGE = COLS * ROWS


@dataclass(frozen=True)
class Cell:
    page: int
    index: int
    char: str
    x_mm: float  # top-left, from page left
    y_mm: float  # top-left, from page top

    @property
    def baseline_y_mm(self) -> float:
        return self.y_mm + BASELINE_FROM_TOP_MM


def page_count() -> int:
    return math.ceil(len(template_cells()) / CELLS_PER_PAGE)


def cells_for_page(page: int) -> list[Cell]:
    chars = template_cells()[page * CELLS_PER_PAGE : (page + 1) * CELLS_PER_PAGE]
    out = []
    for i, ch in enumerate(chars):
        r, c = divmod(i, COLS)
        out.append(Cell(page, i, ch, GRID_X0_MM + c * COL_PITCH_MM, GRID_Y0_MM + r * ROW_PITCH_MM))
    return out


def marker_centers_mm() -> list[tuple[float, float]]:
    """TL, TR, BR, BL marker centres in page mm (y from top)."""
    i = MARKER_CENTER_INSET_MM
    return [(i, i), (PAGE_W_MM - i, i), (PAGE_W_MM - i, PAGE_H_MM - i), (i, PAGE_H_MM - i)]


def code_box_centers_mm() -> list[tuple[float, float]]:
    """Start bit followed by CODE_BITS page bits, centred horizontally."""
    n = CODE_BITS + 1
    x0 = PAGE_W_MM / 2 - (n - 1) * CODE_PITCH_MM / 2
    return [(x0 + k * CODE_PITCH_MM, CODE_Y_MM) for k in range(n)]


def page_code_bits(page: int) -> list[bool]:
    return [True] + [bool((page >> b) & 1) for b in range(CODE_BITS)]


def build_template_pdf() -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(PAGE_W_MM * mm, PAGE_H_MM * mm))
    c.setTitle("Handwriting sample sheet")
    font = fallback_font()
    total = page_count()
    grey = Color(0.62, 0.62, 0.62)
    label = Color(0.45, 0.45, 0.45)

    def Y(y_mm: float) -> float:  # top-based mm -> PDF points
        return (PAGE_H_MM - y_mm) * mm

    for p in range(total):
        c.setFillColorRGB(0, 0, 0)
        for cx, cy in marker_centers_mm():
            s = MARKER_SIZE_MM
            c.rect((cx - s / 2) * mm, Y(cy + s / 2), s * mm, s * mm, stroke=0, fill=1)
        for (cx, cy), bit in zip(code_box_centers_mm(), page_code_bits(p), strict=True):
            s = CODE_BOX_MM
            c.setLineWidth(0.6)
            c.rect((cx - s / 2) * mm, Y(cy + s / 2), s * mm, s * mm, stroke=1, fill=1 if bit else 0)

        c.setFillColor(label)
        c.setFont(font, 7.5)
        c.drawString(24 * mm, Y(22), f"Handwriting sample sheet - page {p + 1} of {total}")
        c.drawRightString(186 * mm, Y(22), "One character per box, resting on the baseline ticks. Use a dark pen.")
        c.drawCentredString(PAGE_W_MM / 2 * mm, Y(287), "Keep all four corner squares visible when scanning.")

        for cell in cells_for_page(p):
            c.setStrokeColor(grey)
            c.setLineWidth(0.35)
            c.rect(cell.x_mm * mm, Y(cell.y_mm + CELL_H_MM), CELL_W_MM * mm, CELL_H_MM * mm, stroke=1, fill=0)
            # baseline ticks outside the box
            by = Y(cell.baseline_y_mm)
            c.setLineWidth(0.6)
            c.line((cell.x_mm - 0.9) * mm, by, cell.x_mm * mm, by)
            c.line((cell.x_mm + CELL_W_MM) * mm, by, (cell.x_mm + CELL_W_MM + 0.9) * mm, by)
            c.setFillColor(label)
            c.setFont(font, 7)
            text = cell.char if cell.char != " " else "space"
            c.drawCentredString((cell.x_mm + CELL_W_MM / 2) * mm, Y(cell.y_mm + CELL_H_MM + 2.8), text)
        c.showPage()
    c.save()
    return buf.getvalue()
