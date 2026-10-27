"""Combine processed samples into a single normalized glyph library plus a quality report."""

from dataclasses import dataclass, field

import numpy as np

from app.handwriting.charset import DIGITS, LOWER, UPPER
from app.handwriting.glyphs import GlyphLibrary
from app.handwriting.preprocess import (
    SampleImageError,
    apply_corrections,
    assess_quality,
    binarize,
    encode_png,
    normalize_illumination,
    to_gray,
)
from app.handwriting.segment import SegmentResult, TemplateNotFound, analyze_freeform, extract_template

DEFAULT_X_HEIGHT_MM = 3.0
ESSENTIAL_PUNCT = ".,;:!?'()-"


@dataclass
class SampleInput:
    id: str
    kind: str  # template | freeform
    image: np.ndarray
    corrections: dict = field(default_factory=dict)
    transcript: str | None = None
    template_page: int | None = None


@dataclass
class SampleOutcome:
    id: str
    status: str  # processed | rejected
    quality: dict
    processed_png: bytes | None = None


@dataclass
class BuildResult:
    library: GlyphLibrary
    samples: list[SampleOutcome]
    report: dict
    ready: bool


def _process_one(s: SampleInput) -> tuple[SegmentResult | None, dict]:
    img = apply_corrections(s.image, s.corrections)
    gray_norm = normalize_illumination(to_gray(img))
    q = assess_quality(img, gray_norm, binarize(gray_norm))
    quality = q.as_dict()
    if not q.ok:
        return None, quality
    try:
        if s.kind == "template":
            seg = extract_template(img, s.id, s.template_page)
        else:
            seg = analyze_freeform(img, s.id, s.transcript)
    except TemplateNotFound as exc:
        quality["issues"].append(f"fatal:{exc}")
        quality["ok"] = False
        return None, quality
    quality["issues"] += seg.issues
    quality["ok"] = not any(i.startswith("fatal:") for i in quality["issues"])
    quality["info"] = seg.info
    return (seg if quality["ok"] else None), quality


def _feature(g) -> np.ndarray:
    import cv2

    b = g.bitmap
    h, w = b.shape
    side = max(h, w)
    sq = np.zeros((side, side), np.uint8)
    sq[(side - h) // 2 : (side - h) // 2 + h, (side - w) // 2 : (side - w) // 2 + w] = b
    v = cv2.resize(sq, (20, 20), interpolation=cv2.INTER_AREA).astype(np.float32).ravel() / 255.0
    v = v / (np.linalg.norm(v) + 1e-6)
    return np.concatenate([v, [0.6 * np.log(max(w, 1) / max(h, 1)), 0.4 * g.top_mm, 0.4 * g.bottom_mm]])


def consistent_variants(glyphs: list, keep_max: int = 6) -> list:
    """Keep the variants that agree with each other (closest to the medoid), drop the rest.

    Automatic slicing of free-form writing mislabels some cuts; mislabelled ones look unlike the
    majority of samples for that character."""
    if len(glyphs) < 3:
        return glyphs
    f = np.stack([_feature(g) for g in glyphs])
    d = np.linalg.norm(f[:, None, :] - f[None, :, :], axis=2)
    medoid = int(np.argmin(d.sum(axis=1)))
    dist = d[medoid]
    order = np.argsort(dist)
    n_keep = max(2, min(keep_max, round(len(glyphs) * 0.4)))
    return [glyphs[i] for i in order[:n_keep]]


# Missing letter -> (source letter, transform). In most handwriting a "u" is an "n" turned upside
# down and "q"/"d" mirror "p"/"b". Used only when the user's own letter is unavailable.
DERIVED = {
    "u": ("n", "rot180"),
    "n": ("u", "rot180"),
    "q": ("p", "mirror"),
    "p": ("q", "mirror"),
    "d": ("b", "mirror"),
    "b": ("d", "mirror"),
}


def add_derived(lib: GlyphLibrary) -> list[str]:
    import dataclasses

    made = []
    for ch, (src, how) in DERIVED.items():
        if lib.has(ch) or not lib.has(src):
            continue
        for g in lib.variants(src)[:3]:
            bmp = g.bitmap[::-1, ::-1] if how == "rot180" else g.bitmap[:, ::-1]
            lib.add(dataclasses.replace(g, char=ch, bitmap=bmp.copy(), source="derived", id="", _outlines={}))
        made.append(f"{ch} (from {src})")
    return made


def _drop_outliers(lib: GlyphLibrary) -> list[str]:
    dropped = []
    for ch, variants in list(lib.glyphs.items()):
        if len(variants) < 3:
            continue
        hs = np.array([g.height_mm for g in variants])
        med = float(np.median(hs))
        keep = [g for g, h in zip(variants, hs, strict=True) if med / 2.5 <= h <= med * 2.5]
        if len(keep) < len(variants):
            dropped.append(ch)
            lib.glyphs[ch] = keep
    return dropped


def build_library(samples: list[SampleInput], verifier=None) -> BuildResult:
    """Build the glyph library. ``verifier(candidates) -> candidates | None`` optionally confirms
    automatically cut free-form letters (see ``verify.py``)."""
    outcomes: list[SampleOutcome] = []
    templates: list[SegmentResult] = []
    freeforms: list[SegmentResult] = []
    for s in samples:
        try:
            seg, quality = _process_one(s)
        except SampleImageError as exc:
            seg, quality = None, {"issues": [f"fatal:{exc}"], "ok": False}
        png = encode_png(seg.processed) if seg is not None and seg.processed is not None else None
        outcomes.append(SampleOutcome(s.id, "processed" if seg else "rejected", quality, png))
        if seg is not None:
            (templates if s.kind == "template" else freeforms).append(seg)

    lib = GlyphLibrary(x_height_mm=DEFAULT_X_HEIGHT_MM)
    for seg in templates:
        for g in seg.glyphs:
            lib.add(g)
    if templates:
        lib.compute_metrics()

    ff_metrics = [seg.info.get("metrics", {}) for seg in freeforms]
    ff_by_char: dict[str, list] = {}
    for seg in freeforms:
        xh_px = seg.info.get("metrics", {}).get("x_height_px")
        if not xh_px:
            continue
        for g in seg.glyphs:
            g.px_per_mm = (g.x_height_px or xh_px) / lib.x_height_mm
            ff_by_char.setdefault(g.char, []).append(g)
    verified = False
    if verifier is not None and ff_by_char:
        # Pre-select the most self-consistent candidates so the sheets stay small.
        pre = {ch: consistent_variants(gs, keep_max=16) if len(gs) > 16 else gs for ch, gs in ff_by_char.items()}
        checked = verifier(pre)
        if checked is not None:
            ff_by_char = {ch: gs for ch, gs in checked.items() if gs}
            verified = True
    for ch, gs in ff_by_char.items():
        # Template glyphs are labelled by the sheet itself; only free-form ones need the vote.
        for g in consistent_variants(gs, keep_max=6 if not lib.has(ch) else 2):
            lib.add(g)

    def _med(key: str) -> float | None:
        vals = [m[key] for m in ff_metrics if m.get(key) is not None]
        return float(np.median(vals)) if vals else None

    for key in ("letter_gap_ratio", "word_gap_ratio", "line_spacing_ratio"):
        v = _med(key)
        if v is not None:
            setattr(lib, key, float(np.clip(v, 0.05, 6.0)))
    dropped = _drop_outliers(lib)
    derived = add_derived(lib)
    has_template_slant = templates and any(lib.variants(c) for c in "bdfhkltI1")
    lib.compute_metrics()
    if not has_template_slant and (v := _med("slant_deg")) is not None:
        lib.slant_deg = v

    cov = lib.coverage()
    missing_lower = [c for c in LOWER if c not in cov]
    missing_upper = [c for c in UPPER if c not in cov]
    missing_digits = [c for c in DIGITS if c not in cov]
    missing_punct = [c for c in ESSENTIAL_PUNCT if c not in cov]
    single_variant = [c for c in LOWER if cov.get(c) == 1]
    ready = len(missing_lower) <= 2 and lib.glyphs != {}
    advice = []
    if not samples:
        advice.append("Upload at least one filled-in template sheet.")
    if missing_lower:
        advice.append("Missing lowercase letters: " + " ".join(missing_lower))
    if missing_upper:
        advice.append("Missing capitals (a printed fallback will be used): " + " ".join(missing_upper))
    if missing_digits:
        advice.append("Missing digits: " + " ".join(missing_digits))
    if missing_punct:
        advice.append("Missing punctuation: " + " ".join(missing_punct))
    if single_variant and len(single_variant) > 8:
        advice.append("Only one variant for many letters; add another sheet for more natural variation.")
    if derived:
        advice.append("Approximated from your other letters: " + ", ".join(derived) + ". Add them for a closer match.")
    if not templates and freeforms:
        advice.append(
            "Only free-form samples were provided. Letter shapes come from the template sheet; "
            "free-form samples contribute slant, spacing and stroke measurements."
        )
    report = {
        "coverage": cov,
        "missing": {
            "lowercase": missing_lower,
            "uppercase": missing_upper,
            "digits": missing_digits,
            "punctuation": missing_punct,
        },
        "outliers_removed": dropped,
        "advice": advice,
        "template_pages": sorted({seg.info.get("page", 0) + 1 for seg in templates}),
        "freeform_samples": len(freeforms),
        "rejected_samples": sum(1 for o in outcomes if o.status == "rejected"),
        "ai_verified_letters": verified,
        "derived_letters": derived,
    }
    return BuildResult(lib, outcomes, report, ready)
