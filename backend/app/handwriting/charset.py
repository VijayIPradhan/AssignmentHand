"""Characters collected by the template sheet and how many variants of each are requested."""

LOWER = "abcdefghijklmnopqrstuvwxyz"
UPPER = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
DIGITS = "0123456789"
PUNCT = ".,;:!?'\"()-/[]&%@#*_+="
MATH = "×÷±≠≤≥≈√πθαβγλμσΔΣ∫∞°→<>^"

# (characters, variants requested)
TEMPLATE_GROUPS: list[tuple[str, int]] = [
    (LOWER, 3),
    (UPPER, 2),
    (DIGITS, 2),
    (PUNCT, 1),
    (MATH, 1),
]

# Characters that sit below the baseline / are short; used for baseline + x-height estimation
DESCENDERS = set("gjpqy,;")
X_HEIGHT_CHARS = set("acemnorsuvwxz")
ASCENDER_CHARS = set("bdfhklt")


def template_cells() -> list[str]:
    cells: list[str] = []
    for chars, variants in TEMPLATE_GROUPS:
        for ch in chars:
            cells.extend([ch] * variants)
    return cells


# Typographic characters that can be safely mapped to something the user wrote.
SUBSTITUTIONS: dict[str, str] = {
    "‘": "'",
    "’": "'",
    "‚": ",",
    "“": '"',
    "”": '"',
    "–": "-",
    "—": "-",
    "−": "-",
    "‐": "-",
    "\u00a0": " ",
    "\u2009": " ",
    "•": "-",
    "∗": "*",
}
