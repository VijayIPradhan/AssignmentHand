"""The user's own signature: uploaded once, cleaned to an ink mask, placed only where they choose.

Stored per user (``users/<uid>/signature/``): the original upload and a cropped ink mask. The mask is
traced into vector outlines like handwriting glyphs, so it prints sharply in any ink colour.
"""

import hashlib
import json

import cv2
import numpy as np

from app.handwriting.glyphs import Glyph, crop_to_ink
from app.handwriting.preprocess import (
    SampleImageError,
    apply_corrections,
    binarize,
    load_image,
    normalize_illumination,
    remove_specks,
    to_gray,
)
from app.storage import get_storage

MAX_HEIGHT_PX = 360


def _prefix(user_id: str) -> str:
    return f"users/{user_id}/signature"


def process_signature(data: bytes, mime: str) -> np.ndarray:
    """Return a tightly cropped ink mask (uint8, ink=255) from a photo or scan of a signature."""
    img = load_image(data, mime)
    mask = binarize(normalize_illumination(to_gray(img)), hysteresis=0.35)
    mask = remove_specks(mask, min_area=max(20, mask.size // 40_000))
    # Keep the main strokes: drop components far smaller than the largest (dust, paper texture).
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    if n <= 1:
        raise SampleImageError("No signature ink was found in the image")
    biggest = int(stats[1:, cv2.CC_STAT_AREA].max())
    keep = np.zeros(n, dtype=bool)
    keep[1:] = stats[1:, cv2.CC_STAT_AREA] >= max(12, biggest * 0.004)
    mask = (keep[labels] * 255).astype(np.uint8)
    cropped = crop_to_ink(mask, pad=4)
    if cropped is None:
        raise SampleImageError("No signature ink was found in the image")
    bmp = cropped[0]
    if bmp.shape[0] > MAX_HEIGHT_PX:
        f = MAX_HEIGHT_PX / bmp.shape[0]
        bmp = cv2.resize(bmp, (max(1, int(bmp.shape[1] * f)), MAX_HEIGHT_PX), interpolation=cv2.INTER_AREA)
        bmp = ((bmp > 127) * 255).astype(np.uint8)
    if bmp.shape[1] < 40 or bmp.shape[0] < 15:
        raise SampleImageError("The signature is too small in this image; photograph it closer")
    return bmp


def save_signature(user_id: str, data: bytes, mime: str, ext: str, corrections: dict | None = None) -> dict:
    if corrections:
        img = apply_corrections(load_image(data, mime), corrections)
        _ok, buf = cv2.imencode(".png", img)
        data, mime = buf.tobytes(), "image/png"
    bmp = process_signature(data, mime)
    st = get_storage()
    p = _prefix(user_id)
    st.delete_prefix(p)
    st.put(f"{p}/original.{ext}", data)
    _ok, png = cv2.imencode(".png", bmp)
    st.put(f"{p}/mask.png", png.tobytes())
    meta = {
        "width": int(bmp.shape[1]),
        "height": int(bmp.shape[0]),
        "version": hashlib.sha1(png.tobytes()).hexdigest()[:12],
    }
    st.put(f"{p}/meta.json", json.dumps(meta).encode())
    return meta


def signature_meta(user_id: str) -> dict | None:
    st = get_storage()
    key = f"{_prefix(user_id)}/meta.json"
    return json.loads(st.get(key)) if st.exists(key) else None


def delete_signature(user_id: str) -> None:
    get_storage().delete_prefix(_prefix(user_id))


def signature_glyph(user_id: str) -> Glyph | None:
    st = get_storage()
    key = f"{_prefix(user_id)}/mask.png"
    if not st.exists(key):
        return None
    bmp = cv2.imdecode(np.frombuffer(st.get(key), np.uint8), cv2.IMREAD_GRAYSCALE)
    meta = signature_meta(user_id) or {}
    # px_per_mm is set by the renderer (scale); baseline at 78% of the height so loops of g/y/p can
    # dip below the line as in a real signature.
    return Glyph("✍", bmp, 1.0, bmp.shape[0] * 0.78, "signature", "", f"sig-{meta.get('version', 'x')}")


def preview_png(user_id: str, color: str = "#1f3a93") -> bytes | None:
    st = get_storage()
    key = f"{_prefix(user_id)}/mask.png"
    if not st.exists(key):
        return None
    bmp = cv2.imdecode(np.frombuffer(st.get(key), np.uint8), cv2.IMREAD_GRAYSCALE)
    c = color.lstrip("#")
    rgb = (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16))
    out = np.full((*bmp.shape, 4), 0, np.uint8)
    out[..., 0], out[..., 1], out[..., 2] = rgb[2], rgb[1], rgb[0]
    out[..., 3] = bmp
    _ok, png = cv2.imencode(".png", out)
    return png.tobytes()
