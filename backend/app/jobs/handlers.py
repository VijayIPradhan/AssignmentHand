"""Job handlers. Each receives (db, job, progress) and returns a JSON-serialisable result.

``on_failure`` hooks put the owning entity into a recoverable state; approved answers and user
edits are never discarded by a failed job.
"""

from collections.abc import Callable

from sqlalchemy.orm import Session

from app.models import (
    AnswerStatus,
    Assignment,
    AssignmentStatus,
    HandwritingProfile,
    ProcessingJob,
    ProfileStatus,
)
from app.services import assignments as svc
from app.services import profiles as psvc

Progress = Callable[[float, str], None]


def _assignment(db: Session, job: ProcessingJob) -> Assignment:
    a = db.get(Assignment, job.assignment_id)
    if a is None or a.user_id != job.user_id:
        raise svc.PipelineError("The assignment no longer exists")
    return a


def process_profile(db: Session, job: ProcessingJob, progress: Progress) -> dict:
    p = db.get(HandwritingProfile, job.profile_id)
    if p is None or p.user_id != job.user_id:
        raise svc.PipelineError("The handwriting profile no longer exists")
    p.status = ProfileStatus.processing
    db.commit()
    return psvc.process_profile(db, p, progress)


def process_profile_failed(db: Session, job: ProcessingJob, message: str) -> None:
    p = db.get(HandwritingProfile, job.profile_id)
    if p is not None:
        p.status = ProfileStatus.failed
        p.error = message
        db.commit()


def extract(db: Session, job: ProcessingJob, progress: Progress) -> dict:
    a = _assignment(db, job)
    a.status = AssignmentStatus.extracting
    db.commit()
    return svc.run_extraction(db, a, progress)


def extract_failed(db: Session, job: ProcessingJob, message: str) -> None:
    a = db.get(Assignment, job.assignment_id)
    if a is not None:
        a.status = AssignmentStatus.extraction_failed
        a.error = message
        db.commit()


def generate(db: Session, job: ProcessingJob, progress: Progress) -> dict:
    a = _assignment(db, job)
    return svc.run_generation(
        db,
        a,
        job.payload.get("question_ids"),
        bool(job.payload.get("include_approved")),
        progress,
        only_failed=job.attempts > 1,
    )


def generate_failed(db: Session, job: ProcessingJob, message: str) -> None:
    a = db.get(Assignment, job.assignment_id)
    if a is None:
        return
    for q in a.questions:
        if q.answer is not None and q.answer.status == AnswerStatus.generating:
            q.answer.status = AnswerStatus.failed if not q.answer.content else AnswerStatus.generated
            q.answer.error = message
    a.status = (
        AssignmentStatus.awaiting_approval if any(q.answer for q in a.questions) else AssignmentStatus.awaiting_review
    )
    a.error = message
    db.commit()


def render(db: Session, job: ProcessingJob, progress: Progress) -> dict:
    a = _assignment(db, job)
    return svc.run_render(db, a, bool(job.payload.get("preview", True)), progress)


def render_failed(db: Session, job: ProcessingJob, message: str) -> None:
    a = db.get(Assignment, job.assignment_id)
    if a is not None and not job.payload.get("preview", True):
        a.status = AssignmentStatus.render_failed
        a.error = message
        db.commit()


def export(db: Session, job: ProcessingJob, progress: Progress) -> dict:
    a = _assignment(db, job)
    progress(0.3, "Building document")
    rd = svc.export_digital(db, a, job.payload.get("kind", "digital"))
    return {"rendered_id": rd.id}


HANDLERS: dict[str, tuple[Callable, Callable | None]] = {
    "process_profile": (process_profile, process_profile_failed),
    "extract": (extract, extract_failed),
    "generate": (generate, generate_failed),
    "render": (render, render_failed),
    "export": (export, None),
}
