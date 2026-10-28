from typing import Literal

from pydantic import BaseModel, Field, field_validator

PAGE_SIZES_MM = {"A4": (210.0, 297.0), "Letter": (215.9, 279.4)}


class Margins(BaseModel):
    top: float = Field(22.0, ge=5, le=80)
    bottom: float = Field(18.0, ge=5, le=80)
    left: float = Field(25.0, ge=5, le=80)
    right: float = Field(15.0, ge=5, le=80)


class RenderSettings(BaseModel):
    page_size: Literal["A4", "Letter"] = "A4"
    orientation: Literal["portrait", "landscape"] = "portrait"
    # "source" writes answers onto the original assignment pages, below each question
    paper: Literal["ruled", "blank", "grid", "source"] = "ruled"
    paper_color: str = "#fffdf8"
    rule_color: str = "#9fbbe0"
    margin_line: bool = True
    margins: Margins = Field(default_factory=Margins)
    line_spacing_mm: float = Field(8.0, ge=4, le=20)
    handwriting_size_mm: float = Field(2.8, ge=1.2, le=8, description="Target x-height")
    ink_color: str = "#1b2a6b"
    stroke_weight: float = Field(1.0, ge=0.6, le=2.0)
    slant_offset_deg: float = Field(0.0, ge=-20, le=20)
    char_spacing: float = Field(1.0, ge=0.3, le=3.0)
    word_spacing: float = Field(1.0, ge=0.4, le=3.0)
    variation: float = Field(0.5, ge=0, le=1)
    # "joined" connects the letters of each word with joining strokes (cursive), "separate" writes them
    # apart. "auto": the profile's choice (an assignment), else joined if the samples were written joined.
    letter_style: Literal["auto", "joined", "separate"] = "auto"
    paragraph_indent_mm: float = Field(6.0, ge=0, le=40)
    question_spacing_lines: int = Field(1, ge=0, le=6)
    include_question_text: bool = True
    underline_question_numbers: bool = True
    show_marks: bool = False
    page_numbers: bool = True
    header_text: str = Field("", max_length=200)
    footer_text: str = Field("", max_length=200)
    seed: int = Field(1234, ge=0, le=2**31 - 1)
    # Writing on source pages
    fit_to_space: bool = True
    min_fit_scale: float = Field(0.7, ge=0.4, le=1.0)
    answer_prefix: str = Field("", max_length=20)
    extra_page_paper: Literal["ruled", "blank", "grid"] = "ruled"
    # Printed form fields on the source pages ("Registration Number: ____"): label -> value written in
    # handwriting. Keys are normalised labels as returned by GET /assignments/{id}/fields.
    field_values: dict[str, str] = Field(default_factory=dict, max_length=30)
    # Place the user's own uploaded signature on signature lines. None = automatic: sign when the user
    # has uploaded a signature (resolved in services.assignments.effective_settings).
    sign_fields: bool | None = None
    signature_color: str = "#1f3a93"
    signature_height_mm: float = Field(11.0, ge=4, le=30)

    @field_validator("field_values")
    @classmethod
    def _values(cls, v: dict[str, str]) -> dict[str, str]:
        return {k.strip().lower()[:80]: str(val)[:80] for k, val in v.items() if k.strip()}

    @field_validator("paper_color", "rule_color", "ink_color", "signature_color")
    @classmethod
    def _hex(cls, v: str) -> str:
        v = v.strip()
        if len(v) != 7 or not v.startswith("#") or any(c not in "0123456789abcdefABCDEF" for c in v[1:]):
            raise ValueError("expected a colour like #1b2a6b")
        return v.lower()

    def page_mm(self) -> tuple[float, float]:
        w, h = PAGE_SIZES_MM[self.page_size]
        return (h, w) if self.orientation == "landscape" else (w, h)
