"""Assignment pipeline: extraction, answer generation, rendering and exports."""

import contextvars
import hashlib
import io
import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.generate import QuestionSpec, generate_answer, is_hard_question
from app.ai.pricing import cost
from app.ai.prompts import AnswerPreferences
from app.ai.providers import LLMConfigError, LLMError, LLMTransientError, get_provider
from app.ai.retrieval import chunk, top_chunks
from app.ai.usage import usage_scope
from app.db import session_factory
from app.ingestion.ocr import get_ocr
from app.ingestion.pdf import extract_pdf
from app.ingestion.questions import segment
from app.logging_setup import Timer
from app.models import (
    AnswerStatus,
    Assignment,
    AssignmentStatus,
    ExtractedQuestion,
    GeneratedAnswer,
    HandwritingProfile,
    ProfileStatus,
    RenderConfiguration,
    RenderedDocument,
    utcnow,
)
from app.render.layout import AnswerSpace, Layouter, SectionInput
from app.render.markup import word_count
from app.render.pdf import compose_with_source, rasterize, render_pdf
from app.render.settings import RenderSettings
from app.services.profiles import engine_for, load_library, style_from
from app.storage import get_storage

log = logging.getLogger(__name__)
PT_TO_MM = 25.4 / 72


class PipelineError(Exception):
    """A user-facing problem that stops a job (message is safe to show)."""


def assignment_prefix(a: Assignment) -> str:
    return f"users/{a.user_id}/assignments/{a.id}"


def source_document(a: Assignment):
    return next((d for d in a.documents if d.role == "assignment"), None)


# ------------------------------------------------------------------------------------- extraction


def run_extraction(db: Session, a: Assignment, progress) -> dict:
    doc = source_document(a)
    if doc is None:
        raise PipelineError("No assignment PDF was uploaded")
    storage = get_storage()
    data = storage.get(doc.storage_key)
    progress(0.1, "Reading pages")
    with usage_scope(a.user_id, "ocr", assignment_id=a.id), Timer() as t_read:
        ex = extract_pdf(data, get_ocr(routing(a)))
    for p in ex.pages:
        log.info(
            f"page {p.index + 1}: {'text layer' if p.has_text else 'scanned'}",
            extra={
                "page": p.index + 1,
                "has_text": p.has_text,
                "ocr": p.ocr,
                "confidence": round(p.confidence, 3),
                "vector_graphics": p.vector_graphics,
                "issues": p.issues or None,
            },
        )
    progress(0.6, "Finding questions")
    with Timer() as t_seg:
        seg = segment(ex.lines, ex.pages, ex.figures)
    log.info(
        f"extraction: {len(seg.questions)} questions from {len(ex.pages)} pages",
        extra={
            "pages": len(ex.pages),
            "lines": len(ex.lines),
            "figures": len(ex.figures),
            "questions": len(seg.questions),
            "needs_review": sum(q.needs_review for q in seg.questions),
            "excluded": sum(q.excluded for q in seg.questions),
            "numbers": [q.number for q in seg.questions][:40],
            "scheme_notes": seg.notes or None,
            "read_ms": t_read.ms,
            "segment_ms": t_seg.ms,
            "pdf_bytes": len(data),
        },
    )
    for q in seg.questions:
        if q.reasons:
            log.info(f"question {q.number} flagged for review", extra={"number": q.number, "reasons": q.reasons})
    prefix = assignment_prefix(a)
    pages_meta = []
    for p in ex.pages:
        key = f"{prefix}/docs/{doc.id}/pages/{p.index + 1}.png"
        storage.put(key, p.image_png)
        pages_meta.append(
            {
                "index": p.index,
                "width": p.width,
                "height": p.height,
                "has_text": p.has_text,
                "ocr": p.ocr,
                "confidence": round(p.confidence, 3),
                "issues": p.issues,
                "image_key": key,
            }
        )
    doc.pages = pages_meta
    doc.page_count = len(ex.pages)
    doc.extracted_text = "\n".join(ln.text for ln in ex.lines)[:500_000]

    # Re-extraction keeps the user's work: an extracted question that has an answer or was edited is
    # matched to the new extraction by its number and only its page layout (where it is printed and
    # where its answer goes) is refreshed. Unmatched questions without answers are replaced; the
    # user's own added questions are always kept.
    existing = {q.number: q for q in a.questions if q.origin == "extracted"}
    matched: set[str] = set()
    for q in list(a.questions):
        if q.origin == "extracted" and not q.answer and q.number not in {d.number for d in seg.questions}:
            db.delete(q)
    db.flush()
    keep = [q for q in a.questions if q.origin != "extracted"]
    figure_keys = []
    for fi, fig in enumerate(ex.figures):
        key = f"{prefix}/figures/{doc.id}-{fi}.png"
        storage.put(key, fig.png)
        figure_keys.append({"key": key, "page": fig.page, "bbox": list(fig.bbox)})
    for pos, d in enumerate(seg.questions):
        space = d.answer_space
        layout = [
            {"page": pg, "y0": y0, "y1": y1, **({"answer_space": space} if space and space["page"] == pg else {})}
            for pg, y0, y1 in d.y_range
        ]
        prev = existing.get(d.number)
        if prev is not None and prev.number not in matched:
            matched.add(prev.number)
            prev.position = pos
            prev.pages = layout
            if not prev.figures:
                prev.figures = [figure_keys[i] for i in d.figure_indexes]
            log.info(f"question {d.number}: layout refreshed, text, marks and answer kept", extra={"number": d.number})
            continue
        db.add(
            ExtractedQuestion(
                user_id=a.user_id,
                assignment_id=a.id,
                position=pos,
                number=d.number,
                text=d.text,
                marks=d.marks,
                instructions=d.instructions,
                context=d.context,
                pages=layout,
                confidence=d.confidence,
                needs_review=d.needs_review,
                review_reasons=d.reasons,
                has_visual=d.has_visual,
                excluded=d.excluded,
                figures=[figure_keys[i] for i in d.figure_indexes],
            )
        )
    # Extracted questions that no longer appear but have answers are kept at the end, flagged.
    orphans = [q for q in existing.values() if q.number not in matched and q.answer is not None]
    for q in orphans:
        q.needs_review = True
        q.review_reasons = [*q.review_reasons, "not found when the PDF was re-read; check it still belongs here"]
    for k, q in enumerate([*orphans, *keep]):
        q.position = len(seg.questions) + k
    a.instructions = seg.preamble or None
    a.extraction_confirmed = False
    a.status = AssignmentStatus.awaiting_review
    a.error = None
    # Suggest writing on the original pages when every question leaves room for an answer.
    spaces = [d.answer_space for d in seg.questions if d.answer_space]
    if seg.questions and len(spaces) == len(seg.questions) and all(s["fraction"] >= 0.3 for s in spaces):
        cfg = ensure_render_config(db, a)
        if not cfg.settings.get("_user_set_paper"):
            cfg.settings = {**cfg.settings, "paper": "source"}
    db.commit()
    return {"questions": len(seg.questions), "notes": seg.notes, "pages": len(ex.pages)}


# ------------------------------------------------------------------------------------- generation


def routing(a: Assignment) -> dict:
    """The owner's per-task model choice (User.model_routing)."""
    from sqlalchemy.orm import object_session

    from app.models import User

    db = object_session(a)
    user = db.get(User, a.user_id) if db is not None else None
    return dict((user.model_routing if user else None) or {})


def preferences(a: Assignment) -> AnswerPreferences:
    """Effective answer preferences: the user's global ones, overridden by any set on the assignment."""
    from sqlalchemy.orm import object_session

    from app.models import User

    db = object_session(a)
    user = db.get(User, a.user_id) if db is not None else None
    merged = {**((user.answer_preferences if user else None) or {}), **(a.preferences or {})}
    return AnswerPreferences.model_validate(merged)


def _reference_chunks(a: Assignment) -> list[str]:
    out: list[str] = []
    for d in a.documents:
        if d.role == "reference" and d.extracted_text:
            out += chunk(d.extracted_text)
    return out


def ensure_answer(db: Session, q: ExtractedQuestion) -> GeneratedAnswer:
    if q.answer is None:
        q.answer = GeneratedAnswer(user_id=q.user_id, assignment_id=q.assignment_id, question_id=q.id)
        db.flush()
    return q.answer


def snapshot(ans: GeneratedAnswer) -> None:
    """Keep the previous version so a regeneration never silently destroys user work."""
    if ans.content.strip():
        ans.history = [
            *ans.history[-9:],
            {"revision": ans.revision, "content": ans.content, "status": ans.status, "at": utcnow().isoformat()},
        ]


CAPACITY_WORDS = [
    "the",
    "of",
    "and",
    "to",
    "in",
    "is",
    "that",
    "for",
    "it",
    "as",
    "with",
    "was",
    "on",
    "be",
    "by",
    "this",
    "are",
    "which",
    "or",
    "an",
    "from",
    "have",
    "not",
    "information",
    "system",
    "employee",
    "management",
    "data",
    "process",
    "development",
    "organisation",
    "records",
    "important",
    "decision",
    "payroll",
    "benefits",
]
COMFORT_SCALE = 0.85  # aim to fit at 85% of the chosen handwriting size, leaving fit-to-space headroom


def space_capacity(a: Assignment, profile: HandwritingProfile | None) -> dict[str, int]:
    """Words that fit in each question's answer space on the source pages, in this handwriting.

    Measured with the real glyphs: average shaped width of common words, line pitch and the
    space's height. Empty unless the render settings write on the source pages."""
    if profile is None or not profile.glyph_library_key:
        return {}
    s = effective_settings(a, profile)
    if s.paper != "source":
        return {}
    import random

    engine = engine_for(load_library(profile), s)
    rng = random.Random(0)
    widths = [engine.shape(w, rng).width for w in CAPACITY_WORDS]
    avg = sum(widths) / len(widths) + engine.space_width(rng)
    out: dict[str, int] = {}
    for sec in build_sections(a, require_approved=False)[0]:
        if sec.space is None:
            continue
        text_w = 210.0 - max(s.margins.left, sec.space.left) - s.margins.right
        doc = source_document(a)
        if doc and doc.pages and sec.space.page < len(doc.pages):
            text_w = (
                doc.pages[sec.space.page]["width"] * PT_TO_MM - max(s.margins.left, sec.space.left) - s.margins.right
            )
        lines = max(0, int((sec.space.bottom - sec.space.top) / s.line_spacing_mm) - 1)
        per_line = max(1.0, text_w / avg)
        full = lines * per_line * 0.8  # paragraph ends and indents waste part of some lines
        out[sec.key] = int(full / (COMFORT_SCALE**2))
    return out


def answer_fits(a: Assignment, profile: HandwritingProfile, qid: str, content: str) -> bool:
    """Lay one answer out in its answer space (with fit-to-space) and report whether it fits."""
    s = effective_settings(a, profile)
    doc = source_document(a)
    if s.paper != "source" or doc is None or not doc.pages:
        return True
    sec = next((x for x in build_sections(a, require_approved=False)[0] if x.key == qid), None)
    if sec is None or sec.space is None:
        return True
    sec.answer = content
    layouter = Layouter(engine_for(load_library(profile), s), s)
    res = layouter.run_on_source([sec], [(p["width"] * PT_TO_MM, p["height"] * PT_TO_MM) for p in doc.pages])
    return not any(w["type"] == "exceeds_space" for w in res.warnings)


MAX_FIT_RETRIES = 2


def run_generation(
    db: Session,
    a: Assignment,
    question_ids: list[str] | None,
    include_approved: bool,
    progress,
    only_failed: bool = False,
) -> dict:
    """Generate answers. Transient provider errors make the job retryable; a retry (``only_failed``)
    regenerates just the answers that failed, never ones that already succeeded."""
    route = routing(a)
    provider = get_provider("answer", route)  # raises LLMConfigError if not configured
    hard_provider = get_provider("answer_hard", route)
    prefs = preferences(a)
    refs_all = _reference_chunks(a)
    qs = [q for q in a.questions if not q.excluded and (question_ids is None or q.id in question_ids)]
    targets = []
    for q in qs:
        ans = ensure_answer(db, q)
        if ans.status == AnswerStatus.approved and not include_approved:
            continue
        if only_failed and ans.status not in (AnswerStatus.failed, AnswerStatus.generating):
            continue
        ans.status = AnswerStatus.generating
        ans.error = None
        targets.append(q)
    a.status = AssignmentStatus.generating_answers
    db.commit()
    if not targets:
        a.status = AssignmentStatus.awaiting_approval
        db.commit()
        return {"generated": 0, "failed": 0}
    storage = get_storage()
    capacity = space_capacity(a, db.get(HandwritingProfile, a.profile_id) if a.profile_id else None)
    specs = {}
    for q in targets:
        figs = []
        for f in q.figures or []:
            try:
                figs.append(storage.get(f["key"]))
            except Exception:
                log.info("figure missing", extra={"question": q.id})
        specs[q.id] = (
            QuestionSpec(q.number, q.text, q.marks, q.context, q.instructions, figs, capacity.get(q.id)),
            top_chunks(f"{q.context or ''} {q.text}", refs_all),
        )

    profile = db.get(HandwritingProfile, a.profile_id) if a.profile_id else None

    def work(qid: str):
        # Runs in a pool thread: declare the usage scope here (context variables do not cross threads).
        with usage_scope(a.user_id, "answer", assignment_id=a.id, question_id=qid):
            return _work(qid)

    def _work(qid: str):
        spec, refs = specs[qid]
        hard, why = is_hard_question(prefs, spec)
        chosen = hard_provider if hard else provider
        log.info(
            f"answer {spec.number or '?'} routed to {chosen.model}",
            extra={"question_id": qid, "hard": hard, "reason": why},
        )
        out = generate_answer(
            chosen, subject=a.subject, assignment_instructions=a.instructions, prefs=prefs, q=spec, references=refs
        )
        # Closed loop for fixed answer spaces: measure with the real handwriting, tighten if needed.
        tries = 0
        while spec.max_words and profile is not None:
            # Own session: this runs in a worker thread.
            with session_factory()() as s2:
                fits = answer_fits(s2.get(Assignment, a.id), s2.get(HandwritingProfile, profile.id), qid, out.content)
            if fits:
                break
            log.info(
                f"answer too long for its space (attempt {tries + 1})",
                extra={"question_id": qid, "words": out.word_count, "limit_words": spec.max_words},
            )
            if tries >= MAX_FIT_RETRIES:
                out.flags.append(
                    {
                        "type": "other",
                        "message": "Still too long for the space on the answer sheet; shorten it before exporting.",
                    }
                )
                break
            tries += 1
            spec.max_words = max(40, int(min(spec.max_words, out.word_count) * 0.82))
            prev_in, prev_out = out.input_tokens, out.output_tokens
            out = generate_answer(
                chosen,
                subject=a.subject,
                assignment_instructions=a.instructions,
                prefs=prefs,
                q=spec,
                references=refs,
            )
            out.input_tokens += prev_in
            out.output_tokens += prev_out
            out.cost_usd = cost(out.model, out.input_tokens, out.output_tokens)
        return qid, out

    done = failed = transient = 0
    errors: list[str] = []
    workers = max(1, min(3, len(targets)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        # copy_context: each worker thread keeps the job's log context (job_id, assignment_id, ...)
        futs = {pool.submit(contextvars.copy_context().run, work, q.id): q.id for q in targets}
        for fut in as_completed(futs):
            qid = futs[fut]
            q = db.get(ExtractedQuestion, qid)
            ans = ensure_answer(db, q)
            try:
                _, out = fut.result()
            except LLMError as exc:
                log.warning(
                    f"answer {q.number or '?'} failed: {exc}",
                    extra={"question_id": qid, "error_type": type(exc).__name__, "retryable": exc.retryable},
                )
                failed += 1
                transient += int(exc.retryable)
                ans.status = AnswerStatus.failed
                ans.error = str(exc)
                errors.append(f"{q.number or '?'}: {exc}")
                if isinstance(exc, LLMConfigError):
                    db.commit()
                    raise
            else:
                snapshot(ans)
                ans.content = out.content
                ans.flags = out.flags
                ans.word_count = out.word_count
                ans.model = out.model
                ans.input_tokens += out.input_tokens
                ans.output_tokens += out.output_tokens
                ans.cost_usd = (ans.cost_usd or 0) + out.cost_usd if out.cost_usd is not None else ans.cost_usd
                ans.status = AnswerStatus.generated
                ans.revision += 1
                ans.approved_at = None
                done += 1
                log.info(
                    f"answer {q.number or '?'} generated: {out.word_count} words",
                    extra={
                        "question_id": qid,
                        "words": out.word_count,
                        "flags": [f["type"] for f in out.flags] or None,
                        "model": out.model,
                        "input_tokens": out.input_tokens,
                        "output_tokens": out.output_tokens,
                        "cost_usd": out.cost_usd,
                        "space_limit_words": specs[qid][0].max_words,
                    },
                )
            db.commit()
            progress((done + failed) / len(targets), f"Answered {done + failed} of {len(targets)}")
    a.status = AssignmentStatus.awaiting_approval
    db.commit()
    if transient:
        raise LLMTransientError(f"{transient} answer(s) hit a temporary AI provider error")
    log.info(
        f"generation finished: {done} ok, {failed} failed",
        extra={"generated": done, "failed": failed, "transient": transient},
    )
    return {"generated": done, "failed": failed, "errors": errors[:20]}


# ------------------------------------------------------------------------------------- rendering


def ensure_render_config(db: Session, a: Assignment) -> RenderConfiguration:
    if a.render_config is None:
        a.render_config = RenderConfiguration(user_id=a.user_id, assignment_id=a.id, settings={})
        db.flush()
    return a.render_config


def effective_settings(
    a: Assignment, profile: HandwritingProfile | None, overrides: dict | None = None
) -> RenderSettings:
    stored = {
        k: v for k, v in ((a.render_config.settings if a.render_config else {}) or {}).items() if not k.startswith("_")
    }
    base = style_from(profile) if profile else {}
    if stored.get("letter_style") == "auto":
        del stored["letter_style"]  # same as the profile
    s = RenderSettings.model_validate({**base, **stored, **(overrides or {})})
    if s.sign_fields is None:
        from app.services.signature import signature_meta

        s = s.model_copy(update={"sign_fields": signature_meta(a.user_id) is not None})
    return s


def build_sections(a: Assignment, require_approved: bool) -> tuple[list[SectionInput], list[str]]:
    storage = get_storage()
    sections, unapproved = [], []
    for q in a.questions:
        if q.excluded:
            continue
        ans = q.answer
        content = ans.content if ans else ""
        if not ans or ans.status != AnswerStatus.approved:
            unapproved.append(q.number or f"#{q.position + 1}")  # always a human-readable label
            if require_approved:
                continue
        figs = []
        for f in q.figures or []:
            try:
                figs.append(storage.get(f["key"]))
            except Exception:
                figs.append(b"")
        space = None
        for pg in q.pages or []:
            sp = pg.get("answer_space") if isinstance(pg, dict) else None
            if sp:
                space = AnswerSpace(
                    sp["page"],
                    sp["top"] * PT_TO_MM,
                    sp["bottom"] * PT_TO_MM,
                    sp["x0"] * PT_TO_MM,
                    [(b[0] * PT_TO_MM, b[1] * PT_TO_MM) for b in sp.get("blocked", [])],
                )
        sections.append(SectionInput(q.id, q.number, q.text, content, q.marks, figs, space))
    return sections, unapproved


def settings_hash(
    a: Assignment, profile: HandwritingProfile, s: RenderSettings, sections: list[SectionInput], kind: str
) -> str:
    h = hashlib.sha256()
    h.update(json.dumps(s.model_dump(), sort_keys=True).encode())
    h.update(f"{profile.id}:{profile.library_version}:{kind}".encode())
    if s.sign_fields:
        from app.services.signature import signature_meta

        h.update(str((signature_meta(a.user_id) or {}).get("version")).encode())  # new signature -> re-render
    for sec in sections:
        h.update(f"{sec.key}|{sec.number}|{sec.question_text}|{sec.answer}|{sec.marks}".encode())
    return h.hexdigest()


def run_render(db: Session, a: Assignment, preview: bool, progress) -> dict:
    if a.profile_id is None:
        raise PipelineError("Choose a handwriting profile first")
    profile = db.get(HandwritingProfile, a.profile_id)
    if profile is None or profile.user_id != a.user_id or not profile.glyph_library_key:
        raise PipelineError("The selected handwriting profile is not ready")
    if profile.status not in (ProfileStatus.ready, ProfileStatus.needs_samples):
        raise PipelineError("The handwriting profile is still being processed")
    sections, unapproved = build_sections(a, require_approved=not preview)
    if not preview and unapproved:
        raise PipelineError("Approve every answer before exporting: " + ", ".join(unapproved[:10]))
    if not sections:
        raise PipelineError("There are no answers to render")
    s = effective_settings(a, profile)
    digest = settings_hash(a, profile, s, sections, "preview" if preview else "final")
    existing = db.scalar(
        select(RenderedDocument).where(
            RenderedDocument.assignment_id == a.id,
            RenderedDocument.settings_hash == digest,
            RenderedDocument.kind == "handwritten",
            RenderedDocument.is_preview == preview,
        )
    )
    if existing is not None and get_storage().exists(existing.storage_key):
        a.status = AssignmentStatus.completed if not preview else a.status
        db.commit()
        return {"rendered_id": existing.id, "cached": True}

    prev_status = a.status
    if not preview:
        a.status = AssignmentStatus.rendering
        db.commit()
    progress(0.1, "Loading handwriting")
    lib = load_library(profile)
    engine = engine_for(lib, s)
    layouter = Layouter(engine, s)
    progress(0.3, "Laying out pages")
    source_bytes = None
    if s.paper == "source":
        doc = source_document(a)
        if doc is None or not doc.pages:
            raise PipelineError("The original assignment pages are not available; choose another paper style")
        source_bytes = get_storage().get(doc.storage_key)
        sizes = [(p["width"] * PT_TO_MM, p["height"] * PT_TO_MM) for p in doc.pages]
        with Timer() as t_layout:
            result = layouter.run_on_source(sections, sizes)
    else:
        with Timer() as t_layout:
            result = layouter.run(sections)
    if source_bytes is not None:
        from app.ingestion.fields import detect_fields
        from app.render.fields import fill_fields
        from app.services.signature import signature_glyph

        fields = detect_fields(source_bytes)
        sig = signature_glyph(a.user_id) if s.sign_fields else None
        result.warnings += fill_fields(result, fields, s, engine, sig)
        log.info(
            "form fields",
            extra={
                "fields": [f"{f.page + 1}:{f.label}" for f in fields] or None,
                "signed": bool(sig and s.sign_fields),
                "values": sorted(s.field_values) or None,
            },
        )
    progress(0.6, "Drawing PDF")
    with Timer() as t_pdf:
        pdf = render_pdf(result, s, title=a.title)
        if source_bytes is not None:
            pdf = compose_with_source(pdf, source_bytes, result)
    warnings = list(result.warnings)
    if preview and unapproved:
        warnings.insert(
            0,
            {
                "type": "unapproved",
                "message": "Preview includes answers that are not approved yet: " + ", ".join(unapproved[:10]),
            },
        )
    progress(0.8, "Creating page previews")
    rd = RenderedDocument(
        user_id=a.user_id,
        assignment_id=a.id,
        kind="handwritten",
        is_preview=preview,
        storage_key="",
        page_count=len(result.pages),
        settings_hash=digest,
        warnings=warnings,
        size_bytes=len(pdf),
    )
    db.add(rd)
    db.flush()
    key_base = f"{assignment_prefix(a)}/rendered/{rd.id}"
    storage = get_storage()
    storage.put(f"{key_base}/document.pdf", pdf)
    keys = []
    for i, png in enumerate(rasterize(pdf, dpi=110)):
        k = f"{key_base}/pages/{i + 1}.png"
        storage.put(k, png)
        keys.append(k)
    rd.storage_key = f"{key_base}/document.pdf"
    rd.page_image_keys = keys
    glyphs = sum(1 for pg in result.pages for it in pg.items if type(it).__name__ == "GlyphItem")
    log.info(
        f"rendered {'preview' if preview else 'final'}: {len(result.pages)} pages",
        extra={
            "pages": len(result.pages),
            "paper": s.paper,
            "sections": len(sections),
            "glyphs_placed": glyphs,
            "warnings": [w["type"] for w in warnings] or None,
            "pdf_bytes": len(pdf),
            "layout_ms": t_layout.ms,
            "pdf_ms": t_pdf.ms,
            "profile_version": profile.library_version,
        },
    )
    if preview:
        _prune_previews(db, a, keep=rd.id)
        a.status = prev_status
    else:
        a.status = AssignmentStatus.completed
    db.commit()
    return {"rendered_id": rd.id, "pages": len(result.pages), "warnings": len(warnings)}


def _prune_previews(db: Session, a: Assignment, keep: str, max_keep: int = 3) -> None:
    previews = [r for r in a.rendered if r.is_preview and r.id != keep]
    previews.sort(key=lambda r: r.created_at, reverse=True)
    storage = get_storage()
    for r in previews[max_keep - 1 :]:
        storage.delete_prefix(f"{assignment_prefix(a)}/rendered/{r.id}")
        db.delete(r)


# ------------------------------------------------------------------------------------- exports


def export_digital(db: Session, a: Assignment, kind: str) -> RenderedDocument:
    """Typed copy of questions and answers (``digital``), or the handwritten pages side by side with
    the typed text (``review``)."""
    from app.render.digital import digital_pdf, review_pdf

    sections, _ = build_sections(a, require_approved=False)
    if kind == "digital":
        pdf = digital_pdf(a, sections)
    else:
        final = next((r for r in reversed(a.rendered) if r.kind == "handwritten"), None)
        if final is None:
            raise PipelineError("Render the handwritten assignment first")
        imgs = [get_storage().get(k) for k in final.page_image_keys]
        pdf = review_pdf(a, sections, imgs)
    rd = RenderedDocument(
        user_id=a.user_id,
        assignment_id=a.id,
        kind=kind,
        is_preview=False,
        storage_key="",
        page_count=0,
        settings_hash=hashlib.sha256(pdf).hexdigest(),
        size_bytes=len(pdf),
    )
    db.add(rd)
    db.flush()
    key = f"{assignment_prefix(a)}/rendered/{rd.id}/document.pdf"
    get_storage().put(key, pdf)
    rd.storage_key = key
    from pypdf import PdfReader

    rd.page_count = len(PdfReader(io.BytesIO(pdf)).pages)
    db.commit()
    return rd


def answer_word_count(text: str) -> int:
    return word_count(text)


def _profile_of(a: Assignment) -> HandwritingProfile | None:
    from sqlalchemy.orm import object_session

    db = object_session(a)
    return db.get(HandwritingProfile, a.profile_id) if db is not None and a.profile_id else None


def estimate_generation(a: Assignment, question_ids: list[str] | None) -> dict:
    """Token and cost estimate that routes each question exactly as generation does (light vs hard model)."""
    from app.ai.pricing import estimate_tokens, price_for
    from app.ai.prompts import SYSTEM_PROMPT, target_words
    from app.ai.providers import task_model

    prefs = preferences(a)
    route = routing(a)
    qs = [q for q in a.questions if not q.excluded and (question_ids is None or q.id in question_ids)]
    capacity = space_capacity(a, a_profile) if (a_profile := _profile_of(a)) else {}
    per_model: dict[str, dict] = {}
    for q in qs:
        spec = QuestionSpec(q.number, q.text, q.marks, q.context, q.instructions, list(q.figures or []))
        hard, _ = is_hard_question(prefs, spec)
        model = task_model("answer_hard" if hard else "answer", route)
        words = target_words(prefs, q.marks)
        if capacity.get(q.id):
            words = min(words, capacity[q.id])
        m = per_model.setdefault(
            model, {"model": model, "questions": 0, "hard": 0, "input_tokens": 0, "output_tokens": 0}
        )
        m["questions"] += 1
        m["hard"] += int(hard)
        m["input_tokens"] += estimate_tokens(len(SYSTEM_PROMPT) + len(q.text) + len(q.context or "") + 1500)
        m["output_tokens"] += int(words * 1.4)
    # Thinking tokens are billed as output and vary per question; show a range (1x to 3x the answer length).
    pricing_known = True
    sources = set()
    for m in per_model.values():
        price = price_for(m["model"])
        if price is None:
            pricing_known = False
            m["cost_usd_min"] = m["cost_usd_max"] = None
            continue
        sources.add(price.source)
        base = m["input_tokens"] / 1e6 * price.input_per_mtok
        m["cost_usd_min"] = round(base + m["output_tokens"] / 1e6 * price.output_per_mtok, 4)
        m["cost_usd_max"] = round(base + m["output_tokens"] * 3 / 1e6 * price.output_per_mtok, 4)
    models = sorted(per_model.values(), key=lambda m: -m["questions"])
    tin = sum(m["input_tokens"] for m in models)
    tout = sum(m["output_tokens"] for m in models)
    known = pricing_known and bool(models)
    return {
        "questions": len(qs),
        "space_limited_words": capacity,
        "input_tokens": tin,
        "output_tokens_min": tout,
        "output_tokens_max": tout * 3,
        "cost_usd_min": round(sum(m["cost_usd_min"] for m in models), 4) if known else None,
        "cost_usd_max": round(sum(m["cost_usd_max"] for m in models), 4) if known else None,
        "model": models[0]["model"] if models else task_model("answer", route),
        "models": models,
        "pricing_known": known,
        "pricing_source": sources.pop() if len(sources) == 1 else ("mixed" if sources else "unknown"),
    }
