"""Optional AI check of automatically cut letters.

Free-form (especially joined) handwriting is cut into letters heuristically, and some cuts are
wrong: half of an "m" labelled "n", two letters labelled as one. A vision-capable model is shown
contact sheets of the candidates for each character and asked which ones really show that
character. The user's samples are sent only to build their own profile.
"""

import logging

import cv2
import numpy as np

from app.handwriting.glyphs import Glyph

log = logging.getLogger(__name__)

CELL = 72
PER_ROW = 16
ROWS_PER_SHEET = 14

VERIFY_PROMPT = (
    "The attached sheet shows small images cut automatically out of a person's handwriting. Each row starts "
    "with a boxed label giving the character those images are supposed to show; each image has a number under "
    "it. For every row, list the numbers of images that clearly show exactly that one character, correctly cut "
    "out: not a piece of a character, not two characters, not a different character, and not a smudge. Capital "
    "and small letters are different characters. Be strict: when unsure, leave the number out. Return every row "
    "label, with an empty list if no image qualifies."
)

VERIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "rows": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"label": {"type": "string"}, "keep": {"type": "array", "items": {"type": "integer"}}},
                "required": ["label", "keep"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["rows"],
    "additionalProperties": False,
}


def _cell(g: Glyph, x_height_px: float) -> np.ndarray:
    """Draw a glyph at a common scale (x-height ~ 22 px) so relative sizes stay comparable."""
    ink = 255 - g.bitmap
    s = min(22.0 / max(x_height_px, 1.0), (CELL - 12) / max(ink.shape), 4.0)
    small = cv2.resize(
        ink, (max(1, int(ink.shape[1] * s)), max(1, int(ink.shape[0] * s))), interpolation=cv2.INTER_AREA
    )
    cell = np.full((CELL, CELL), 255, np.uint8)
    h, w = small.shape
    h, w = min(h, CELL - 14), min(w, CELL - 4)
    y0, x0 = (CELL - 14 - h) // 2, (CELL - w) // 2
    cell[y0 : y0 + h, x0 : x0 + w] = small[:h, :w]
    return cell


def _sheet(rows: list[tuple[str, list[Glyph]]]) -> bytes:
    height = len(rows) * (CELL + 6) + 6
    width = CELL + 10 + PER_ROW * (CELL + 4)
    sheet = np.full((height, width), 255, np.uint8)
    for r, (ch, gs) in enumerate(rows):
        y = 6 + r * (CELL + 6)
        cv2.rectangle(sheet, (4, y + 8), (CELL - 4, y + CELL - 8), 0, 2)
        (tw, th), _ = cv2.getTextSize(ch, cv2.FONT_HERSHEY_SIMPLEX, 1.1, 2)
        cv2.putText(sheet, ch, ((CELL - tw) // 2, y + (CELL + th) // 2), cv2.FONT_HERSHEY_SIMPLEX, 1.1, 0, 2)
        for i, g in enumerate(gs):
            x = CELL + 10 + i * (CELL + 4)
            sheet[y : y + CELL, x : x + CELL] = _cell(g, g.x_height_px or g.bitmap.shape[0])
            cv2.rectangle(sheet, (x, y), (x + CELL - 1, y + CELL - 1), 200, 1)
            cv2.putText(sheet, str(i + 1), (x + 3, y + CELL - 3), cv2.FONT_HERSHEY_SIMPLEX, 0.38, 90, 1)
    ok, buf = cv2.imencode(".png", sheet)
    assert ok
    return buf.tobytes()


def ascii_label(ch: str) -> bool:
    # OpenCV's Hershey fonts only draw ASCII; other characters are not sent for verification.
    return 33 <= ord(ch) < 127


def verify_with_model(provider, candidates: dict[str, list[Glyph]]) -> dict[str, list[Glyph]] | None:
    """Return the candidates the model confirmed, per character (None if verification failed)."""
    rows = [(ch, gs[:PER_ROW]) for ch, gs in sorted(candidates.items()) if gs and ascii_label(ch)]
    out: dict[str, list[Glyph]] = {ch: gs for ch, gs in candidates.items() if not ascii_label(ch)}
    try:
        for k in range(0, len(rows), ROWS_PER_SHEET):
            batch = rows[k : k + ROWS_PER_SHEET]
            res = provider.complete_json(
                "You check handwriting crops carefully and conservatively.",
                VERIFY_PROMPT + "\nRow labels in order: " + " ".join(repr(ch) for ch, _ in batch),
                VERIFY_SCHEMA,
                max_tokens=8000,
                images=[_sheet(batch)],
            )
            by_label = {r.get("label", ""): r.get("keep", []) for r in res.data.get("rows", [])}
            for ch, gs in batch:
                keep = {int(i) for i in by_label.get(ch, []) if isinstance(i, int | float)}
                out[ch] = [g for i, g in enumerate(gs, 1) if i in keep]
    except Exception as exc:  # verification is best-effort; geometric filtering still applies
        log.warning("glyph verification failed", extra={"error_type": type(exc).__name__})
        return None
    return out
