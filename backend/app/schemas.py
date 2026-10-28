"""API request/response models."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------- auth


class ModelRoutingIn(BaseModel):
    routing: dict[str, str | None] = Field(default_factory=dict, max_length=10)


class ModelRoutingTask(BaseModel):
    task: str
    label: str
    description: str
    model: str
    default: str
    custom: bool


class ModelChoice(BaseModel):
    model: str
    input_per_mtok: float | None
    output_per_mtok: float | None


class ModelRoutingOut(BaseModel):
    tasks: list[ModelRoutingTask]
    choices: list[ModelChoice]
    fallback: str  # a lighter model that fails is retried once on this one


class Credentials(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=200)
    display_name: str | None = Field(None, max_length=120)


class UserOut(ORM):
    id: str
    email: str
    display_name: str | None
    created_at: datetime


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


# ---------------------------------------------------------------- jobs


class JobOut(ORM):
    id: str
    type: str
    status: str
    progress: float
    message: str | None
    error: str | None
    attempts: int
    max_attempts: int
    result: dict
    events: list = []
    assignment_id: str | None
    profile_id: str | None
    created_at: datetime
    finished_at: datetime | None


# ---------------------------------------------------------------- handwriting


class StyleIn(BaseModel):
    handwriting_size_mm: float | None = Field(None, ge=1.2, le=8)
    stroke_weight: float | None = Field(None, ge=0.6, le=2.0)
    slant_offset_deg: float | None = Field(None, ge=-20, le=20)
    char_spacing: float | None = Field(None, ge=0.3, le=3.0)
    word_spacing: float | None = Field(None, ge=0.4, le=3.0)
    variation: float | None = Field(None, ge=0, le=1)
    letter_style: Literal["auto", "joined", "separate"] | None = None
    ink_color: str | None = Field(None, pattern=r"^#[0-9a-fA-F]{6}$")


class ProfileCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class ProfileUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    style: StyleIn | None = None


class SampleOut(ORM):
    id: str
    kind: str
    template_page: int | None
    original_filename: str
    mime_type: str
    transcript: str | None
    corrections: dict
    status: str
    quality: dict
    has_processed: bool = False
    created_at: datetime


class SampleUpdate(BaseModel):
    transcript: str | None = Field(None, max_length=20_000)
    rotate: float | None = Field(None, ge=-360, le=360)
    crop: list[float] | None = Field(None, min_length=4, max_length=4)
    clear_crop: bool = False
    template_page: int | None = Field(None, ge=0, le=20)


class ProfileOut(ORM):
    id: str
    name: str
    status: str
    style: dict
    metrics: dict
    coverage: dict
    quality_report: dict
    library_version: int
    error: str | None
    created_at: datetime
    updated_at: datetime
    samples: list[SampleOut] = []


class PreviewIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    style: StyleIn | None = None
    paper: str = Field("ruled", pattern="^(ruled|blank|grid)$")


# ---------------------------------------------------------------- assignments


class AnswerPrefsIn(BaseModel):
    academic_level: str | None = Field(None, max_length=80)
    detail: str | None = Field(None, pattern="^(concise|moderate|detailed)$")
    language: str | None = Field(None, max_length=40)
    target_words: int | None = Field(None, ge=20, le=3000)
    technical_depth: str | None = Field(None, pattern="^(introductory|standard|advanced)$")
    citations: str | None = Field(None, pattern="^(none|if_available|required)$")
    show_working: bool | None = None
    tone: str | None = Field(None, pattern="^(formal|neutral|conversational)$")
    extra_instructions: str | None = Field(None, max_length=1000)


class AssignmentUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=255)
    subject: str | None = Field(None, max_length=120)
    instructions: str | None = Field(None, max_length=10_000)
    preferences: AnswerPrefsIn | None = None
    clear_preference_overrides: bool = False  # use the global preferences for this assignment again
    profile_id: str | None = None


class DocumentOut(ORM):
    id: str
    role: str
    original_filename: str
    page_count: int
    pages: list
    created_at: datetime


class AnswerOut(ORM):
    id: str
    question_id: str
    content: str
    status: str
    word_count: int
    flags: list
    revision: int
    history: list
    model: str | None
    input_tokens: int
    output_tokens: int
    cost_usd: float | None
    error: str | None
    approved_at: datetime | None
    updated_at: datetime


class QuestionOut(ORM):
    id: str
    position: int
    number: str
    text: str
    marks: float | None
    instructions: str | None
    context: str | None
    pages: list
    figures: list
    confidence: float
    needs_review: bool
    review_reasons: list
    has_visual: bool
    excluded: bool
    origin: str
    answer: AnswerOut | None = None


class QuestionIn(BaseModel):
    number: str = Field("", max_length=32)
    text: str = Field(min_length=1, max_length=20_000)
    marks: float | None = Field(None, ge=0, le=1000)
    instructions: str | None = Field(None, max_length=5000)
    context: str | None = Field(None, max_length=20_000)
    after_id: str | None = None


class QuestionUpdate(BaseModel):
    number: str | None = Field(None, max_length=32)
    text: str | None = Field(None, min_length=1, max_length=20_000)
    marks: float | None = Field(None, ge=0, le=1000)
    clear_marks: bool = False
    instructions: str | None = Field(None, max_length=5000)
    context: str | None = Field(None, max_length=20_000)
    excluded: bool | None = None
    reviewed: bool | None = None


class ReorderIn(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=500)


class MergeIn(BaseModel):
    ids: list[str] = Field(min_length=2, max_length=50)


class SplitIn(BaseModel):
    at: int = Field(ge=1, description="Character offset in the question text where the second part starts")
    number: str | None = Field(None, max_length=32)


class GenerateIn(BaseModel):
    question_ids: list[str] | None = Field(None, max_length=500)
    include_approved: bool = False


class AnswerUpdate(BaseModel):
    content: str = Field(max_length=60_000)
    expected_revision: int | None = None


class RenderedOut(ORM):
    id: str
    kind: str
    is_preview: bool
    page_count: int
    warnings: list
    size_bytes: int
    created_at: datetime
    downloaded_at: datetime | None


class AssignmentOut(ORM):
    id: str
    title: str
    subject: str | None
    instructions: str | None
    status: str
    preferences: dict
    profile_id: str | None
    extraction_confirmed: bool
    error: str | None
    created_at: datetime
    updated_at: datetime


class AssignmentDetail(AssignmentOut):
    documents: list[DocumentOut] = []
    questions: list[QuestionOut] = []
    rendered: list[RenderedOut] = []
    render_settings: dict[str, Any] = {}
    effective_preferences: dict[str, Any] = {}  # global preferences merged with this assignment's overrides
    active_jobs: list[JobOut] = []
    stats: dict[str, Any] = {}


class RenderIn(BaseModel):
    preview: bool = True


class ExportIn(BaseModel):
    kind: str = Field(pattern="^(digital|review)$")
