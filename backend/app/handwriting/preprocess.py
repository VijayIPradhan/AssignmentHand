"""Image loading, user corrections, quality assessment and ink extraction for handwriting samples."""

import io
from dataclasses import dataclass, field

import cv2
import numpy as np
from PIL import Image, ImageOps

MAX_SIDE_PX = 5000
MIN_SIDE_PX = 600

Image.MAX_IMAGE_PIXELS = 60_000_000  # guard against decompression bombs


class SampleImageError(ValueError):
    """The uploaded file could not be used as a handwriting sample."""


@dataclass
class Quality:
    width: int
    height: int
    sharpness: float
    contrast: float
    ink_ratio: float
    issues: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(i.startswith("fatal:") for i in self.issues)

    def as_dict(self) -> dict:
        return {
            "width": self.width,
            "height": self.height,
            "sharpness": round(self.sharpness, 1),
            "contrast": round(self.contrast, 1),
            "ink_ratio": round(self.ink_ratio, 4),
            "issues": self.issues,
            "ok": self.ok,
        }


def load_image(data: bytes, mime: str) -> np.ndarray:
    """Decode a JPG/PNG (honouring EXIF orientation) or the first page of a PDF scan to BGR."""
    if mime == "application/pdf":
        import pypdfium2 as pdfium

        try:
            pdf = pdfium.PdfDocument(data)
        except pdfium.PdfiumError as exc:
            raise SampleImageError("The PDF could not be opened") from exc
        if len(pdf) == 0:
            raise SampleImageError("The PDF has no pages")
        page = pdf[0]
        scale = min(300 / 72, MAX_SIDE_PX / max(page.get_size()))
        pil = page.render(scale=scale).to_pil().convert("RGB")
        pdf.close()
    else:
        try:
            pil = Image.open(io.BytesIO(data))
            pil.load()
        except (OSError, Image.DecompressionBombError) as exc:
            raise SampleImageError("The image could not be decoded") from exc
        pil = ImageOps.exif_transpose(pil).convert("RGB")
    img = cv2.cvtColor(np.asarray(pil), cv2.COLOR_RGB2BGR)
    h, w = img.shape[:2]
    if max(h, w) > MAX_SIDE_PX:
        s = MAX_SIDE_PX / max(h, w)
        img = cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
    return img


def apply_corrections(img: np.ndarray, corrections: dict | None) -> np.ndarray:
    """Apply user-specified crop (normalized [x0,y0,x1,y1]) then rotation (degrees, clockwise)."""
    if not corrections:
        return img
    crop = corrections.get("crop")
    if crop and len(crop) == 4:
        h, w = img.shape[:2]
        x0, y0, x1, y1 = (float(v) for v in crop)
        x0, x1 = sorted((max(0.0, min(1.0, x0)), max(0.0, min(1.0, x1))))
        y0, y1 = sorted((max(0.0, min(1.0, y0)), max(0.0, min(1.0, y1))))
        if x1 - x0 > 0.05 and y1 - y0 > 0.05:
            img = img[int(y0 * h) : int(y1 * h), int(x0 * w) : int(x1 * w)]
    rot = float(corrections.get("rotate", 0) or 0) % 360
    if rot:
        img = rotate_bound(img, -rot)
    return img


def rotate_bound(img: np.ndarray, angle_ccw: float, fill: int = 255) -> np.ndarray:
    """Rotate without cropping. ``fill`` is the border value (255 = paper; use 0 for ink masks)."""
    if angle_ccw % 360 == 0:
        return img
    for k, a in ((1, 90), (2, 180), (3, 270)):
        if abs(angle_ccw % 360 - a) < 1e-6:
            return np.ascontiguousarray(np.rot90(img, k))
    h, w = img.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle_ccw, 1.0)
    cos, sin = abs(m[0, 0]), abs(m[0, 1])
    nw, nh = int(h * sin + w * cos), int(h * cos + w * sin)
    m[0, 2] += nw / 2 - w / 2
    m[1, 2] += nh / 2 - h / 2
    border = (fill, fill, fill) if img.ndim == 3 else fill
    return cv2.warpAffine(img, m, (nw, nh), flags=cv2.INTER_LINEAR, borderValue=border)


def to_gray(img: np.ndarray) -> np.ndarray:
    if img.ndim == 2:
        return img
    # Use the minimum channel: coloured inks (blue/black) stay dark, pale coloured paper turns light.
    return img.min(axis=2)


def normalize_illumination(gray: np.ndarray) -> np.ndarray:
    """Divide by a blurred background estimate to flatten shadows and paper texture."""
    h, w = gray.shape
    k = max(15, (min(h, w) // 25) | 1)
    bg = cv2.morphologyEx(gray, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    bg = cv2.GaussianBlur(bg, (0, 0), k / 3)
    norm = cv2.divide(gray.astype(np.float32), np.maximum(bg.astype(np.float32), 1.0), scale=255.0)
    return np.clip(norm, 0, 255).astype(np.uint8)


def binarize(gray_norm: np.ndarray, hysteresis: float = 0.0) -> np.ndarray:
    """Return an ink mask (uint8 0/255, ink=255). Expects an illumination-normalized image."""
    blur = cv2.GaussianBlur(gray_norm, (3, 3), 0)
    otsu, _ = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    # On a nearly blank page Otsu can pick a threshold inside the paper noise; clamp it.
    thr = min(otsu, 200)
    strong = blur < thr
    # Hysteresis: faint pixels (light pen pressure, slight blur) count as ink only when they connect
    # to clearly dark ink, so faded stems are kept without picking up paper texture.
    paper = float(np.median(blur))
    weak_thr = thr + (paper - thr) * hysteresis
    weak = (blur < weak_thr).astype(np.uint8)
    n, labels = cv2.connectedComponents(weak, connectivity=8)
    has_strong = np.zeros(n, dtype=bool)
    has_strong[np.unique(labels[strong])] = True
    has_strong[0] = False
    mask = (has_strong[labels] * 255).astype(np.uint8)
    return remove_specks(mask, min_area=max(4, gray_norm.size // 2_000_000))


def remove_specks(mask: np.ndarray, min_area: int) -> np.ndarray:
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    keep = np.zeros(n, dtype=bool)
    keep[1:] = stats[1:, cv2.CC_STAT_AREA] >= min_area
    return (keep[labels] * 255).astype(np.uint8)


def _rule_rows(mask: np.ndarray) -> list[tuple[np.ndarray, int]]:
    """Find printed horizontal rules. Returns [(polynomial y(x) coefficients, thickness px)].

    Long flat segments are projected along the page's dominant tilt; rules show up as peaks in that
    histogram. Pixels are assigned to their nearest peak and fitted with a quadratic (phone photos bend
    lines slightly), rejecting outliers such as handwritten underlines."""
    h, w = mask.shape
    seg = cv2.morphologyEx(mask, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (max(40, w // 40), 1)))
    ys, xs = np.nonzero(seg)
    if len(xs) < 0.2 * w:
        return []
    if len(xs) > 200_000:
        k = np.random.default_rng(0).choice(len(xs), 200_000, replace=False)
        ys, xs = ys[k], xs[k]
    best_k, best_score = 0.0, -1.0
    for deg in np.arange(-4.0, 4.01, 0.1):
        k = np.tan(np.radians(deg))
        hist = np.bincount(((ys - k * xs) - (ys - k * xs).min()).astype(int) // 2)
        score = float((hist.astype(np.float64) ** 2).sum())
        if score > best_score:
            best_k, best_score = k, score
    yp = ys - best_k * xs
    off = yp.min()
    hist = np.bincount((yp - off).astype(int), minlength=1).astype(np.float64)
    smooth = np.convolve(hist, np.ones(7), mode="same")
    min_gap = max(12, h // 200)
    order = np.argsort(-smooth)
    peaks: list[int] = []
    for idx in order:
        if smooth[idx] < 0.05 * w:
            break
        if all(abs(idx - p) >= min_gap for p in peaks):
            peaks.append(int(idx))
    rules = []
    for pk in sorted(peaks):
        sel = np.abs(yp - off - pk) <= 5
        px, py = xs[sel], ys[sel]
        if len(np.unique(px // 4)) * 4 < 0.2 * w:  # must cover at least 20% of the width
            continue
        deg = 2 if np.ptp(px) > 0.5 * w else 1
        coef = np.polyfit(px, py, deg)
        for _ in range(3):
            res = np.abs(np.polyval(coef, px) - py)
            keep = res <= 3.0
            if keep.all() or keep.sum() < 50:
                break
            px, py = px[keep], py[keep]
            coef = np.polyfit(px, py, deg)
        cols = seg
        # thickness: median vertical run of segment pixels at sampled columns along the fit
        sample = np.linspace(px.min(), px.max(), 40).astype(int)
        runs = []
        for x in sample:
            y = round(np.polyval(coef, x))
            if 0 <= y < h and cols[y, x]:
                a = y
                while a > 0 and cols[a - 1, x]:
                    a -= 1
                b = y
                while b < h - 1 and cols[b + 1, x]:
                    b += 1
                runs.append(b - a + 1)
        thick = int(np.median(runs)) if runs else 3
        rules.append((coef, max(1, min(thick, max(6, h // 250)))))
    return rules


def remove_ruling(img: np.ndarray, mask: np.ndarray) -> tuple[np.ndarray, dict]:
    """Remove printed ruled/margin lines and notebook-edge marks from an ink mask.

    Horizontal rules use staff-line removal: along each fitted rule, a column's ink run through the
    rule is erased only if it is no thicker than the rule itself, so pen strokes crossing the line are
    kept. Colour is not relied on (JPEG chroma bleeding makes it unreliable next to strokes)."""
    h, w = mask.shape
    mask = mask.copy()
    rules = _rule_rows(mask)
    if len(rules) < 3:
        rules = []  # ruled paper has many evenly spaced rules; one or two "rules" are aligned letter bars
    info: dict = {"rules_removed": bool(rules), "rules": len(rules), "fits": rules}
    band = np.zeros(mask.shape, dtype=bool)
    xs = np.arange(w)
    for coef, thick in rules:
        yc = np.round(np.polyval(coef, xs)).astype(int)
        limit = int(thick * 1.6) + 2
        reach = thick + 3
        for x in range(w):
            y = yc[x]
            if not 0 <= y < h:
                continue
            col = mask[:, x]
            # snap to the nearest ink pixel within the rule's thickness
            hit = next((y + d for d in (0, 1, -1, 2, -2, 3, -3) if 0 <= y + d < h and col[y + d]), None)
            if hit is None:
                continue
            top = hit
            while top > 0 and col[top - 1] and hit - top < reach + limit:
                top -= 1
            bot = hit
            while bot < h - 1 and col[bot + 1] and bot - hit < reach + limit:
                bot += 1
            if bot - top + 1 <= limit:
                col[top : bot + 1] = 0
        for dy in range(-(thick // 2 + 3), thick // 2 + 4):
            band[np.clip(yc + dy, 0, h - 1), xs] = True
    # Margin lines run most of the page height; a tall letter stroke in a close-up photo does not.
    vert = cv2.morphologyEx(mask, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(40, h // 3))))
    if vert.any():
        mask[vert > 0] = 0
        info["vertical_lines_removed"] = True
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    edge = int(0.03 * w)
    min_rule_len = max(80, int(0.03 * w))
    thin = max(8, h // 300) if rules else 0
    drop_ids = []
    for i in range(1, n):
        x, y, cw, ch, _area = (int(v) for v in stats[i])
        # Notebook binding/edge marks confined to the outer 3% of the page width
        if (
            x + cw <= edge
            or x >= w - edge
            or (cw >= min_rule_len and cw > 10 * ch and (band[y : y + ch, x : x + cw].mean() > 0.3 or ch <= thin))
            # page edges, folds and table borders: very long and thin, never handwriting
            or (cw >= 0.35 * w and ch <= 0.03 * h)
        ):
            drop_ids.append(i)
    if drop_ids:
        mask[np.isin(labels, drop_ids)] = 0
    return remove_specks(mask, min_area=max(6, mask.size // 1_000_000)), info


def assess_quality(img: np.ndarray, gray_norm: np.ndarray, mask: np.ndarray) -> Quality:
    h, w = img.shape[:2]
    gray = to_gray(img)
    sharp = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    ink = mask > 0
    ink_ratio = float(ink.mean())
    if ink.any() and (~ink).any():
        contrast = float(gray_norm[~ink].mean() - gray_norm[ink].mean())
    else:
        contrast = 0.0
    q = Quality(w, h, sharp, contrast, ink_ratio)
    if min(h, w) < MIN_SIDE_PX:
        q.issues.append(f"fatal:resolution too low ({w}x{h}); scan at 200 dpi or higher")
    elif min(h, w) < 1200:
        q.issues.append("low resolution; letter shapes may lose detail")
    if ink_ratio < 0.0015:
        q.issues.append("fatal:almost no handwriting detected")
    elif ink_ratio > 0.35:
        q.issues.append("fatal:image is mostly dark; check lighting or threshold")
    if sharp < 40:
        q.issues.append("image looks blurry; hold the camera steady or use a scanner")
    if contrast and contrast < 60:
        q.issues.append("low ink contrast; use a darker pen or better lighting")
    return q


def estimate_skew(mask: np.ndarray) -> float:
    """Return the counter-clockwise rotation (degrees) that best levels the text lines.

    Pass the result to ``rotate_bound`` to deskew. Uses projection-profile variance."""
    small = mask
    s = 1000 / max(mask.shape)
    if s < 1:
        small = cv2.resize(mask, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    best, best_score = 0.0, -1.0
    for angle in np.arange(-10, 10.01, 0.5):
        rot = rotate_bound(small, angle, fill=0)
        profile = rot.sum(axis=1, dtype=np.float64)
        score = float(np.var(profile))
        if score > best_score:
            best, best_score = float(angle), score
    return best


def encode_png(img: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", img)
    if not ok:  # pragma: no cover
        raise RuntimeError("png encode failed")
    return buf.tobytes()
