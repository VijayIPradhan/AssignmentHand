"""OCR providers for scanned pages. Selection is configurable; when none is available the page is
flagged so the user can type the questions in the review screen."""

import io
import logging
import shutil
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from app.config import get_settings

log = logging.getLogger(__name__)


@dataclass
class OcrLine:
    text: str
    y0: float  # fraction of page height
    y1: float
    x0: float  # fraction of page width
    confidence: float


@dataclass
class OcrResult:
    lines: list[OcrLine] = field(default_factory=list)
    engine: str = ""

    @property
    def confidence(self) -> float:
        if not self.lines:
            return 0.0
        total = sum(len(ln.text) for ln in self.lines) or 1
        return sum(ln.confidence * len(ln.text) for ln in self.lines) / total


class OcrError(RuntimeError):
    pass


class OcrProvider(ABC):
    name = "abstract"

    @abstractmethod
    def ocr(self, png: bytes) -> OcrResult: ...


class TesseractOcr(OcrProvider):
    name = "tesseract"

    def __init__(self, cmd: str | None = None):
        import pytesseract

        if cmd:
            pytesseract.pytesseract.tesseract_cmd = cmd
        self._pt = pytesseract

    @staticmethod
    def available(cmd: str | None = None) -> bool:
        return bool(cmd and shutil.which(cmd)) or bool(shutil.which("tesseract"))

    def ocr(self, png: bytes) -> OcrResult:
        from PIL import Image

        try:
            img = Image.open(io.BytesIO(png))
            data = self._pt.image_to_data(img, output_type=self._pt.Output.DICT, timeout=120)
        except Exception as exc:  # pytesseract raises several types (TesseractError, RuntimeError)
            raise OcrError(f"Tesseract failed: {type(exc).__name__}") from exc
        W, H = img.size
        groups: dict[tuple, list[int]] = {}
        for i, word in enumerate(data["text"]):
            if word.strip():
                key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
                groups.setdefault(key, []).append(i)
        lines = []
        for idxs in groups.values():
            words = [data["text"][i] for i in idxs]
            confs = [float(data["conf"][i]) for i in idxs if float(data["conf"][i]) >= 0]
            top = min(data["top"][i] for i in idxs)
            bottom = max(data["top"][i] + data["height"][i] for i in idxs)
            left = min(data["left"][i] for i in idxs)
            lines.append(
                OcrLine(
                    " ".join(words), top / H, bottom / H, left / W, (sum(confs) / len(confs) / 100) if confs else 0.5
                )
            )
        lines.sort(key=lambda ln: (round(ln.y0, 3), ln.x0))
        return OcrResult(lines, self.name)


class LlmOcr(OcrProvider):
    """Uses a vision-capable LLM to transcribe the page. Positions are approximate (line order only)."""

    name = "llm"

    def __init__(self, provider):
        self.provider = provider

    def ocr(self, png: bytes) -> OcrResult:
        text = self.provider.transcribe_image(png)
        rows = [r for r in text.splitlines() if r.strip()]
        n = max(len(rows), 1)
        lines = [OcrLine(r.strip(), i / n, (i + 0.8) / n, 0.05, 0.7) for i, r in enumerate(rows)]
        return OcrResult(lines, self.name)


def get_ocr(routing: dict | None = None) -> OcrProvider | None:
    s = get_settings()
    mode = s.ocr_provider
    if mode in ("auto", "tesseract") and TesseractOcr.available(s.tesseract_cmd):
        return TesseractOcr(s.tesseract_cmd)
    if mode in ("auto", "llm"):
        from app.ai.providers import get_provider, provider_configured

        if provider_configured():
            return LlmOcr(get_provider("transcribe", routing))
    if mode == "tesseract":
        log.warning("OCR_PROVIDER=tesseract but the tesseract binary was not found")
    return None
