from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import FileResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.providers import provider_configured, provider_status
from app.api.deps import owned, owned_answer, owned_assignment, owned_question, owned_rendered, read_upload
from app.config import get_settings
from app.db import get_db
from app.errors import AppError, ConfigurationError, Conflict, NotFound
from app.ingestion.pdf import PdfValidationError, extract_pdf, validate_pdf
from app.jobs.queue import enqueue
from app.models import (
    AnswerStatus,
    Assignment,
    AssignmentStatus,
    ExtractedQuestion,
    HandwritingProfile,
    JobStatus,
    ProcessingJob,
    RenderedDocument,
    SourceDocument,
    User,
    utcnow,
)
from app.ratelimit import check_rate
from app.render.markup import word_count
from app.render.settings import RenderSettings
from app.schemas import (
    AnswerOut,
    AnswerSpaceIn,
    AnswerUpdate,
    AssignmentDetail,
    AssignmentOut,
    AssignmentUpdate,
    DocumentOut,
    ExportIn,
    GenerateIn,
    JobOut,
    MergeIn,
    QuestionIn,
    QuestionOut,
    QuestionUpdate,
    RenderedOut,
    RenderIn,
    ReorderIn,
    SplitIn,
)
from app.security import current_user
from app.services import assignments as svc
from app.services.retention import delete_assignment
from app.storage import get_storage, safe_filename

router = APIRouter(tags=["assignments"])


def _detail(db: Session, a: Assignment) -> AssignmentDetail:
    out = AssignmentDetail.model_validate(a)
    out.documents = [DocumentOut.model_validate(d) for d in a.documents]
    out.questions = [QuestionOut.model_validate(q) for q in a.questions]
    out.effective_preferences = svc.preferences(a).model_dump()
    out.rendered = [RenderedOut.model_validate(r) for r in a.rendered]
    out.render_settings = svc.effective_settings(
        a, db.get(HandwritingProfile, a.profile_id) if a.profile_id else None
    ).model_dump()
    jobs = db.scalars(
        select(ProcessingJob).where(
            ProcessingJob.assignment_id == a.id, ProcessingJob.status.in_([JobStatus.queued, JobStatus.running])
        )
    ).all()
    out.active_jobs = [JobOut.model_validate(j) for j in jobs]
    included = [q for q in a.questions if not q.excluded]
    out.stats = {
        "questions": len(included),
        "needs_review": sum(1 for q in included if q.needs_review),
        "answered": sum(1 for q in included if q.answer and q.answer.content.strip()),
        "approved": sum(1 for q in included if q.answer and q.answer.status == AnswerStatus.approved),
        "words": sum(q.answer.word_count for q in included if q.answer),
        "cost_usd": round(sum((q.answer.cost_usd or 0) for q in included if q.answer), 4),
        "ai": provider_status(svc.routing(a)),
    }
    return out


# ------------------------------------------------------------------------------------- assignments


@router.get("/assignments", response_model=list[AssignmentOut])
def list_assignments(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return db.scalars(
        select(Assignment).where(Assignment.user_id == user.id).order_by(Assignment.updated_at.desc())
    ).all()


@router.post("/assignments", response_model=AssignmentDetail, status_code=201)
def create_assignment(
    file: UploadFile = File(..., description="Assignment PDF"),
    title: str | None = Form(None, max_length=255),
    subject: str | None = Form(None, max_length=120),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Upload an assignment PDF; question extraction starts in the background."""
    data, _mime, sha = read_upload(file, {"application/pdf"})
    try:
        pages = validate_pdf(data, get_settings().max_pdf_pages)
    except PdfValidationError as exc:
        raise AppError(str(exc), code=f"pdf_{exc.code}", status_code=422) from exc
    name = safe_filename(file.filename or "assignment.pdf")
    a = Assignment(user_id=user.id, title=(title or name.rsplit(".", 1)[0])[:255], subject=subject, preferences={})
    db.add(a)
    db.flush()
    doc = SourceDocument(
        user_id=user.id,
        assignment_id=a.id,
        role="assignment",
        original_filename=name,
        storage_key="",
        sha256=sha,
        page_count=pages,
    )
    db.add(doc)
    db.flush()
    doc.storage_key = f"{svc.assignment_prefix(a)}/docs/{doc.id}/source.pdf"
    get_storage().put(doc.storage_key, data)
    db.commit()
    enqueue(db, user_id=user.id, type="extract", assignment_id=a.id)
    db.refresh(a)
    return _detail(db, a)


@router.get("/assignments/{aid}", response_model=AssignmentDetail)
def get_assignment(aid: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return _detail(db, owned_assignment(db, aid, user))


@router.patch("/assignments/{aid}", response_model=AssignmentDetail)
def update_assignment(
    aid: str, body: AssignmentUpdate, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    a = owned_assignment(db, aid, user)
    if body.title is not None:
        a.title = body.title.strip()
    if body.subject is not None:
        a.subject = body.subject.strip() or None
    if body.instructions is not None:
        a.instructions = body.instructions.strip() or None
    if body.preferences is not None:
        # Per-assignment overrides of the global preferences (rarely needed; see PUT /preferences)
        a.preferences = {**(a.preferences or {}), **body.preferences.model_dump(exclude_none=True)}
    if body.clear_preference_overrides:
        a.preferences = {}
    if "profile_id" in body.model_fields_set:
        if body.profile_id is not None:
            owned(db, HandwritingProfile, body.profile_id, user)
        a.profile_id = body.profile_id
    db.commit()
    return _detail(db, a)


@router.delete("/assignments/{aid}", status_code=204)
def remove_assignment(aid: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Delete the assignment, its uploaded PDFs, answers and every export."""
    delete_assignment(db, owned_assignment(db, aid, user))


@router.post("/assignments/{aid}/references", response_model=DocumentOut, status_code=201)
def add_reference(
    aid: str, file: UploadFile = File(...), user: User = Depends(current_user), db: Session = Depends(get_db)
):
    """Attach reference material (PDF). Its text is used to ground answers and citations."""
    a = owned_assignment(db, aid, user)
    data, _mime, sha = read_upload(file, {"application/pdf"})
    try:
        pages = validate_pdf(data, get_settings().max_pdf_pages)
    except PdfValidationError as exc:
        raise AppError(str(exc), code=f"pdf_{exc.code}", status_code=422) from exc
    doc = SourceDocument(
        user_id=user.id,
        assignment_id=a.id,
        role="reference",
        original_filename=safe_filename(file.filename or "reference.pdf"),
        storage_key="",
        sha256=sha,
        page_count=pages,
    )
    db.add(doc)
    db.flush()
    doc.storage_key = f"{svc.assignment_prefix(a)}/docs/{doc.id}/source.pdf"
    get_storage().put(doc.storage_key, data)
    ex = extract_pdf(data, None)
    doc.extracted_text = "\n".join(ln.text for ln in ex.lines)[:500_000]
    doc.pages = [
        {"index": p.index, "width": p.width, "height": p.height, "has_text": p.has_text, "issues": p.issues}
        for p in ex.pages
    ]
    db.commit()
    return doc


@router.delete("/assignments/{aid}/documents/{did}", status_code=204)
def remove_document(aid: str, did: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    a = owned_assignment(db, aid, user)
    d = owned(db, SourceDocument, did, user)
    if d.assignment_id != a.id or d.role != "reference":
        raise NotFound("Only reference documents can be removed")
    get_storage().delete_prefix(f"{svc.assignment_prefix(a)}/docs/{d.id}")
    db.delete(d)
    db.commit()


@router.get("/assignments/{aid}/documents/{did}/pages/{n}.png", response_class=Response)
def document_page(aid: str, did: str, n: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    a = owned_assignment(db, aid, user)
    d = owned(db, SourceDocument, did, user)
    if d.assignment_id != a.id or not 1 <= n <= len(d.pages or []):
        raise NotFound("Page not found")
    key = d.pages[n - 1].get("image_key")
    if not key:
        from app.ingestion.pdf import render_page_png

        return Response(render_page_png(get_storage().get(d.storage_key), n - 1), media_type="image/png")
    return Response(get_storage().get(key), media_type="image/png", headers={"Cache-Control": "private, max-age=300"})


@router.get("/assignments/{aid}/documents/{did}/source.pdf", response_class=Response)
def document_pdf(aid: str, did: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    a = owned_assignment(db, aid, user)
    d = owned(db, SourceDocument, did, user)
    if d.assignment_id != a.id:
        raise NotFound("Document not found")
    return Response(get_storage().get(d.storage_key), media_type="application/pdf")


@router.get("/assignments/{aid}/figures", response_class=Response)
def figure(
    aid: str, key: str = Query(..., max_length=512), user: User = Depends(current_user), db: Session = Depends(get_db)
):
    a = owned_assignment(db, aid, user)
    if not key.startswith(f"{svc.assignment_prefix(a)}/figures/"):
        raise NotFound("Figure not found")
    return Response(get_storage().get(key), media_type="image/png")


@router.post("/assignments/{aid}/extract", response_model=JobOut, status_code=202)
def re_extract(aid: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Re-read the PDF. Questions with answers (and your own added questions) are kept: their page
    layout is refreshed and their text, marks and answers are left as they are."""
    a = owned_assignment(db, aid, user)
    return enqueue(db, user_id=user.id, type="extract", assignment_id=a.id)


# ------------------------------------------------------------------------------------- questions


@router.get("/assignments/{aid}/questions", response_model=list[QuestionOut])
def list_questions(aid: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return owned_assignment(db, aid, user).questions


def _renumber(a: Assignment) -> None:
    for i, q in enumerate(sorted(a.questions, key=lambda q: q.position)):
        q.position = i


@router.post("/assignments/{aid}/questions", response_model=QuestionOut, status_code=201)
def add_question(aid: str, body: QuestionIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    a = owned_assignment(db, aid, user)
    pos = len(a.questions)
    if body.after_id:
        after = owned_question(db, body.after_id, user)
        if after.assignment_id != a.id:
            raise NotFound("Question not found")
        pos = after.position + 1
        for q in a.questions:
            if q.position >= pos:
                q.position += 1
    q = ExtractedQuestion(
        user_id=user.id,
        assignment_id=a.id,
        position=pos,
        number=body.number,
        text=body.text,
        marks=body.marks,
        instructions=body.instructions,
        context=body.context,
        origin="user",
        pages=[],
    )
    db.add(q)
    a.extraction_confirmed = False
    db.commit()
    return q


@router.patch("/questions/{qid}", response_model=QuestionOut)
def update_question(qid: str, body: QuestionUpdate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    q = owned_question(db, qid, user)
    for f in ("number", "text", "instructions", "context", "excluded"):
        v = getattr(body, f)
        if v is not None:
            setattr(q, f, v)
    if body.marks is not None:
        q.marks = body.marks
    if body.clear_marks:
        q.marks = None
    if body.reviewed:
        q.needs_review = False
    db.commit()
    return q


@router.put("/questions/{qid}/answer-space", response_model=QuestionOut)
def set_answer_space(qid: str, body: AnswerSpaceIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Place this question's answer area on a page of the original PDF by hand."""
    q = owned_question(db, qid, user)
    doc = svc.source_document(q.assignment)
    if doc is None or not doc.pages or body.page >= len(doc.pages):
        raise AppError("That page is not part of the assignment PDF", code="invalid_page", status_code=422)
    pw, ph = doc.pages[body.page]["width"], doc.pages[body.page]["height"]
    top, bottom = max(0.0, body.top), min(ph, body.bottom)
    x0, x1 = max(0.0, body.x0), min(pw, body.x1)
    if bottom - top < 14 or x1 - x0 < 36:  # about one line and a few words
        raise AppError("The answer area is too small to write in", code="area_too_small", status_code=422)
    space = {"page": body.page, "top": top, "bottom": bottom, "x0": x0, "x1": x1, "fraction": (bottom - top) / ph}
    q.pages = svc.with_answer_space(q.pages, space)
    db.commit()
    return q


@router.delete("/questions/{qid}/answer-space", response_model=QuestionOut)
def reset_answer_space(qid: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Go back to the answer area found when the PDF was read."""
    q = owned_question(db, qid, user)
    q.pages = svc.with_answer_space(q.pages, None)
    db.commit()
    return q


@router.delete("/questions/{qid}", status_code=204)
def delete_question(qid: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    q = owned_question(db, qid, user)
    a = q.assignment
    db.delete(q)
    db.flush()
    db.refresh(a)
    _renumber(a)
    db.commit()


@router.post("/assignments/{aid}/questions/reorder", response_model=list[QuestionOut])
def reorder(aid: str, body: ReorderIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    a = owned_assignment(db, aid, user)
    by_id = {q.id: q for q in a.questions}
    if set(body.ids) != set(by_id):
        raise AppError(
            "ids must list every question of the assignment exactly once", code="invalid_order", status_code=422
        )
    for i, qid in enumerate(body.ids):
        by_id[qid].position = i
    db.commit()
    db.refresh(a)
    return a.questions


@router.post("/assignments/{aid}/questions/merge", response_model=QuestionOut)
def merge(aid: str, body: MergeIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Merge questions into the first one (in document order). Existing answers of the others are dropped."""
    a = owned_assignment(db, aid, user)
    qs = sorted((owned_question(db, i, user) for i in body.ids), key=lambda q: q.position)
    if any(q.assignment_id != a.id for q in qs):
        raise NotFound("Question not found")
    first = qs[0]
    first.text = "\n\n".join(q.text for q in qs)
    marks = [q.marks for q in qs if q.marks is not None]
    first.marks = sum(marks) if marks else None
    first.pages = [p for q in qs for p in (q.pages or [])]
    first.figures = [f for q in qs for f in (q.figures or [])]
    for q in qs[1:]:
        db.delete(q)
    db.flush()
    db.refresh(a)
    _renumber(a)
    db.commit()
    return first


@router.post("/questions/{qid}/split", response_model=list[QuestionOut])
def split(qid: str, body: SplitIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    q = owned_question(db, qid, user)
    if body.at >= len(q.text):
        raise AppError("Split position is past the end of the question", code="invalid_split", status_code=422)
    a = q.assignment
    head, tail = q.text[: body.at].strip(), q.text[body.at :].strip()
    if not head or not tail:
        raise AppError("Both parts must contain text", code="invalid_split", status_code=422)
    for other in a.questions:
        if other.position > q.position:
            other.position += 1
    q.text = head
    new = ExtractedQuestion(
        user_id=user.id,
        assignment_id=a.id,
        position=q.position + 1,
        number=body.number or "",
        text=tail,
        instructions=q.instructions,
        context=q.context,
        pages=q.pages,
        origin="user",
    )
    db.add(new)
    db.commit()
    return [q, new]


@router.post("/assignments/{aid}/confirm-extraction", response_model=AssignmentDetail)
def confirm_extraction(aid: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    a = owned_assignment(db, aid, user)
    if not any(not q.excluded for q in a.questions):
        raise Conflict("Add or include at least one question first")
    a.extraction_confirmed = True
    for q in a.questions:
        q.needs_review = False
    if a.status in (AssignmentStatus.awaiting_review, AssignmentStatus.extraction_failed, AssignmentStatus.uploaded):
        a.status = (
            AssignmentStatus.awaiting_approval
            if any(q.answer for q in a.questions)
            else AssignmentStatus.awaiting_review
        )
    db.commit()
    return _detail(db, a)


# ------------------------------------------------------------------------------------- answers


@router.get("/assignments/{aid}/estimate")
def estimate(aid: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Approximate token use and cost for generating all included answers."""
    a = owned_assignment(db, aid, user)
    return {**svc.estimate_generation(a, None), "ai": provider_status(svc.routing(a))}


@router.post("/assignments/{aid}/generate", response_model=JobOut, status_code=202)
def generate(aid: str, body: GenerateIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    a = owned_assignment(db, aid, user)
    if not a.extraction_confirmed:
        raise Conflict("Confirm the extracted questions before generating answers")
    if not provider_configured():
        raise ConfigurationError(
            "No AI provider is configured (set LLM_PROVIDER and its credentials). You can still write answers yourself."
        )
    s = get_settings()
    check_rate(user.id, "generate", s.rate_limit_generate_per_min)
    from app.ai.budget import enforce_budget

    enforce_budget(db, user.id)
    n = len([q for q in a.questions if not q.excluded and (body.question_ids is None or q.id in body.question_ids)])
    if n > s.max_questions_per_generation:
        raise AppError(
            f"At most {s.max_questions_per_generation} questions per request", code="too_many", status_code=422
        )
    return enqueue(db, user_id=user.id, type="generate", assignment_id=a.id, payload=body.model_dump())


def _answer_for(db: Session, qid: str, user: User):
    q = owned_question(db, qid, user)
    return q, svc.ensure_answer(db, q)


@router.put("/questions/{qid}/answer", response_model=AnswerOut)
def write_answer(qid: str, body: AnswerUpdate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Create or edit an answer by hand (autosave target). Editing an approved answer un-approves it."""
    _q, ans = _answer_for(db, qid, user)
    if body.expected_revision is not None and body.expected_revision != ans.revision:
        raise Conflict("The answer changed elsewhere; reload before saving", details={"revision": ans.revision})
    if body.content != ans.content:
        if ans.status in (AnswerStatus.generated, AnswerStatus.approved) and ans.revision > 0:
            svc.snapshot(ans)
        ans.content = body.content
        ans.word_count = word_count(body.content)
        ans.status = AnswerStatus.edited if body.content.strip() else AnswerStatus.pending
        ans.revision += 1
        ans.approved_at = None
    db.commit()
    return ans


@router.post("/answers/{ans_id}/approve", response_model=AnswerOut)
def approve(ans_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    ans = owned_answer(db, ans_id, user)
    if not ans.content.strip():
        raise Conflict("An empty answer cannot be approved")
    ans.status = AnswerStatus.approved
    ans.approved_at = utcnow()
    db.commit()
    return ans


@router.post("/answers/{ans_id}/unapprove", response_model=AnswerOut)
def unapprove(ans_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    ans = owned_answer(db, ans_id, user)
    if ans.status == AnswerStatus.approved:
        ans.status = AnswerStatus.edited
        ans.approved_at = None
        db.commit()
    return ans


@router.post("/answers/{ans_id}/restore/{revision}", response_model=AnswerOut)
def restore(ans_id: str, revision: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Bring back an earlier version of an answer (the current one is kept in history)."""
    ans = owned_answer(db, ans_id, user)
    old = next((h for h in ans.history if h.get("revision") == revision), None)
    if old is None:
        raise NotFound("That version is not in the history")
    svc.snapshot(ans)
    ans.content = old["content"]
    ans.word_count = word_count(ans.content)
    ans.status = AnswerStatus.edited
    ans.revision += 1
    ans.approved_at = None
    db.commit()
    return ans


@router.post("/assignments/{aid}/answers/approve-all", response_model=list[AnswerOut])
def approve_all(aid: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    a = owned_assignment(db, aid, user)
    out = []
    for q in a.questions:
        if not q.excluded and q.answer and q.answer.content.strip() and q.answer.status != AnswerStatus.approved:
            q.answer.status = AnswerStatus.approved
            q.answer.approved_at = utcnow()
            out.append(q.answer)
    db.commit()
    return out


# ------------------------------------------------------------------------------------- rendering


@router.get("/assignments/{aid}/render-config")
def get_render_config(aid: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    a = owned_assignment(db, aid, user)
    out = svc.effective_settings(a, db.get(HandwritingProfile, a.profile_id) if a.profile_id else None).model_dump()
    # The assignment's own choice: "auto" (same as the profile) unless it was set here.
    out["letter_style"] = ((a.render_config.settings if a.render_config else {}) or {}).get("letter_style", "auto")
    return out


@router.put("/assignments/{aid}/render-config")
def put_render_config(
    aid: str, body: RenderSettings, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    a = owned_assignment(db, aid, user)
    if body.paper == "source" and svc.source_document(a) is None:
        raise AppError("There is no source document to write on", code="no_source", status_code=422)
    cfg = svc.ensure_render_config(db, a)
    cfg.settings = {**body.model_dump(), "_user_set_paper": True}
    db.commit()
    return body.model_dump()


@router.post("/assignments/{aid}/render", response_model=JobOut, status_code=202)
def render(aid: str, body: RenderIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Render a preview (any answers) or the final PDF (all included answers must be approved)."""
    a = owned_assignment(db, aid, user)
    if a.profile_id is None:
        raise Conflict("Choose a handwriting profile first")
    if not body.preview:
        _sections, unapproved = svc.build_sections(a, require_approved=True)
        if unapproved:
            raise Conflict("Approve every answer before the final export", details={"unapproved": unapproved})
    check_rate(user.id, "render", get_settings().rate_limit_render_per_min)
    return enqueue(db, user_id=user.id, type="render", assignment_id=a.id, payload={"preview": body.preview})


@router.post("/assignments/{aid}/export", response_model=JobOut, status_code=202)
def export(aid: str, body: ExportIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    a = owned_assignment(db, aid, user)
    return enqueue(db, user_id=user.id, type="export", assignment_id=a.id, payload={"kind": body.kind}, dedupe=False)


@router.get("/rendered/{rid}", response_model=RenderedOut)
def rendered_info(rid: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return owned_rendered(db, rid, user)


@router.get("/rendered/{rid}/pages/{n}.png", response_class=Response)
def rendered_page(rid: str, n: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    r = owned_rendered(db, rid, user)
    if not 1 <= n <= len(r.page_image_keys or []):
        raise NotFound("Page not found")
    return Response(
        get_storage().get(r.page_image_keys[n - 1]),
        media_type="image/png",
        headers={"Cache-Control": "private, max-age=3600"},
    )


@router.get("/rendered/{rid}/download")
def download(rid: str, inline: bool = False, user: User = Depends(current_user), db: Session = Depends(get_db)):
    r = owned_rendered(db, rid, user)
    a = db.get(Assignment, r.assignment_id)
    # No brackets: safe_filename would turn "(preview)" into "_preview_".
    if r.kind == "handwritten":  # the file to hand in: just the title marked _H
        name = safe_filename(f"{a.title}_H{'_preview' if r.is_preview else ''}.pdf")
    else:
        suffix = {"digital": "typed", "review": "review"}.get(r.kind, r.kind)
        name = safe_filename(f"{a.title} - {suffix}{' preview' if r.is_preview else ''}.pdf")
    if not r.is_preview:
        r.downloaded_at = utcnow()
        db.commit()
    disp = "inline" if inline else "attachment"
    path = get_storage().local_path(r.storage_key)
    headers = {"Content-Disposition": f'{disp}; filename="{name}"', "Cache-Control": "private, no-store"}
    if path is not None and path.is_file():
        return FileResponse(path, media_type="application/pdf", headers=headers)
    return Response(get_storage().get(r.storage_key), media_type="application/pdf", headers=headers)


@router.delete("/rendered/{rid}", status_code=204)
def delete_rendered(rid: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    r = owned_rendered(db, rid, user)
    a = db.get(Assignment, r.assignment_id)
    get_storage().delete_prefix(f"{svc.assignment_prefix(a)}/rendered/{r.id}")
    db.delete(r)
    db.commit()


@router.get("/exports", response_model=list[RenderedOut])
def export_history(
    limit: int = Query(30, ge=1, le=200), user: User = Depends(current_user), db: Session = Depends(get_db)
):
    return db.scalars(
        select(RenderedDocument)
        .where(RenderedDocument.user_id == user.id, RenderedDocument.is_preview.is_(False))
        .order_by(RenderedDocument.created_at.desc())
        .limit(limit)
    ).all()
