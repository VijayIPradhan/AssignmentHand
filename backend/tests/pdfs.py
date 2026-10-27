"""Generated assignment PDFs used as test fixtures."""

import io

import numpy as np
from PIL import Image, ImageDraw
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from app.render.fonts import fallback_font


def _figure_png() -> bytes:
    im = Image.new("RGB", (480, 300), "white")
    d = ImageDraw.Draw(im)
    d.rectangle([20, 20, 460, 280], outline="black", width=3)
    d.line([40, 260, 440, 60], fill="black", width=4)
    d.ellipse([200, 120, 280, 200], outline="black", width=3)
    b = io.BytesIO()
    im.save(b, format="PNG")
    return b.getvalue()


def assignment_pdf(*, gap: bool = True, with_figure: bool = True) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    W, H = A4
    font = fallback_font()

    def header(page: int):
        c.setFont(font, 9)
        c.drawString(50, H - 30, "PHYS 101 - Assignment 3")
        c.drawRightString(W - 50, 25, f"Page {page} of 2")

    header(1)
    y = H - 80
    c.setFont(font, 14)
    c.drawString(50, y, "Physics Assignment 3: Motion and Energy")
    y -= 24
    c.setFont(font, 10.5)
    for t in ["Instructions: Answer all questions. Show your working.", "Section A"]:
        c.drawString(50, y, t)
        y -= 18
    rows = [
        "Q1. Define kinetic energy and state its SI unit. (3 marks)",
        "Q2. A ball of mass 2 kg is dropped from a height of 10 m.",
        "(a) Calculate its potential energy at the top. [2]",
        "(b) Find its speed just before it hits the ground, using v = √(2gh). (4 marks)",
        "Q3. The table below shows distance travelled over time.",
    ]
    for t in rows:
        c.drawString(50, y, t)
        y -= 18
    # table
    data = [["Time (s)", "Distance (m)"], ["0", "0"], ["1", "4.9"], ["2", "19.6"]]
    x0, cw, rh = 60, 120, 18
    top = y
    for r, row in enumerate(data):
        for k, cell in enumerate(row):
            c.rect(x0 + k * cw, top - (r + 1) * rh, cw, rh)
            c.drawString(x0 + k * cw + 6, top - (r + 1) * rh + 5, cell)
    y = top - len(data) * rh - 20
    c.drawString(50, y, "Use the table to estimate the acceleration. (5 marks)")
    y -= 30
    if with_figure:
        c.drawString(
            50,
            y,
            "Q4. Describe the motion shown in the diagram below. (4 marks)"
            if gap is False
            else "Q5. Describe the motion shown in the diagram below. (4 marks)",
        )
        c.drawImage(ImageReader(io.BytesIO(_figure_png())), 80, y - 200, 300, 180)
    c.showPage()
    header(2)
    c.setFont(font, 10.5)
    y = H - 80
    for t in [
        "Section B",
        "Q6. Explain why the total mechanical energy is conserved when air resistance is",
        "negligible, and discuss what happens when it is not. (8 marks)",
    ]:
        c.drawString(50, y, t)
        y -= 18
    c.showPage()
    c.save()
    return buf.getvalue()


def scanned_pdf(text_lines: list[str]) -> bytes:
    """A PDF whose page is only an image (no text layer)."""
    im = Image.new("L", (1240, 1754), 255)
    d = ImageDraw.Draw(im)
    for i, t in enumerate(text_lines):
        d.text((100, 150 + i * 60), t, fill=0)
    arr = np.asarray(im)
    pil = Image.fromarray(arr)
    b = io.BytesIO()
    pil.save(b, format="PDF", resolution=150)
    return b.getvalue()


def encrypted_pdf() -> bytes:
    from reportlab.lib import pdfencrypt

    buf = io.BytesIO()
    enc = pdfencrypt.StandardEncryption("secret", ownerPassword="owner", canPrint=0)
    c = canvas.Canvas(buf, encrypt=enc)
    c.drawString(100, 700, "Q1. Hidden question")
    c.showPage()
    c.save()
    return buf.getvalue()


def plain_paragraphs_pdf() -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setFont(fallback_font(), 11)
    y = 780
    for para in [
        ["Reading response"],
        ["Explain the main argument of the article in your own words and", "evaluate its strengths."],
        ["The article was published in 2019 in a peer reviewed journal and", "is about urban planning."],
        ["What evidence does the author use to support the conclusion?"],
    ]:
        for t in para:
            c.drawString(50, y, t)
            y -= 15
        y -= 25
    c.showPage()
    c.save()
    return buf.getvalue()
