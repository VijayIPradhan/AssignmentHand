"""Handwriting profile operations: processing samples into a glyph library and previews."""

import io
import logging
import threading
from collections import OrderedDict

import numpy as np
from PIL import Image
from sqlalchemy.orm import Session

from app.handwriting.glyphs import GlyphLibrary
from app.handwriting.preprocess import SampleImageError, load_image
from app.handwriting.profile_builder import SampleInput, build_library
from app.models import HandwritingProfile, ProfileStatus, SampleStatus
from app.render.engine import EngineStyle, GlyphCompositingEngine
from app.render.layout import Layouter, SectionInput
from app.render.pdf import rasterize, render_pdf
from app.render.settings import Margins, RenderSettings
from app.storage import get_storage

log = logging.getLogger(__name__)

_LIB_CACHE: OrderedDict[tuple[str, int], GlyphLibrary] = OrderedDict()
_LIB_LOCK = threading.Lock()
_LIB_CACHE_SIZE = 8


def profile_prefix(p: HandwritingProfile) -> str:
    return f"users/{p.user_id}/profiles/{p.id}"


def load_library(p: HandwritingProfile) -> GlyphLibrary:
    if not p.glyph_library_key:
        raise ValueError("profile has no glyph library yet")
    key = (p.id, p.library_version)
    with _LIB_LOCK:
        lib = _LIB_CACHE.get(key)
        if lib is not None:
            _LIB_CACHE.move_to_end(key)
            return lib
    lib = GlyphLibrary.from_bytes(get_storage().get(p.glyph_library_key))
    with _LIB_LOCK:
        _LIB_CACHE[key] = lib
        while len(_LIB_CACHE) > _LIB_CACHE_SIZE:
            _LIB_CACHE.popitem(last=False)
    return lib


def forget_library(profile_id: str) -> None:
    with _LIB_LOCK:
        for k in [k for k in _LIB_CACHE if k[0] == profile_id]:
            del _LIB_CACHE[k]


def process_profile(db: Session, profile: HandwritingProfile, progress) -> dict:
    storage = get_storage()
    samples = list(profile.samples)
    if not samples:
        profile.status = ProfileStatus.needs_samples
        profile.quality_report = {"advice": ["Upload at least one filled-in template sheet."]}
        db.commit()
        return {"ready": False}
    inputs: list[SampleInput] = []
    for i, s in enumerate(samples):
        progress(0.05 + 0.4 * i / len(samples), f"Reading sample {i + 1} of {len(samples)}")
        try:
            img = load_image(storage.get(s.storage_key), s.mime_type)
        except SampleImageError as exc:
            s.status = SampleStatus.rejected
            s.quality = {"issues": [f"fatal:{exc}"], "ok": False}
            continue
        inputs.append(SampleInput(s.id, s.kind, img, s.corrections or {}, s.transcript, s.template_page))
    progress(0.5, "Extracting letter shapes")
    verifier = None
    if any(s.kind == "freeform" for s in inputs) and (profile.style or {}).get("verify_letters_with_ai", True):
        from app.ai.providers import get_provider, provider_configured
        from app.handwriting.verify import verify_with_model

        if provider_configured():
            provider = get_provider("verify")

            def verifier(cands):
                progress(0.6, "Checking cut-out letters with AI")
                return verify_with_model(provider, cands)

    result = build_library(inputs, verifier)
    by_id = {s.id: s for s in samples}
    for o in result.samples:
        s = by_id[o.id]
        s.status = SampleStatus.processed if o.status == "processed" else SampleStatus.rejected
        s.quality = o.quality
        if o.processed_png:
            key = f"{profile_prefix(profile)}/samples/{s.id}/processed.png"
            storage.put(key, o.processed_png)
            s.processed_key = key
    progress(0.85, "Saving glyph library")
    lib = result.library
    if lib.glyphs:
        version = profile.library_version + 1
        key = f"{profile_prefix(profile)}/library/v{version}.json"
        storage.put(key, lib.to_bytes())
        old = profile.glyph_library_key
        profile.glyph_library_key = key
        profile.library_version = version
        if old and old != key:
            storage.delete(old)
        forget_library(profile.id)
    profile.metrics = lib.metrics() if lib.glyphs else {}
    profile.coverage = lib.coverage()
    profile.quality_report = result.report
    profile.status = ProfileStatus.ready if result.ready else ProfileStatus.needs_samples
    profile.error = None
    db.commit()
    return {"ready": result.ready, "glyphs": sum(len(v) for v in lib.glyphs.values())}


def style_from(profile: HandwritingProfile, overrides: dict | None = None) -> dict:
    base = {
        "handwriting_size_mm": 2.8,
        "stroke_weight": 1.0,
        "slant_offset_deg": 0.0,
        "char_spacing": 1.0,
        "word_spacing": 1.0,
        "variation": 0.5,
        "ink_color": "#1b2a6b",
    }
    base.update({k: v for k, v in (profile.style or {}).items() if k in base})
    if overrides:
        base.update({k: v for k, v in overrides.items() if k in base and v is not None})
    return base


def engine_for(lib: GlyphLibrary, s: RenderSettings) -> GlyphCompositingEngine:
    return GlyphCompositingEngine(
        lib,
        EngineStyle(
            x_height_mm=s.handwriting_size_mm,
            weight=s.stroke_weight,
            slant_offset_deg=s.slant_offset_deg,
            char_spacing=s.char_spacing,
            word_spacing=s.word_spacing,
            variation=s.variation,
        ),
    )


def preview_png(
    profile: HandwritingProfile, text: str, overrides: dict | None = None, paper: str = "ruled"
) -> tuple[bytes, list[dict]]:
    lib = load_library(profile)
    style = style_from(profile, overrides)
    s = RenderSettings(
        paper=paper,
        margin_line=False,
        page_numbers=False,
        include_question_text=False,
        paragraph_indent_mm=0,
        margins=Margins(top=8, bottom=8, left=10, right=10),
        line_spacing_mm=max(4.0, style["handwriting_size_mm"] * 2.9),
        **style,
    )
    layout = Layouter(engine_for(lib, s), s).run([SectionInput("preview", "", "", text)])
    page = layout.pages[0]
    pdf = render_pdf(layout, s, title="Preview")
    png = rasterize(pdf, dpi=150, max_pages=1)[0]
    # Crop to the used height so short previews are not a whole blank page.
    ys = [getattr(it, "y", 0.0) for it in page.items]
    used_mm = (max(ys) if ys else 20) + s.line_spacing_mm * 1.2
    im = Image.open(io.BytesIO(png))
    h = min(im.height, int(used_mm / layout.height_mm * im.height) + 10)
    out = io.BytesIO()
    im.crop((0, 0, im.width, max(h, 60))).save(out, format="PNG", optimize=True)
    return out.getvalue(), layout.warnings


def glyph_sheet_png(profile: HandwritingProfile) -> bytes:
    """Contact sheet of every captured glyph, for reviewing the profile."""
    lib = load_library(profile)
    cell, cols = 64, 16
    entries = [(ch, g) for ch in sorted(lib.glyphs) for g in lib.glyphs[ch]]
    rows = max(1, -(-len(entries) // cols))
    sheet = np.full((rows * cell, cols * cell), 255, np.uint8)
    for i, (_ch, g) in enumerate(entries):
        r, c = divmod(i, cols)
        bmp = 255 - g.bitmap
        scale = min((cell - 8) / max(bmp.shape), 1.0 * (cell - 8) / max(1, bmp.shape[0]))
        im = Image.fromarray(bmp).resize((max(1, int(bmp.shape[1] * scale)), max(1, int(bmp.shape[0] * scale))))
        a = np.asarray(im)
        y0 = r * cell + (cell - a.shape[0]) // 2
        x0 = c * cell + (cell - a.shape[1]) // 2
        sheet[y0 : y0 + a.shape[0], x0 : x0 + a.shape[1]] = np.minimum(
            sheet[y0 : y0 + a.shape[0], x0 : x0 + a.shape[1]], a
        )
    out = io.BytesIO()
    Image.fromarray(sheet).save(out, format="PNG", optimize=True)
    return out.getvalue()
