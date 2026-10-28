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
from app.logging_setup import Timer
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


def _glyph_key(g) -> str:
    import hashlib

    return hashlib.sha1(g.char.encode() + g.bitmap.tobytes() + str(g.bitmap.shape).encode()).hexdigest()


def cached_verify(profile: HandwritingProfile, candidates: dict, verify, progress) -> dict | None:
    """AI letter verification with a per-profile cache of verdicts.

    Letter cutting is deterministic, so a crop seen before (same character, same pixels) keeps its
    verdict; only new crops are sent to the model. Re-processing after adding one photo therefore
    checks only that photo's letters instead of every letter again."""
    import json

    storage = get_storage()
    key = f"{profile_prefix(profile)}/verify_cache.json"
    try:
        cache: dict[str, bool] = json.loads(storage.get(key)) if storage.exists(key) else {}
    except (ValueError, OSError):
        cache = {}
    todo = {ch: [g for g in gs if _glyph_key(g) not in cache] for ch, gs in candidates.items()}
    todo = {ch: gs for ch, gs in todo.items() if gs}
    n_new = sum(len(v) for v in todo.values())
    if n_new:
        progress(0.6, f"Checking {n_new} new cut-out letters with AI")
        checked = verify(todo)
        if checked is None:
            if not cache:
                return None  # verification unavailable and nothing cached: fall back to geometry only
            # Keep this run's new letters unverified (geometric checks still apply); retry next time.
            unverified = {id(g) for gs in todo.values() for g in gs}
            return {
                ch: [g for g in gs if id(g) in unverified or cache.get(_glyph_key(g), False)]
                for ch, gs in candidates.items()
            }
        else:
            for ch, gs in todo.items():
                kept = {id(g) for g in checked.get(ch, [])}
                for g in gs:
                    cache[_glyph_key(g)] = id(g) in kept
            storage.put(key, json.dumps(cache).encode())
    log.info("letter verification", extra={"new": n_new, "cached": sum(len(v) for v in candidates.values()) - n_new})
    return {ch: [g for g in gs if cache.get(_glyph_key(g), False)] for ch, gs in candidates.items()}


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
            from sqlalchemy.orm import object_session

            from app.models import User

            owner = object_session(profile).get(User, profile.user_id)
            provider = get_provider("verify", owner.model_routing if owner else None)

            def verifier(cands):
                from app.ai.usage import usage_scope

                with usage_scope(profile.user_id, "verify", profile_id=profile.id):
                    return cached_verify(profile, cands, lambda todo: verify_with_model(provider, todo), progress)

    with Timer() as t_build:
        result = build_library(inputs, verifier)
    by_id = {s.id: s for s in samples}
    for o in result.samples:
        q = o.quality or {}
        info = q.get("info", {})
        issues = [i.removeprefix("fatal:") for i in q.get("issues", [])]
        log.log(
            logging.INFO if o.status == "processed" else logging.WARNING,
            f"sample {by_id[o.id].original_filename}: {o.status}",
            extra={
                "sample_id": o.id,
                "kind": info.get("kind"),
                "lines": (info.get("metrics") or {}).get("lines"),
                "aligned": info.get("aligned_glyphs"),
                "rejected": info.get("rejected_glyphs"),
                "sliced_words": info.get("sliced_words"),
                "rules": info.get("rules"),
                "template_page": info.get("page"),
                "issues": issues or None,
            },
        )
    rep = result.report
    log.info(
        f"profile built: {len(result.library.glyphs)} characters, {sum(len(v) for v in result.library.glyphs.values())} glyphs",
        extra={
            "ready": result.ready,
            "missing": {k: v for k, v in rep.get("missing", {}).items() if v} or None,
            "derived": rep.get("derived_letters") or None,
            "ai_verified": rep.get("ai_verified_letters"),
            "build_ms": t_build.ms,
            "samples": len(inputs),
        },
    )
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
        "letter_style": "auto",
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
            joined=joined_letters(lib, s),
        ),
    )


def joined_letters(lib: GlyphLibrary, s: RenderSettings) -> bool:
    """Resolve the letter style: an explicit choice wins, "auto" follows how the samples were written."""
    return {"joined": True, "separate": False}.get(s.letter_style, lib.joined_writing)


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
