"""Extract glyphs from template sheets and style metrics (plus optional glyphs) from free-form samples."""

import itertools
from dataclasses import dataclass, field

import cv2
import numpy as np

from app.handwriting import template as T
from app.handwriting.cursive import group_words, plausible, shape_plausible, slice_positions, weight
from app.handwriting.glyphs import Glyph, crop_to_ink, slant_deg, stroke_width_px
from app.handwriting.preprocess import (
    binarize,
    estimate_skew,
    normalize_illumination,
    remove_ruling,
    remove_specks,
    rotate_bound,
    to_gray,
)

CANON_PPM = 12.0  # canonical pixels per mm for warped template pages
CELL_INSET_MM = 1.3


@dataclass
class SegmentResult:
    glyphs: list[Glyph] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    info: dict = field(default_factory=dict)
    processed: np.ndarray | None = None  # visualisation for the review UI


class TemplateNotFound(ValueError):
    pass


# ---------------------------------------------------------------------------------------------
# Template sheets
# ---------------------------------------------------------------------------------------------


def _marker_candidates(mask: np.ndarray) -> list[tuple[float, float, float]]:
    h, w = mask.shape
    side = min(h, w)
    lo, hi = (0.012 * side) ** 2, (0.10 * side) ** 2
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out = []
    for c in contours:
        area = cv2.contourArea(c)
        if not lo <= area <= hi:
            continue
        (cx, cy), (rw, rh), _ = cv2.minAreaRect(c)
        if rw == 0 or rh == 0 or not 0.7 <= rw / rh <= 1.4:
            continue
        if area / (rw * rh) < 0.82:
            continue
        hull = cv2.convexHull(c)
        if area / max(cv2.contourArea(hull), 1) < 0.9:
            continue
        out.append((float(cx), float(cy), float(area)))
    return out


def _order_quad(pts: np.ndarray) -> np.ndarray:
    """Order 4 points TL, TR, BR, BL by image geometry."""
    s = pts.sum(axis=1)
    d = pts[:, 0] - pts[:, 1]
    return np.array([pts[np.argmin(s)], pts[np.argmax(d)], pts[np.argmax(s)], pts[np.argmin(d)]], dtype=np.float32)


def find_markers(mask: np.ndarray) -> np.ndarray:
    cands = _marker_candidates(mask)
    if len(cands) < 4:
        raise TemplateNotFound("Could not find the four corner squares of the template sheet")
    cands.sort(key=lambda c: -c[2])
    cands = cands[:12]
    best, best_area = None, 0.0
    for combo in itertools.combinations(cands, 4):
        areas = [c[2] for c in combo]
        if max(areas) > 2.5 * min(areas):
            continue
        pts = np.array([(c[0], c[1]) for c in combo], dtype=np.float32)
        quad = _order_quad(pts)
        if len({tuple(p) for p in quad}) < 4:
            continue
        a = cv2.contourArea(quad)
        if a > best_area:
            best, best_area = quad, a
    if best is None or best_area < 0.15 * mask.size:
        raise TemplateNotFound("Corner squares were found but do not frame a full page")
    return best


def _ink_fraction(mask: np.ndarray, x: float, y: float, r: float) -> float:
    h, w = mask.shape
    x0, x1 = int(max(0, x - r)), int(min(w, x + r + 1))
    y0, y1 = int(max(0, y - r)), int(min(h, y + r + 1))
    if x1 <= x0 or y1 <= y0:
        return 0.0
    return float((mask[y0:y1, x0:x1] > 0).mean())


def _dst_markers_px() -> np.ndarray:
    return np.array([(x * CANON_PPM, y * CANON_PPM) for x, y in T.marker_centers_mm()], dtype=np.float32)


def resolve_orientation(mask: np.ndarray, quad: np.ndarray) -> tuple[np.ndarray, int]:
    """Try the four cyclic corner assignments; return (homography, page index)."""
    dst = _dst_markers_px()
    src_side = float(np.linalg.norm(quad[0] - quad[1]))
    dst_side = float(np.linalg.norm(dst[0] - dst[1]))
    r_src = 0.3 * T.CODE_BOX_MM * CANON_PPM * src_side / dst_side
    max_page = T.page_count()
    for k in range(4):
        src = np.roll(quad, -k, axis=0)
        H = cv2.getPerspectiveTransform(src, dst)
        Hinv = np.linalg.inv(H)
        centers = np.array([[(x * CANON_PPM, y * CANON_PPM) for x, y in T.code_box_centers_mm()]], dtype=np.float32)
        mirror = np.array(
            [[(x * CANON_PPM, (T.PAGE_H_MM - y) * CANON_PPM) for x, y in T.code_box_centers_mm()]], dtype=np.float32
        )
        src_pts = cv2.perspectiveTransform(centers, Hinv)[0]
        src_mirror = cv2.perspectiveTransform(mirror, Hinv)[0]
        bits = [_ink_fraction(mask, x, y, r_src) > 0.5 for x, y in src_pts]
        if not bits[0]:
            continue
        if _ink_fraction(mask, *src_mirror[0], r_src) > 0.5:
            continue  # ambiguous: something dark where the code would be if rotated 180°
        page = sum(1 << i for i, b in enumerate(bits[1:]) if b)
        if page < max_page:
            return H, page
    raise TemplateNotFound("Found the corner squares but could not read the page code")


def extract_template(img: np.ndarray, sample_id: str, page_hint: int | None = None) -> SegmentResult:
    gray = to_gray(img)
    norm = normalize_illumination(gray)
    mask = binarize(norm)
    quad = find_markers(mask)
    try:
        H, page = resolve_orientation(mask, quad)
    except TemplateNotFound:
        if page_hint is None:
            raise
        H, page = cv2.getPerspectiveTransform(quad, _dst_markers_px()), page_hint
    W, Hh = int(T.PAGE_W_MM * CANON_PPM), int(T.PAGE_H_MM * CANON_PPM)
    warped = cv2.warpPerspective(norm, H, (W, Hh), flags=cv2.INTER_LINEAR, borderValue=255)
    wmask = binarize(warped)

    res = SegmentResult(info={"page": page, "kind": "template"})
    if page_hint is not None and page_hint != page:
        res.issues.append(f"page code says page {page + 1}, but page {page_hint + 1} was selected; using {page + 1}")

    vis = cv2.cvtColor(warped, cv2.COLOR_GRAY2BGR)
    inset = int(CELL_INSET_MM * CANON_PPM)
    empty, clipped = [], []
    min_ink = 25
    for cell in T.cells_for_page(page):
        x0 = int(cell.x_mm * CANON_PPM) + inset
        y0 = int(cell.y_mm * CANON_PPM) + inset
        x1 = int((cell.x_mm + T.CELL_W_MM) * CANON_PPM) - inset
        y1 = int((cell.y_mm + T.CELL_H_MM) * CANON_PPM) - inset
        sub = remove_specks(wmask[y0:y1, x0:x1], min_area=10)
        if int((sub > 0).sum()) < min_ink:
            empty.append(cell.char)
            cv2.rectangle(vis, (x0, y0), (x1, y1), (180, 180, 180), 2)
            continue
        if sub[:2].any() or sub[-2:].any() or sub[:, :2].any() or sub[:, -2:].any():
            clipped.append(cell.char)
        cropped = crop_to_ink(sub)
        assert cropped is not None
        bmp, cy0, _cx0 = cropped
        baseline_px = (cell.baseline_y_mm * CANON_PPM - y0) - cy0
        res.glyphs.append(Glyph(cell.char, bmp, CANON_PPM, float(baseline_px), "template", sample_id))
        color = (40, 160, 40) if cell.char not in clipped else (0, 140, 255)
        cv2.rectangle(vis, (x0, y0), (x1, y1), color, 3)
    res.processed = vis
    res.info.update(glyphs=len(res.glyphs), empty_cells=len(empty))
    if clipped:
        res.issues.append("ink touches the box edge for: " + " ".join(sorted(set(clipped))) + " (glyph may be clipped)")
    if len(res.glyphs) < 10:
        res.issues.append("fatal:very few characters were found on this sheet")
    return res


# ---------------------------------------------------------------------------------------------
# Free-form samples
# ---------------------------------------------------------------------------------------------


def _line_bands(mask: np.ndarray) -> list[tuple[int, int]]:
    """Split unruled writing into lines. Line cores are peaks of the row-ink profile at least about
    one line pitch apart (pitch from the profile autocorrelation, never less than a typical letter
    height); lines are divided at the profile minimum between neighbouring peaks, so touching
    ascenders/descenders do not merge lines."""
    h = mask.shape[0]
    prof = (mask > 0).sum(axis=1).astype(np.float64)
    if prof.max() == 0:
        return []
    k = max(3, h // 150)
    prof = np.convolve(prof, np.ones(k) / k, mode="same")
    _n, _l, st, _c = cv2.connectedComponentsWithStats(mask, connectivity=8)
    heights = st[1:, cv2.CC_STAT_HEIGHT]
    heights = heights[heights > max(4, h // 400)]
    comp_h = float(np.median(heights)) if len(heights) else h / 10
    centred = prof - prof.mean()
    ac = np.correlate(centred, centred, mode="full")[len(prof) - 1 :]
    lag_min = int(max(8, comp_h * 1.2))
    lag_max = max(lag_min + 1, h // 2)
    pitch = lag_min + int(np.argmax(ac[lag_min:lag_max])) if lag_max > lag_min + 1 else int(comp_h * 2)
    d = int(max(pitch * 0.5, comp_h * 0.9))
    floor = 0.15 * prof.max()
    peaks: list[int] = []
    for y in range(h):
        if prof[y] < floor:
            continue
        lo, hi = max(0, y - d), min(h, y + d + 1)
        if prof[y] >= prof[lo:hi].max() and (not peaks or y - peaks[-1] > d):
            peaks.append(y)
    if not peaks:
        return []
    nz = np.nonzero(prof > 0.02 * prof.max())[0]
    bounds = [int(nz[0])]
    for a, b in itertools.pairwise(peaks):
        bounds.append(a + int(np.argmin(prof[a:b])))
    bounds.append(int(nz[-1]) + 1)
    return [(bounds[i], bounds[i + 1]) for i in range(len(peaks))]


def _core_height(mask_line: np.ndarray) -> float:
    prof = (mask_line > 0).sum(axis=1).astype(np.float32)
    if prof.max() == 0:
        return 0.0
    return float((prof > 0.45 * prof.max()).sum())


@dataclass
class _Cluster:
    x0: int
    x1: int
    y0: int
    y1: int
    labels: list[int]

    @property
    def w(self) -> int:
        return self.x1 - self.x0


def _clusters_for_line(labels: np.ndarray, stats: np.ndarray, ids: list[int]) -> list[_Cluster]:
    comps = sorted(ids, key=lambda i: stats[i, cv2.CC_STAT_LEFT])
    out: list[_Cluster] = []
    for i in comps:
        x, y, w, h = (int(stats[i, k]) for k in (0, 1, 2, 3))
        if out:
            prev = out[-1]
            overlap = min(prev.x1, x + w) - max(prev.x0, x)
            if overlap > 0.5 * min(prev.w, w) or (overlap > 0 and min(prev.w, w) < 0.35 * max(prev.w, w)):
                prev.x0, prev.x1 = min(prev.x0, x), max(prev.x1, x + w)
                prev.y0, prev.y1 = min(prev.y0, y), max(prev.y1, y + h)
                prev.labels.append(i)
                continue
        out.append(_Cluster(x, x + w, y, y + h, [i]))
    return out


@dataclass
class _TextLine:
    ids: list[int]
    tilt: np.ndarray  # per-column baseline offset relative to the line's mid-point (px)
    core_top: float = 0.0  # in flattened coordinates
    core_bottom: float = 0.0
    clusters: list[_Cluster] = field(default_factory=list)

    @property
    def x_height(self) -> float:
        return self.core_bottom - self.core_top

    def baseline_at(self, x: float) -> float:
        xi = int(min(max(x, 0), len(self.tilt) - 1))
        return self.core_bottom + float(self.tilt[xi])


def _lines_from_rules(fits, stats, cents, w: int) -> list[_TextLine]:
    xs = np.arange(w)
    curves = sorted((np.polyval(coef, xs) for coef, _t in fits), key=lambda c: c[w // 2])
    if len(curves) < 2:
        return []
    diffs = np.diff([c[w // 2] for c in curves])
    pitch = float(np.median(diffs))
    # Ruled paper is periodic: fill rules hidden under dense writing, and extend above/below.
    filled = [curves[0]]
    for a, b in itertools.pairwise(curves):
        k = round((b[w // 2] - a[w // 2]) / pitch)
        for j in range(1, k):
            filled.append(a + (b - a) * j / k)
        filled.append(b)
    ink_top = min(cents[i][1] for i in range(1, len(stats))) if len(stats) > 1 else 0
    ink_bottom = max(cents[i][1] for i in range(1, len(stats))) if len(stats) > 1 else 0
    while filled[0][w // 2] - pitch > ink_top - pitch:
        filled.insert(0, filled[0] - pitch)
    while filled[-1][w // 2] < ink_bottom + pitch:
        filled.append(filled[-1] + pitch)
    curves = filled
    groups: dict[int, list[int]] = {}
    for i in range(1, len(stats)):
        cx, cy = cents[i]
        xi = int(min(max(cx, 0), w - 1))
        for ri, c in enumerate(curves):
            if c[xi] >= cy - 0.2 * pitch:
                groups.setdefault(ri, []).append(i)
                break
    return [_TextLine(groups[ri], curves[ri] - curves[ri][w // 2]) for ri in sorted(groups)]


def _lines_from_bands(mask, stats, cents) -> list[_TextLine]:
    bands = _line_bands(mask)
    w = mask.shape[1]
    groups: list[list[int]] = [[] for _ in bands]
    for i in range(1, len(stats)):
        cy = cents[i][1]
        for li, (a, b) in enumerate(bands):
            if a <= cy < b:
                groups[li].append(i)
                break
    return [_TextLine(g, np.zeros(w)) for g in groups if g]


def _measure_core(line: _TextLine, labels: np.ndarray, stats: np.ndarray) -> None:
    """x-height band of a line, measured on the tilt-flattened ink so sloping lines are handled."""
    x0 = int(min(stats[i, 0] for i in line.ids))
    y0 = int(min(stats[i, 1] for i in line.ids))
    x1 = int(max(stats[i, 0] + stats[i, 2] for i in line.ids))
    y1 = int(max(stats[i, 1] + stats[i, 3] for i in line.ids))
    ys, xs = np.nonzero(np.isin(labels[y0:y1, x0:x1], line.ids))
    if len(ys) == 0:
        return
    flat = ys + y0 - line.tilt[xs + x0]
    lo = flat.min()
    hist = np.bincount((flat - lo).astype(int)).astype(np.float64)
    k = max(1, len(hist) // 40)
    hist = np.convolve(hist, np.ones(k) / k, mode="same")
    core = np.nonzero(hist > 0.45 * hist.max())[0]
    line.core_top = float(lo + core.min())
    line.core_bottom = float(lo + core.max())


def analyze_freeform(img: np.ndarray, sample_id: str, transcript: str | None) -> SegmentResult:
    gray = to_gray(img)
    norm = normalize_illumination(gray)
    mask = binarize(norm)
    mask, rinfo = remove_ruling(img, mask)
    fits = rinfo.pop("fits", [])
    res = SegmentResult(info={"kind": "freeform", **rinfo})
    if len(fits) < 3:
        fits = []
        angle = estimate_skew(mask)
        res.info["deskew_deg"] = angle
        if abs(angle) >= 0.5:
            mask = (rotate_bound(mask, angle, fill=0) > 127).astype(np.uint8) * 255
    _n, labels, stats, cents = cv2.connectedComponentsWithStats(mask, connectivity=8)
    w = mask.shape[1]
    # Thin flat slivers (pieces of ruled lines left between letters) are not handwriting.
    sliver = (stats[:, cv2.CC_STAT_HEIGHT] <= max(3, mask.shape[0] // 600)) & (
        stats[:, cv2.CC_STAT_WIDTH] >= 2.5 * stats[:, cv2.CC_STAT_HEIGHT]
    )
    sliver[0] = False
    if sliver.any():
        mask = mask.copy()
        mask[sliver[labels]] = 0
        _n, labels, stats, cents = cv2.connectedComponentsWithStats(mask, connectivity=8)
    lines = _lines_from_rules(fits, stats, cents, w) if fits else []
    if not lines:
        lines = _lines_from_bands(mask, stats, cents)
    # drop near-empty lines (specks, stray marks)
    ink = [sum(int(stats[i, cv2.CC_STAT_AREA]) for i in ln.ids) for ln in lines]
    if ink:
        thr = 0.08 * float(np.median(ink))
        lines = [ln for ln, a in zip(lines, ink, strict=True) if a >= thr]
    if not lines:
        res.issues.append("fatal:no lines of handwriting were detected")
        return res
    for ln in lines:
        _measure_core(ln, labels, stats)
        ln.clusters = _clusters_for_line(labels, stats, ln.ids)

    xh_list = [ln.x_height for ln in lines if ln.x_height > 2]
    xh_px = float(np.median(xh_list)) if xh_list else 0.0
    if xh_px < 8:
        res.issues.append("fatal:handwriting is too small in this image; photograph it closer")
        return res
    for ln in lines:
        lxh = ln.x_height if ln.x_height > 2 else xh_px
        kept = []
        for c in ln.clusters:
            area = sum(int(stats[i, cv2.CC_STAT_AREA]) for i in c.labels)
            ch_ = c.y1 - c.y0
            if ch_ < 0.22 * lxh and c.w > 1.2 * lxh:
                continue  # flat remnant of a ruled line
            if area < 0.012 * lxh * lxh:
                continue  # speck
            kept.append(c)
        ln.clusters = kept
    slants, sw_list, gaps_all = [], [], []
    for ln in lines:
        for c in ln.clusters:
            sub = (np.isin(labels[c.y0 : c.y1, c.x0 : c.x1], c.labels) * 255).astype(np.uint8)
            if c.y1 - c.y0 > 1.6 * xh_px and c.w < 4 * xh_px:
                s = slant_deg(sub)
                if s is not None and abs(s) < 35:
                    slants.append(s)
            if sub.size > 50:
                sw_list.append(stroke_width_px(sub))
        gaps_all += [max(0, nxt.x0 - cur.x1) for cur, nxt in itertools.pairwise(ln.clusters)]
    mids = [ln.baseline_at(w / 2) for ln in lines]
    pitches = [b - a for a, b in itertools.pairwise(mids)]
    letter_gap = word_gap = None
    if len(gaps_all) >= 6:
        g = np.array(gaps_all, dtype=np.float32)
        small, big = g[g <= 0.45 * xh_px], g[g > 0.45 * xh_px]
        if len(small):
            letter_gap = float(np.median(small))
        if len(big):
            word_gap = float(np.median(big))
    total_clusters = sum(len(ln.clusters) for ln in lines)
    res.info["metrics"] = {
        "x_height_px": xh_px,
        "slant_deg": float(np.median(slants)) if slants else None,
        "stroke_ratio": float(np.median(sw_list)) / xh_px if sw_list else None,
        "letter_gap_ratio": letter_gap / xh_px if letter_gap is not None else None,
        "word_gap_ratio": word_gap / xh_px if word_gap is not None else None,
        "line_spacing_ratio": float(np.median(pitches)) / xh_px if pitches else None,
        "lines": len(lines),
    }

    vis = cv2.cvtColor(255 - mask, cv2.COLOR_GRAY2BGR)
    for ln in lines:
        xs_ = np.arange(0, w, 8)
        pts = np.stack([xs_, [ln.baseline_at(x) for x in xs_]], 1).astype(np.int32)
        cv2.polylines(vis, [pts], False, (220, 160, 60), 2)
    res.processed = vis

    if not (transcript and transcript.strip()):
        res.issues.append("add a transcript of this sample to extract individual letters from it")
        return res
    t_lines = [t for t in transcript.splitlines() if t.strip()]
    # More detected lines than transcribed: drop the faintest ones (stray marks, rule remnants).
    line_ink = {id(ln): sum(int(stats[i, cv2.CC_STAT_AREA]) for i in ln.ids) for ln in lines}
    med_ink = float(np.median(list(line_ink.values())))
    while len(lines) > len(t_lines):
        faint = min(lines, key=lambda ln: line_ink[id(ln)])
        if line_ink[id(faint)] >= 0.35 * med_ink:
            break
        lines.remove(faint)
    if len(t_lines) != len(lines):
        res.issues.append(
            f"transcript has {len(t_lines)} lines but {len(lines)} lines of writing were detected; "
            "make each transcript line match one written line"
        )
        return res
    expected = sum(len(t.replace(" ", "")) for t in t_lines)
    joined = total_clusters < 0.75 * expected
    res.info["joined_writing"] = joined
    aligned = sliced = rejected = 0
    skipped: list[int] = []
    for li, (text, ln) in enumerate(zip(t_lines, lines, strict=True)):
        words_txt = text.split()
        line_xh = ln.x_height if ln.x_height > 2 else xh_px
        assign = group_words([(c.x0, c.x1) for c in ln.clusters], words_txt, line_xh)
        if assign is None:
            skipped.append(li + 1)
            continue
        for word, (ga, gb) in zip(words_txt, assign, strict=True):
            clusters = ln.clusters[ga:gb]
            ids = [i for c in clusters for i in c.labels]
            x0, x1 = min(c.x0 for c in clusters), max(c.x1 for c in clusters)
            y0, y1 = min(c.y0 for c in clusters), max(c.y1 for c in clusters)
            wmask = (np.isin(labels[y0:y1, x0:x1], ids) * 255).astype(np.uint8)
            avg = (x1 - x0) / max(sum(weight(c) for c in word), 0.1)
            direct = len(clusters) == len(word) and all(
                plausible(c.x1 - c.x0, ch, avg) for ch, c in zip(word, clusters, strict=True)
            )
            if direct:
                pieces = [(c.x0 - x0, c.x1 - x0) for c in clusters]
            else:
                cuts = slice_positions((wmask > 0).sum(axis=0), word)
                if not cuts:
                    continue
                pieces = list(itertools.pairwise([0, *cuts, x1 - x0]))
                sliced += 1
            for ch, (a, b) in zip(word, pieces, strict=True):
                if not direct and not plausible(b - a, ch, avg):
                    continue
                sub = np.zeros_like(wmask)
                sub[:, a:b] = wmask[:, a:b]
                cropped = crop_to_ink(sub)
                if cropped is None or int((cropped[0] > 0).sum()) < 15:
                    continue
                bmp, oy, _ox = cropped
                base = ln.baseline_at(x0 + (a + b) / 2)
                top = (base - (y0 + oy)) / line_xh
                bottom = (base - (y0 + oy + bmp.shape[0])) / line_xh
                if not shape_plausible(ch, top, bottom, bmp.shape[1] / line_xh):
                    rejected += 1
                    continue
                g = Glyph(ch, bmp, 1.0, float(base - (y0 + oy)), "freeform" if direct else "freeform-sliced", sample_id)
                g.x_height_px = line_xh
                res.glyphs.append(g)
                aligned += 1
    if skipped:
        res.issues.append("could not match the words to the transcript on line(s) " + ", ".join(map(str, skipped)))
    if joined:
        res.issues.append(
            "joined handwriting: letters were cut apart automatically, which is approximate. "
            "Fill in the template sheet for the most faithful letter shapes (especially capitals and digits)."
        )
    res.info["aligned_glyphs"] = aligned
    res.info["sliced_words"] = sliced
    res.info["rejected_glyphs"] = rejected
    return res
