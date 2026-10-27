"""Split joined (cursive / semi-cursive) handwriting into per-letter slices using a transcript.

This is an approximation: cuts are placed where the pen trace is thinnest near the position each
letter is expected to occupy (from typical relative letter widths). Slices keep the connecting
strokes, which helps re-joined letters look continuous when composited, but individual cuts can be
off by part of a letter. Slices that look implausible are discarded.
"""

import numpy as np

WIDTH_WEIGHTS: dict[str, float] = {
    **{c: 1.0 for c in "abcdeghknopqsuvxyz"},
    **{c: 1.5 for c in "mw"},
    **{c: 0.55 for c in "fijlrt"},
    **{c: 1.25 for c in "ABCDEFGHJKLNOPQRSTUVXYZ"},
    "M": 1.6,
    "W": 1.6,
    "I": 0.6,
    **{c: 1.0 for c in "0123456789"},
    "1": 0.6,
    **{c: 0.35 for c in ".,:;'!"},
    "-": 0.6,
    "?": 0.8,
    '"': 0.5,
    "(": 0.5,
    ")": 0.5,
}


def weight(ch: str) -> float:
    return WIDTH_WEIGHTS.get(ch, 1.0)


def slice_positions(ink_cols: np.ndarray, chars: str) -> list[int]:
    """Return cut x positions (len(chars)-1 of them) for a word whose column ink profile is given.

    Dynamic programming minimises: ink crossed at each cut + squared deviation from the expected
    boundary, with each letter at least 30% of its expected width."""
    n = len(chars)
    W = len(ink_cols)
    if n <= 1 or n * 2 > W:
        return []
    weights = np.array([weight(c) for c in chars], dtype=np.float64)
    bounds = np.cumsum(weights)[:-1] / weights.sum() * W
    avg = W / weights.sum()
    ink = ink_cols.astype(np.float64)
    ink = np.convolve(ink, np.ones(3) / 3, mode="same")
    ink_n = ink / max(ink.max(), 1.0)
    min_w = np.maximum(1, (0.3 * weights * avg).astype(int))
    INF = 1e18
    # candidates for each cut: window around the expected boundary
    cands = []
    for k, e in enumerate(bounds):
        lo = max(1, int(e - 0.65 * avg * max(weights[k], weights[k + 1])))
        hi = min(W - 1, int(e + 0.65 * avg * max(weights[k], weights[k + 1])))
        cands.append(np.arange(lo, max(lo + 1, hi + 1)))
    cost = [np.full(len(c), INF) for c in cands]
    back = [np.zeros(len(c), dtype=int) for c in cands]
    for j, x in enumerate(cands[0]):
        if x >= min_w[0]:
            cost[0][j] = 3.0 * ink_n[x] + ((x - bounds[0]) / avg) ** 2
    for k in range(1, len(cands)):
        prev_x, prev_c = cands[k - 1], cost[k - 1]
        for j, x in enumerate(cands[k]):
            ok = (x - prev_x) >= min_w[k]
            if not ok.any():
                continue
            pc = np.where(ok, prev_c, INF)
            i = int(np.argmin(pc))
            if pc[i] >= INF:
                continue
            cost[k][j] = pc[i] + 3.0 * ink_n[x] + ((x - bounds[k]) / avg) ** 2
            back[k][j] = i
    last = cost[-1]
    valid = (W - cands[-1]) >= min_w[-1]
    last = np.where(valid, last, INF)
    j = int(np.argmin(last))
    if last[j] >= INF:
        return []
    cuts = [int(cands[-1][j])]
    for k in range(len(cands) - 1, 0, -1):
        j = back[k][j]
        cuts.append(int(cands[k - 1][j]))
    return cuts[::-1]


def group_words(spans: list[tuple[int, int]], words: list[str], xh: float) -> list[tuple[int, int]] | None:
    """Assign consecutive ink clusters (x-spans, left to right) to transcript words.

    Returns [(first_index, last_index_exclusive)] per word, or None if no plausible assignment exists.
    DP minimises the deviation of each group's width from its word's expected width while rewarding
    boundaries placed at wide gaps."""
    n, m = len(spans), len(words)
    if m == 0 or n < m:
        return None
    wsum = [sum(weight(c) for c in wd) for wd in words]
    ink = sum(b - a for a, b in spans)
    unit = ink / max(sum(wsum), 0.1)
    gaps = [max(0, spans[i + 1][0] - spans[i][1]) for i in range(n - 1)]
    INF = 1e18
    # best[k][i]: cost of assigning the first i clusters to the first k words
    best = [[INF] * (n + 1) for _ in range(m + 1)]
    back = [[0] * (n + 1) for _ in range(m + 1)]
    best[0][0] = 0.0
    for k in range(1, m + 1):
        exp = wsum[k - 1] * unit
        for i in range(k, n - (m - k) + 1):
            for j in range(k - 1, i):
                if best[k - 1][j] >= INF:
                    continue
                width = spans[i - 1][1] - spans[j][0]
                # width includes internal gaps; expected width includes a typical letter gap allowance
                dev = (width - exp * 1.1) / max(exp, xh)
                c = best[k - 1][j] + dev * dev
                if i < n:
                    c -= 1.5 * min(gaps[i - 1] / xh, 2.0)  # reward a boundary at a wide gap
                    if gaps[i - 1] < 0.12 * xh:
                        c += 4.0  # boundary through touching ink: very unlikely between words
                if c < best[k][i]:
                    best[k][i] = c
                    back[k][i] = j
    if best[m][n] >= INF:
        return None
    out, i = [], n
    for k in range(m, 0, -1):
        j = back[k][i]
        out.append((j, i))
        i = j
    out.reverse()
    # sanity: no word may be wildly off its expected width
    for (a, b), ws in zip(out, wsum, strict=True):
        width = spans[b - 1][1] - spans[a][0]
        if not 0.35 * ws * unit <= width <= 2.8 * ws * unit + xh:
            return None
    return out


_XH = set("acemnorsuvwxz")
_ASC = set("bdfhklt")
_DESC = set("gjpqy")


def shape_plausible(ch: str, top: float, bottom: float, width: float) -> bool:
    """Check a candidate glyph's extent (in x-heights, relative to the baseline) against its letter class.

    ``top``/``bottom`` are heights above the baseline (bottom is negative for descenders)."""
    if width > 2.4 * weight(ch) + 0.6 or width < 0.08:
        return False
    if ch in _XH:
        return 0.5 <= top <= 1.75 and bottom >= -0.4
    if ch in _ASC:
        return top >= (1.05 if ch == "t" else 1.25) and bottom >= -0.45
    if ch in _DESC:
        return bottom <= -0.25 and top <= 1.9
    if ch in "ij":
        return 0.6 <= top <= 2.3
    if ch.isupper() or ch.isdigit():
        return top >= 1.15 and bottom >= -0.5
    if ch in ".,":
        return top <= 0.7 and width <= 0.6
    return True


def plausible(slice_w: int, ch: str, avg: float) -> bool:
    exp = weight(ch) * avg
    return 0.35 * exp <= slice_w <= 2.3 * exp
