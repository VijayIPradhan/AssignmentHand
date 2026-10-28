"""Fill printed form fields on the source pages: values in handwriting, the user's signature."""

import random

from app.handwriting.glyphs import Glyph
from app.ingestion.fields import FormField
from app.render.engine import GlyphItem, HandwritingEngine, shift_item
from app.render.layout import LayoutResult
from app.render.settings import RenderSettings

PT_TO_MM = 25.4 / 72


def fill_fields(
    result: LayoutResult,
    fields: list[FormField],
    s: RenderSettings,
    engine: HandwritingEngine,
    signature: Glyph | None,
) -> list[dict]:
    """Add field values / signatures to the pages that came from the source PDF. Returns warnings."""
    warnings: list[dict] = []
    values = {k.strip().lower(): v for k, v in (s.field_values or {}).items() if v and v.strip()}
    pages = {p.source_page: p for p in result.pages if p.source_page is not None}
    signed = written = 0
    for f in fields:
        page = pages.get(f.page)
        if page is None:
            continue
        x0, x1 = f.x0 * PT_TO_MM, f.x1 * PT_TO_MM
        top, bottom = f.top * PT_TO_MM, f.bottom * PT_TO_MM
        line_y = top + 0.82 * (bottom - top)  # the underscores sit about here; writing rests on them
        width = x1 - x0
        rng = random.Random(f"{s.seed}|{f.page}|{f.key}")
        if f.kind == "signature":
            if not s.sign_fields:
                continue
            if signature is None:
                warnings.append({"type": "no_signature", "message": "Signing is on but no signature has been uploaded"})
                continue
            bmp_h, bmp_w = signature.bitmap.shape
            target_h = s.signature_height_mm
            # Fit the field's width (a little overhang is natural); never taller than requested.
            ppm = max(bmp_h / target_h, bmp_w / (width + 8.0))
            # The id includes the scale: the PDF draws each id once and reuses it.
            g = Glyph(
                signature.char,
                signature.bitmap,
                ppm,
                signature.baseline_px,
                "signature",
                "",
                f"{signature.id}-{round(ppm * 100)}",
            )
            page.items.append(
                GlyphItem(
                    g,
                    x0 + 1.0,
                    line_y + 0.4,
                    1.0,
                    rng.gauss(0, 0.6),
                    0.0,
                    1.0 / max(s.stroke_weight, 0.1),
                    s.signature_color,
                )
            )
            signed += 1
        else:
            value = values.get(f.key) or values.get(f.label.strip().lower())
            if not value:
                continue
            run = engine.shape(value, rng, 1.0)
            scale = 1.0
            if run.width > width + 6.0:
                scale = max(0.6, (width + 6.0) / run.width)
                run = engine.shape(value, random.Random(f"{s.seed}|{f.page}|{f.key}"), scale)
            page.items += [shift_item(it, x0 + 1.5, line_y - 0.5) for it in run.items]
            written += 1
    if s.sign_fields and signature is not None and not any(f.kind == "signature" for f in fields):
        warnings.append({"type": "no_signature_field", "message": "No signature line was found on the pages"})
    if signed:
        warnings.append({"type": "signed", "message": f"Your signature was added on {signed} page(s)"})
    if written:
        warnings.append({"type": "fields_filled", "message": f"{written} field(s) were filled in your handwriting"})
    return warnings
