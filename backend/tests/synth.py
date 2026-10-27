"""Synthetic handwriting fixtures. A font stands in for a person's writing so tests never need real
(private) samples. Images are degraded with rotation, perspective, shadows and noise."""

import io

import cv2
import numpy as np
import pypdfium2 as pdfium
from PIL import Image, ImageDraw, ImageFont

from app.handwriting import template as T
from app.render.fonts import _CANDIDATES

DPI = 200
PPM = DPI / 25.4


def font_path() -> str:
    from pathlib import Path

    for c in _CANDIDATES:
        if Path(c).is_file():
            return c
    raise RuntimeError("no TTF font available for synthetic fixtures")


def blank_template_page(page: int, dpi: int = DPI) -> np.ndarray:
    pdf = pdfium.PdfDocument(T.build_template_pdf())
    pil = pdf[page].render(scale=dpi / 72).to_pil().convert("RGB")
    pdf.close()
    return np.asarray(pil).copy()


def filled_template(page: int = 0, *, x_height_mm: float = 3.4, skip: str = "", dpi: int = DPI) -> np.ndarray:
    """Render the template page and 'write' each cell's character with a font, baseline-aligned."""
    rgb = blank_template_page(page, dpi)
    pil = Image.fromarray(rgb)
    draw = ImageDraw.Draw(pil)
    ppm = dpi / 25.4
    font = ImageFont.truetype(font_path(), int(x_height_mm * ppm / 0.52))
    rng = np.random.default_rng(page + 7)
    for cell in T.cells_for_page(page):
        if cell.char in skip:
            continue
        x = (cell.x_mm + 3.5 + rng.uniform(-0.6, 0.6)) * ppm
        y = cell.baseline_y_mm * ppm
        draw.text((x, y), cell.char, font=font, fill=(25, 30, 90), anchor="ls")
    return cv2.cvtColor(np.asarray(pil), cv2.COLOR_RGB2BGR)


def degrade(
    img: np.ndarray,
    *,
    rotate: float = 0.0,
    perspective: float = 0.0,
    shadow: bool = False,
    noise: float = 0.0,
    seed: int = 0,
) -> np.ndarray:
    rng = np.random.default_rng(seed)
    h, w = img.shape[:2]
    out = img.copy()
    if perspective:
        d = perspective * min(h, w)
        src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
        dst = src + np.float32(rng.uniform(-d, d, (4, 2)))
        pad = int(2 * d)
        out = cv2.copyMakeBorder(out, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=(235, 235, 235))
        M = cv2.getPerspectiveTransform(src + pad, dst + pad)
        out = cv2.warpPerspective(out, M, (w + 2 * pad, h + 2 * pad), borderValue=(235, 235, 235))
    if rotate:
        from app.handwriting.preprocess import rotate_bound

        out = rotate_bound(out, rotate)
        out[(out == 255).all(axis=2)] = 235
    if shadow:
        _hh, ww = out.shape[:2]
        grad = np.linspace(0.55, 1.0, ww, dtype=np.float32)[None, :, None]
        out = (out.astype(np.float32) * grad).clip(0, 255).astype(np.uint8)
    if noise:
        out = (out.astype(np.float32) + rng.normal(0, noise, out.shape)).clip(0, 255).astype(np.uint8)
    return out


def freeform_sample(text: str, *, x_height_mm: float = 3.2, dpi: int = DPI, slant: float = 0.0) -> np.ndarray:
    ppm = dpi / 25.4
    size = int(x_height_mm * ppm / 0.52)
    font = ImageFont.truetype(font_path(), size)
    lines = text.splitlines()
    W = int(max(font.getlength(ln) for ln in lines) + 4 * size)
    H = int((len(lines) + 1) * size * 1.9)
    pil = Image.new("RGB", (max(W, 900), max(H, 700)), (250, 250, 245))
    d = ImageDraw.Draw(pil)
    for i, ln in enumerate(lines):
        d.text((2 * size, (i + 1) * size * 1.9), ln, font=font, fill=(20, 20, 20), anchor="ls")
    img = cv2.cvtColor(np.asarray(pil), cv2.COLOR_RGB2BGR)
    if slant:
        h, w = img.shape[:2]
        k = np.tan(np.radians(slant))
        M = np.float32([[1, -k, k * h], [0, 1, 0]])
        img = cv2.warpAffine(img, M, (int(w + k * h), h), borderValue=(250, 250, 245))
    return img


def to_png(img: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", img)
    assert ok
    return buf.tobytes()


def to_jpeg(img: np.ndarray, quality: int = 85) -> bytes:
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    assert ok
    return buf.tobytes()


def images_to_pdf(images: list[np.ndarray]) -> bytes:
    pils = [Image.fromarray(cv2.cvtColor(i, cv2.COLOR_BGR2RGB)) for i in images]
    buf = io.BytesIO()
    pils[0].save(buf, format="PDF", save_all=True, append_images=pils[1:], resolution=DPI)
    return buf.getvalue()
