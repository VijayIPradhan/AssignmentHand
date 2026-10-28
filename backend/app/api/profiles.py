import json

import cv2
import numpy as np
from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import EXT, owned, owned_profile, read_upload
from app.config import get_settings
from app.db import get_db
from app.errors import AppError, ConfigurationError, Conflict, NotFound
from app.handwriting.preprocess import SampleImageError, apply_corrections, load_image
from app.handwriting.template import build_template_pdf
from app.jobs.queue import enqueue
from app.models import HandwritingProfile, HandwritingSample, ProfileStatus, User
from app.ratelimit import check_rate
from app.schemas import JobOut, PreviewIn, ProfileCreate, ProfileOut, ProfileUpdate, SampleOut, SampleUpdate
from app.security import current_user
from app.services import profiles as svc
from app.services.retention import delete_profile
from app.storage import get_storage, safe_filename

router = APIRouter(prefix="/profiles", tags=["handwriting profiles"])

SAMPLE_TYPES = {"image/png", "image/jpeg", "application/pdf"}


def _out(p: HandwritingProfile) -> ProfileOut:
    out = ProfileOut.model_validate(p)
    out.samples = [
        SampleOut.model_validate(s).model_copy(update={"has_processed": bool(s.processed_key)}) for s in p.samples
    ]
    return out


@router.get("/template.pdf", response_class=Response)
def template_pdf(_user: User = Depends(current_user)):
    """Printable sheet to fill in by hand (one character per box)."""
    return Response(
        build_template_pdf(),
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="handwriting-sample-sheet.pdf"'},
    )


@router.get("", response_model=list[ProfileOut])
def list_profiles(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(HandwritingProfile)
        .where(HandwritingProfile.user_id == user.id)
        .order_by(HandwritingProfile.created_at.desc())
    ).all()
    return [_out(p) for p in rows]


@router.post("", response_model=ProfileOut, status_code=201)
def create_profile(body: ProfileCreate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = HandwritingProfile(user_id=user.id, name=body.name.strip(), status=ProfileStatus.draft, style={})
    db.add(p)
    db.commit()
    return _out(p)


@router.get("/{pid}", response_model=ProfileOut)
def get_profile(pid: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return _out(owned_profile(db, pid, user))


@router.patch("/{pid}", response_model=ProfileOut)
def update_profile(pid: str, body: ProfileUpdate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = owned_profile(db, pid, user)
    if body.name is not None:
        p.name = body.name.strip()
    if body.style is not None:
        p.style = {**(p.style or {}), **body.style.model_dump(exclude_none=True)}
    db.commit()
    return _out(p)


@router.delete("/{pid}", status_code=204)
def remove_profile(pid: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Delete the profile, its source samples and all derived glyph data."""
    delete_profile(db, owned_profile(db, pid, user))


@router.post("/{pid}/samples", response_model=SampleOut, status_code=201)
def upload_sample(
    pid: str,
    file: UploadFile = File(...),
    kind: str = Form("template", pattern="^(template|freeform)$"),
    transcript: str | None = Form(None, max_length=20_000),
    template_page: int | None = Form(None, ge=0, le=20),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    p = owned_profile(db, pid, user)
    if len(p.samples) >= get_settings().max_sample_files_per_profile:
        raise Conflict("This profile already has the maximum number of samples")
    data, mime, _sha = read_upload(file, SAMPLE_TYPES)
    try:
        load_image(data, mime)  # validate it decodes before storing
    except SampleImageError as exc:
        raise AppError(str(exc), code="invalid_image", status_code=422) from exc
    s = HandwritingSample(
        user_id=user.id,
        profile_id=p.id,
        kind=kind,
        template_page=template_page,
        original_filename=safe_filename(file.filename or "sample"),
        mime_type=mime,
        storage_key="",
        transcript=transcript,
    )
    db.add(s)
    db.flush()
    s.storage_key = f"{svc.profile_prefix(p)}/samples/{s.id}/original.{EXT[mime]}"
    get_storage().put(s.storage_key, data)
    p.status = ProfileStatus.draft
    db.commit()
    return SampleOut.model_validate(s)


@router.patch("/{pid}/samples/{sid}", response_model=SampleOut)
def update_sample(
    pid: str, sid: str, body: SampleUpdate, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    p = owned_profile(db, pid, user)
    s = owned(db, HandwritingSample, sid, user)
    if s.profile_id != p.id:
        raise NotFound("HandwritingSample not found")
    if body.transcript is not None:
        s.transcript = body.transcript
    corr = dict(s.corrections or {})
    if body.rotate is not None:
        corr["rotate"] = body.rotate % 360
    if body.crop is not None:
        corr["crop"] = body.crop
    if body.clear_crop:
        corr.pop("crop", None)
    s.corrections = corr
    if body.template_page is not None:
        s.template_page = body.template_page
    p.status = ProfileStatus.draft
    db.commit()
    return SampleOut.model_validate(s).model_copy(update={"has_processed": bool(s.processed_key)})


@router.delete("/{pid}/samples/{sid}", status_code=204)
def delete_sample(pid: str, sid: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = owned_profile(db, pid, user)
    s = owned(db, HandwritingSample, sid, user)
    if s.profile_id != p.id:
        raise NotFound("HandwritingSample not found")
    get_storage().delete_prefix(f"{svc.profile_prefix(p)}/samples/{s.id}")
    db.delete(s)
    p.status = ProfileStatus.draft
    db.commit()


@router.get("/{pid}/samples/{sid}/image", response_class=Response)
def sample_image(
    pid: str,
    sid: str,
    variant: str = Query("original", pattern="^(original|corrected|processed)$"),
    max_side: int = Query(1600, ge=200, le=4000),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    p = owned_profile(db, pid, user)
    s = owned(db, HandwritingSample, sid, user)
    if s.profile_id != p.id:
        raise NotFound("HandwritingSample not found")
    storage = get_storage()
    if variant == "processed":
        if not s.processed_key:
            raise NotFound("This sample has not been processed yet")
        img = cv2.imdecode(np.frombuffer(storage.get(s.processed_key), np.uint8), cv2.IMREAD_COLOR)
    else:
        img = load_image(storage.get(s.storage_key), s.mime_type)
        if variant == "corrected":
            img = apply_corrections(img, s.corrections)
    h, w = img.shape[:2]
    if max(h, w) > max_side:
        f = max_side / max(h, w)
        img = cv2.resize(img, (int(w * f), int(h * f)), interpolation=cv2.INTER_AREA)
    _ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return Response(buf.tobytes(), media_type="image/jpeg", headers={"Cache-Control": "private, max-age=60"})


@router.post("/{pid}/samples/{sid}/transcribe", response_model=SampleOut)
def transcribe_sample(pid: str, sid: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Draft a transcript of a free-form sample with the configured AI model (the user should check it)."""
    from app.ai.providers import HANDWRITING_TRANSCRIBE_PROMPT, LLMError, get_provider, provider_configured

    p = owned_profile(db, pid, user)
    s = owned(db, HandwritingSample, sid, user)
    if s.profile_id != p.id:
        raise NotFound("HandwritingSample not found")
    if not provider_configured():
        raise ConfigurationError("No AI provider is configured; type the transcript instead")
    check_rate(user.id, "transcribe", get_settings().rate_limit_generate_per_min)
    from app.ai.budget import enforce_budget
    from app.ai.usage import usage_scope

    enforce_budget(db, user.id)
    img = apply_corrections(load_image(get_storage().get(s.storage_key), s.mime_type), s.corrections)
    f = min(1.0, 2000 / max(img.shape[:2]))
    if f < 1:
        img = cv2.resize(img, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
    _ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 88])
    try:
        with usage_scope(user.id, "transcribe", profile_id=p.id):
            s.transcript = get_provider("transcribe", user.model_routing).transcribe_image(
                buf.tobytes(), HANDWRITING_TRANSCRIBE_PROMPT, "image/jpeg"
            )
    except LLMError as exc:
        raise AppError(f"Transcription failed: {exc}", code="ai_error", status_code=502) from exc
    db.commit()
    return SampleOut.model_validate(s).model_copy(update={"has_processed": bool(s.processed_key)})


@router.post("/{pid}/process", response_model=JobOut, status_code=202)
def process(pid: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Analyse all samples and (re)build the glyph library in the background."""
    p = owned_profile(db, pid, user)
    if not p.samples:
        raise Conflict("Upload at least one sample first")
    p.status = ProfileStatus.processing
    db.commit()
    return enqueue(db, user_id=user.id, type="process_profile", profile_id=p.id)


@router.post("/{pid}/preview", response_class=Response)
def preview(pid: str, body: PreviewIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Render arbitrary text in this handwriting (PNG). Warnings are returned in X-Render-Warnings."""
    p = owned_profile(db, pid, user)
    if not p.glyph_library_key:
        raise Conflict("Process the profile before previewing it")
    check_rate(user.id, "preview", 60)
    png, warnings = svc.preview_png(
        p, body.text, body.style.model_dump(exclude_none=True) if body.style else None, body.paper
    )
    return Response(
        png, media_type="image/png", headers={"X-Render-Warnings": json.dumps([w["message"] for w in warnings])[:4000]}
    )


@router.get("/{pid}/glyphs.png", response_class=Response)
def glyph_sheet(pid: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = owned_profile(db, pid, user)
    if not p.glyph_library_key:
        raise NotFound("No glyphs yet")
    return Response(svc.glyph_sheet_png(p), media_type="image/png")
