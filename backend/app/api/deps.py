"""Shared API helpers: ownership-checked lookups and safe upload reading."""

import hashlib

from fastapi import UploadFile
from sqlalchemy.orm import Session

from app.config import get_settings
from app.errors import NotFound, PayloadTooLarge, UnsupportedMedia
from app.models import (
    Assignment,
    ExtractedQuestion,
    GeneratedAnswer,
    HandwritingProfile,
    ProcessingJob,
    RenderedDocument,
    User,
)

MAGIC = {
    "application/pdf": (b"%PDF-",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
}
EXT = {"application/pdf": "pdf", "image/png": "png", "image/jpeg": "jpg"}


def owned(db: Session, model, obj_id: str, user: User):
    """Fetch a row owned by ``user``; other users' rows are indistinguishable from missing ones."""
    obj = db.get(model, obj_id)
    if obj is None or obj.user_id != user.id:
        raise NotFound(f"{model.__name__} not found")
    return obj


def owned_assignment(db: Session, aid: str, user: User) -> Assignment:
    return owned(db, Assignment, aid, user)


def owned_profile(db: Session, pid: str, user: User) -> HandwritingProfile:
    return owned(db, HandwritingProfile, pid, user)


def owned_question(db: Session, qid: str, user: User) -> ExtractedQuestion:
    return owned(db, ExtractedQuestion, qid, user)


def owned_answer(db: Session, aid: str, user: User) -> GeneratedAnswer:
    return owned(db, GeneratedAnswer, aid, user)


def owned_job(db: Session, jid: str, user: User) -> ProcessingJob:
    return owned(db, ProcessingJob, jid, user)


def owned_rendered(db: Session, rid: str, user: User) -> RenderedDocument:
    return owned(db, RenderedDocument, rid, user)


def read_upload(file: UploadFile, allowed: set[str]) -> tuple[bytes, str, str]:
    """Read an upload with a size cap and detect its type from content (never trust the client).

    Upload routes are plain (sync) functions: FastAPI runs them in its thread pool, so decoding images,
    parsing PDFs and writing to object storage never block the event loop. The body has already been
    received into a spooled temporary file, read here in chunks. Returns (data, mime, sha256)."""
    limit = get_settings().max_upload_bytes
    chunks, size = [], 0
    while True:
        chunk = file.file.read(1024 * 1024)
        if not chunk:
            break
        size += len(chunk)
        if size > limit:
            raise PayloadTooLarge(f"File exceeds the {limit // (1024 * 1024)} MB limit")
        chunks.append(chunk)
    data = b"".join(chunks)
    if not data:
        raise UnsupportedMedia("The file is empty")
    mime = next((m for m, sigs in MAGIC.items() if any(data.startswith(s) for s in sigs)), None)
    if mime is None or mime not in allowed:
        raise UnsupportedMedia("Unsupported file type; allowed: " + ", ".join(sorted(EXT[m].upper() for m in allowed)))
    return data, mime, hashlib.sha256(data).hexdigest()
