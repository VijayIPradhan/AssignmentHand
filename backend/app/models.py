"""Relational schema. All user-owned rows carry ``user_id`` so every query can be scoped."""

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def new_id() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(UTC)


class AssignmentStatus(enum.StrEnum):
    uploaded = "uploaded"
    extracting = "extracting"
    extraction_failed = "extraction_failed"
    awaiting_review = "awaiting_review"
    generating_answers = "generating_answers"
    awaiting_approval = "awaiting_approval"
    rendering = "rendering"
    render_failed = "render_failed"
    completed = "completed"


class ProfileStatus(enum.StrEnum):
    draft = "draft"
    processing = "processing"
    ready = "ready"
    needs_samples = "needs_samples"
    failed = "failed"


class SampleStatus(enum.StrEnum):
    uploaded = "uploaded"
    processed = "processed"
    rejected = "rejected"
    failed = "failed"


class AnswerStatus(enum.StrEnum):
    pending = "pending"
    generating = "generating"
    generated = "generated"
    edited = "edited"
    approved = "approved"
    failed = "failed"


class JobStatus(enum.StrEnum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(120))
    # Answer preferences shared by all of the user's assignments (an assignment may override keys)
    answer_preferences: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    # Per-task model choice ({task: model}); a missing task uses the server default (LLM_MODEL_*)
    model_routing: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class HandwritingProfile(TimestampMixin, Base):
    __tablename__ = "handwriting_profiles"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default=ProfileStatus.draft, nullable=False)
    # User-adjustable defaults (size, slant offset, spacing, variation...)
    style: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    # Measured from samples (slant, stroke width, x-height ratio, spacing...)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    # Which characters are covered and with how many variants
    coverage: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    quality_report: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    glyph_library_key: Mapped[str | None] = mapped_column(String(512))
    library_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)

    samples: Mapped[list["HandwritingSample"]] = relationship(
        back_populates="profile", cascade="all, delete-orphan", order_by="HandwritingSample.created_at"
    )

    __table_args__ = (Index("ix_profiles_user", "user_id", "created_at"),)


class HandwritingSample(TimestampMixin, Base):
    __tablename__ = "handwriting_samples"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    profile_id: Mapped[str] = mapped_column(ForeignKey("handwriting_profiles.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # template | freeform
    template_page: Mapped[int | None] = mapped_column(Integer)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    processed_key: Mapped[str | None] = mapped_column(String(512))
    transcript: Mapped[str | None] = mapped_column(Text)
    # User corrections applied before analysis: {"rotate": deg, "crop": [x0,y0,x1,y1] normalized}
    corrections: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default=SampleStatus.uploaded, nullable=False)
    quality: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    profile: Mapped[HandwritingProfile] = relationship(back_populates="samples")

    __table_args__ = (Index("ix_samples_profile", "profile_id"),)


class Assignment(TimestampMixin, Base):
    __tablename__ = "assignments"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    subject: Mapped[str | None] = mapped_column(String(120))
    # General instructions found before the first question ("Answer all questions", ...)
    instructions: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default=AssignmentStatus.uploaded, nullable=False)
    # Answer-generation preferences (level, detail, language, citations, ...)
    preferences: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    profile_id: Mapped[str | None] = mapped_column(ForeignKey("handwriting_profiles.id", ondelete="SET NULL"))
    extraction_confirmed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)

    documents: Mapped[list["SourceDocument"]] = relationship(back_populates="assignment", cascade="all, delete-orphan")
    questions: Mapped[list["ExtractedQuestion"]] = relationship(
        back_populates="assignment", cascade="all, delete-orphan", order_by="ExtractedQuestion.position"
    )
    render_config: Mapped["RenderConfiguration | None"] = relationship(
        back_populates="assignment", cascade="all, delete-orphan", uselist=False
    )
    rendered: Mapped[list["RenderedDocument"]] = relationship(
        back_populates="assignment", cascade="all, delete-orphan", order_by="RenderedDocument.created_at"
    )

    __table_args__ = (Index("ix_assignments_user", "user_id", "updated_at"),)


class SourceDocument(TimestampMixin, Base):
    __tablename__ = "source_documents"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    assignment_id: Mapped[str] = mapped_column(ForeignKey("assignments.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[str] = mapped_column(String(16), default="assignment", nullable=False)  # assignment|reference
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    page_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Per-page info: [{"index", "width", "height", "has_text", "ocr", "confidence", "image_key"}]
    pages: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    extracted_text: Mapped[str | None] = mapped_column(Text)

    assignment: Mapped[Assignment] = relationship(back_populates="documents")

    __table_args__ = (Index("ix_documents_assignment", "assignment_id"),)


class ExtractedQuestion(TimestampMixin, Base):
    __tablename__ = "extracted_questions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    assignment_id: Mapped[str] = mapped_column(ForeignKey("assignments.id", ondelete="CASCADE"), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    number: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    marks: Mapped[float | None] = mapped_column(Float)
    instructions: Mapped[str | None] = mapped_column(Text)
    # Stem text shared by sub-parts, e.g. the body of Q2 for question "2(a)"
    context: Mapped[str | None] = mapped_column(Text)
    pages: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    # Diagram/image crops from the source: [{"key", "page", "bbox", "caption"}]
    figures: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    review_reasons: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    has_visual: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    excluded: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    origin: Mapped[str] = mapped_column(String(16), default="extracted", nullable=False)  # extracted|user

    assignment: Mapped[Assignment] = relationship(back_populates="questions")
    answer: Mapped["GeneratedAnswer | None"] = relationship(
        back_populates="question", cascade="all, delete-orphan", uselist=False
    )

    __table_args__ = (Index("ix_questions_assignment", "assignment_id", "position"),)


class GeneratedAnswer(TimestampMixin, Base):
    __tablename__ = "generated_answers"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    assignment_id: Mapped[str] = mapped_column(ForeignKey("assignments.id", ondelete="CASCADE"), nullable=False)
    question_id: Mapped[str] = mapped_column(
        ForeignKey("extracted_questions.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    content: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default=AnswerStatus.pending, nullable=False)
    word_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Model-reported caveats: ambiguities, unverified claims, missing source info
    flags: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Previous versions, newest last: [{"revision", "content", "status", "at"}]
    history: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    model: Mapped[str | None] = mapped_column(String(100))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost_usd: Mapped[float | None] = mapped_column(Float)
    error: Mapped[str | None] = mapped_column(Text)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    question: Mapped[ExtractedQuestion] = relationship(back_populates="answer")

    __table_args__ = (Index("ix_answers_assignment", "assignment_id"),)


class RenderConfiguration(TimestampMixin, Base):
    __tablename__ = "render_configurations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    assignment_id: Mapped[str] = mapped_column(
        ForeignKey("assignments.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    settings: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    assignment: Mapped[Assignment] = relationship(back_populates="render_config")


class RenderedDocument(TimestampMixin, Base):
    __tablename__ = "rendered_documents"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    assignment_id: Mapped[str] = mapped_column(ForeignKey("assignments.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # handwritten | digital | review
    is_preview: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    page_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    page_image_keys: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    settings_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    warnings: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    downloaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    assignment: Mapped[Assignment] = relationship(back_populates="rendered")

    __table_args__ = (Index("ix_rendered_assignment", "assignment_id", "created_at"),)


class ProcessingJob(TimestampMixin, Base):
    __tablename__ = "processing_jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    type: Mapped[str] = mapped_column(String(48), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default=JobStatus.queued, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    result: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    progress: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    message: Mapped[str | None] = mapped_column(String(500))
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    locked_by: Mapped[str | None] = mapped_column(String(64))
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Timeline of steps, retries and errors shown to the user: [{"at", "level", "msg", ...}]
    events: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    assignment_id: Mapped[str | None] = mapped_column(ForeignKey("assignments.id", ondelete="CASCADE"), index=True)
    profile_id: Mapped[str | None] = mapped_column(
        ForeignKey("handwriting_profiles.id", ondelete="CASCADE"), index=True
    )

    __table_args__ = (
        Index("ix_jobs_claim", "status", "run_after"),
        Index("ix_jobs_user", "user_id", "created_at"),
    )


class LLMUsage(Base):
    """One AI provider call: what it was for, which model served it, tokens, latency, outcome, cost."""

    __tablename__ = "llm_usage"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    # Kept (set to NULL) when the assignment/profile is deleted, so spending history stays accurate
    assignment_id: Mapped[str | None] = mapped_column(ForeignKey("assignments.id", ondelete="SET NULL"))
    profile_id: Mapped[str | None] = mapped_column(ForeignKey("handwriting_profiles.id", ondelete="SET NULL"))
    question_id: Mapped[str | None] = mapped_column(String(32))
    task: Mapped[str] = mapped_column(String(24), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    served_model: Mapped[str | None] = mapped_column(String(120))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # ok | error
    error_type: Mapped[str | None] = mapped_column(String(64))
    cost_usd: Mapped[float | None] = mapped_column(Float)
    pricing_source: Mapped[str] = mapped_column(String(24), default="unknown", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    __table_args__ = (
        Index("ix_llm_usage_user_time", "user_id", "created_at"),
        Index("ix_llm_usage_assignment", "assignment_id"),
    )


class RateLimitHit(Base):
    """One request counted by the rate limiter. Shared by every API process and server."""

    __tablename__ = "rate_limit_hits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(200), nullable=False)  # "<bucket>:<user id, email or IP>"
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    __table_args__ = (Index("ix_rate_limit_hits_key_at", "key", "at"),)


__all__ = [
    "AnswerStatus",
    "Assignment",
    "AssignmentStatus",
    "ExtractedQuestion",
    "GeneratedAnswer",
    "HandwritingProfile",
    "HandwritingSample",
    "JobStatus",
    "LLMUsage",
    "ProcessingJob",
    "ProfileStatus",
    "RateLimitHit",
    "RenderConfiguration",
    "RenderedDocument",
    "SampleStatus",
    "SourceDocument",
    "User",
]
