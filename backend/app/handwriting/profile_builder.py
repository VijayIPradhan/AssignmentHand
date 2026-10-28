"""Combine processed samples into a single normalized glyph library plus a quality report."""

from dataclasses import dataclass, field

import numpy as np

from app.handwriting.charset import DIGITS, LOWER, UPPER
from app.handwriting.glyphs import GlyphLibrary, crop_to_ink
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
POOR_PHOTO = ("image looks blurry", "low resolution")


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


MID_HEIGHT_MARKS = "-=+~×÷"


def centre_mid_height_marks(lib: GlyphLibrary) -> None:
    """Hyphens and similar marks always sit at about half the x-height. In free-form samples their
    vertical position is ambiguous (e.g. written on a punctuation-only line), so place them there."""
    for ch in MID_HEIGHT_MARKS:
        for g in lib.variants(ch):
            if g.source == "template":
                continue  # the template's printed baseline already fixes their position
            g.baseline_px = g.bitmap.shape[0] / 2 + 0.45 * lib.x_height_mm * g.px_per_mm
            g._outlines.clear()


LOOPED = "abdegopq"  # letters drawn around an open counter (the eye of an e, the bowl of a, o, p ...)


def has_counter(g) -> bool:
    """True if the glyph's loop is open: its ink encloses a hole of a visible size."""
    import cv2

    m = (g.bitmap > 127).astype(np.uint8)
    contours, hier = cv2.findContours(m, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    if hier is None:
        return False
    min_area = max(4.0, 0.004 * m.size)
    return any(h[3] >= 0 and cv2.contourArea(c) >= min_area for c, h in zip(contours, hier[0], strict=True))


def _drop_filled(lib: GlyphLibrary) -> list[str]:
    """Drop variants of looped letters whose loop is filled in (ink blob from a blurry photo or a thick
    pen) when an open variant exists. Returns the letters that only have filled variants."""
    only_filled = []
    for ch in LOOPED:
        variants = lib.variants(ch)
        if not variants:
            continue
        open_ = [g for g in variants if has_counter(g)]
        if open_:
            lib.glyphs[ch] = open_
        else:
            only_filled.append(ch)
    return only_filled


MULTI_PART = set('ij:;!?"%=÷≈≠')  # characters whose small separate marks belong to them
ON_LINE = set("acemnorsuvwxbdhklt") | set("ABCDEFGHIKLMNOPRSTUVWXYZ") | set("0123568")  # no descender


def clean_glyph(g) -> bool:
    """Tidy a cut-out letter in place; True if it changed.

    Removes small separate specks (a neighbour's i-dot or comma caught in the cut) from letters that
    have no separate marks of their own, and sits letters without descenders on the baseline (the line
    estimate on a photo can be off by a few pixels)."""
    import cv2

    changed = False
    bmp = g.bitmap
    if g.char not in MULTI_PART:
        n, labels, stats, _ = cv2.connectedComponentsWithStats((bmp > 127).astype(np.uint8), connectivity=8)
        if n > 2:
            areas = stats[1:, cv2.CC_STAT_AREA]
            total = float(areas.sum())
            main = 1 + int(np.argmax(areas))
            mx, my, mw, mh = stats[main, :4]
            keep = np.zeros(n, bool)
            keep[main] = True
            for i in range(1, n):
                x, y, w, h, a = stats[i]
                near = x < mx + mw * 1.1 and x + w > mx - mw * 0.1 and y < my + mh and y + h > my
                keep[i] = keep[i] or a >= 0.08 * total or near
            if not keep[1:].all():
                cropped = crop_to_ink(np.where(keep[labels], bmp, 0).astype(np.uint8))
                if cropped is not None:
                    bmp, oy, _ox = cropped
                    g.bitmap = bmp
                    g.baseline_px -= oy
                    changed = True
    if g.char in ON_LINE:
        rows = np.nonzero((bmp > 127).any(axis=1))[0]
        if len(rows):
            ink_bottom = rows.max() + 1
            if abs(ink_bottom - g.baseline_px) > 0.06 * bmp.shape[0]:
                g.baseline_px = float(ink_bottom)
                changed = True
    if changed:
        g._outlines.clear()
    return changed


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
    poor: set[str] = set()  # samples whose photo is blurry or low resolution
    for s in samples:
        try:
            seg, quality = _process_one(s)
        except SampleImageError as exc:
            seg, quality = None, {"issues": [f"fatal:{exc}"], "ok": False}
        png = encode_png(seg.processed) if seg is not None and seg.processed is not None else None
        outcomes.append(SampleOutcome(s.id, "processed" if seg else "rejected", quality, png))
        if any(i.startswith(POOR_PHOTO) for i in quality.get("issues", [])):
            poor.add(s.id)
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

    # A letter written on its own is far more reliable than one cut out of a joined word: when a
    # character has usable separately written samples (sharp photo, loop open), the automatically
    # sliced ones are not used. Otherwise the sliced ones are checked too and may replace them.
    def usable(g) -> bool:
        return g.sample_id not in poor and (g.char not in LOOPED or has_counter(g))

    for ch, gs in ff_by_char.items():
        direct = [g for g in gs if g.source == "freeform"]
        if any(usable(g) for g in direct):
            ff_by_char[ch] = direct
    verified = False
    if verifier is not None and ff_by_char:
        # Pre-select the most self-consistent candidates so the sheets stay small.
        pre = {ch: consistent_variants(gs, keep_max=16) if len(gs) > 16 else gs for ch, gs in ff_by_char.items()}
        checked = verifier(pre)
        if checked is not None:
            ff_by_char = {ch: gs for ch, gs in checked.items() if gs}
            verified = True
    for gs in ff_by_char.values():
        for g in gs:
            clean_glyph(g)
    for ch, gs in ff_by_char.items():
        # Best available tier (after verification, so a rejected letter never displaces an accepted
        # one): usable separately written letters, then usable cut-out ones, then any from a sharp photo
        # (blurry or low-resolution photos give lumpy, heavier letters), then whatever there is.
        direct = [g for g in gs if g.source == "freeform"]
        gs = next(
            t
            for t in (
                [g for g in direct if usable(g)],
                [g for g in gs if usable(g)],
                [g for g in gs if g.sample_id not in poor],
                gs,
            )
            if t
        )
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
    lib.joined_writing = any(seg.info.get("joined_writing") for seg in freeforms)
    dropped = _drop_outliers(lib)
    filled = _drop_filled(lib)
    derived = add_derived(lib)
    centre_mid_height_marks(lib)
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
    if filled:
        advice.append(
            "The loop is filled in on every sample of: " + " ".join(filled) + ". Write these a little larger with "
            "a fine pen and scan them (or photograph in good light) so the loop stays open."
        )
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
        "filled_letters": filled,
    }
    return BuildResult(lib, outcomes, report, ready)
