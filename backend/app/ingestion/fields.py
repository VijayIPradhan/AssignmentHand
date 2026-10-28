"""Fill-in fields printed on assignment pages, e.g. "Registration Number: ________" or
"Signature of the Student____________".

A field is a run of underscores; its label is the group of words immediately to its left on the
same line (words separated from it by a wide gap belong to a different field).
"""

import io
import re
from dataclasses import asdict, dataclass

import pdfplumber

MIN_UNDERSCORES = 4
LABEL_GAP_PT = 18.0


@dataclass
class FormField:
    page: int
    label: str
    key: str  # normalised label, used to match values across pages
    kind: str  # signature | text
    x0: float  # points from the page's left / top
    x1: float
    top: float
    bottom: float

    def as_dict(self) -> dict:
        return asdict(self)


def field_key(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", label.lower()).strip()


def _runs(chars: list[dict]) -> list[list[dict]]:
    """Consecutive underscore characters on one line."""
    runs: list[list[dict]] = []
    for c in sorted(chars, key=lambda c: (round(c["top"]), c["x0"])):
        if runs and abs(runs[-1][-1]["top"] - c["top"]) < 2 and c["x0"] - runs[-1][-1]["x1"] < 3:
            runs[-1].append(c)
        else:
            runs.append([c])
    return [r for r in runs if len(r) >= MIN_UNDERSCORES]


def detect_fields(pdf_bytes: bytes) -> list[FormField]:
    out: list[FormField] = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for pi, page in enumerate(pdf.pages):
            for run in _runs([c for c in page.chars if c["text"] == "_"]):
                x0, x1 = run[0]["x0"], run[-1]["x1"]
                top = min(c["top"] for c in run)
                bottom = max(c["bottom"] for c in run)
                # label: characters left of the run on the same line, cut at the last wide gap
                left = sorted(
                    (c for c in page.chars if c["text"] != "_" and abs(c["top"] - top) < 4 and c["x1"] <= x0 + 1),
                    key=lambda c: c["x0"],
                )
                label_chars: list[dict] = []
                for c in reversed(left):
                    nxt = label_chars[0] if label_chars else None
                    edge = nxt["x0"] if nxt else x0
                    if edge - c["x1"] > LABEL_GAP_PT:
                        break
                    label_chars.insert(0, c)
                label = "".join(c["text"] for c in label_chars).strip(" :-")
                if not label:
                    continue
                kind = "signature" if re.search(r"\bsign(ature)?\b|\bsigned\b", label, re.I) else "text"
                out.append(FormField(pi, label, field_key(label), kind, x0, x1, top, bottom))
    return out


def suggest_values(text: str, fields: list[FormField]) -> dict[str, str]:
    """Pre-fill obvious values found elsewhere on the pages (e.g. 'RegdNo. 325112996')."""
    out: dict[str, str] = {}
    keys = {f.key for f in fields if f.kind == "text"}
    reg = re.search(r"\bReg(?:d|istration)?\.?\s*(?:No|Number)\.?\s*[:\-]?\s*([A-Z0-9/-]{4,})", text, re.I)
    for k in keys:
        if ("registration" in k or k.startswith("reg")) and reg:
            out[k] = reg.group(1)
    return out
