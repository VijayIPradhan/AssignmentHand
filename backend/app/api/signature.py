from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.deps import EXT, owned_assignment, read_upload
from app.db import get_db
from app.errors import AppError, NotFound
from app.handwriting.preprocess import SampleImageError
from app.models import User
from app.security import current_user
from app.services import signature as sig
from app.storage import get_storage

router = APIRouter(tags=["signature"])


@router.get("/signature")
def get_signature(user: User = Depends(current_user)):
    """Whether you have uploaded a signature (and its size)."""
    meta = sig.signature_meta(user.id)
    return {"available": meta is not None, **(meta or {})}


@router.post("/signature", status_code=201)
async def upload_signature(
    file: UploadFile = File(..., description="Photo or scan of your own signature (JPG/PNG)"),
    rotate: float = Form(0, ge=-360, le=360),
    user: User = Depends(current_user),
):
    """Upload your signature. The paper background is removed and only the ink is kept. It is placed
    on signature lines only when you turn on "Sign the pages" for an assignment."""
    data, mime, _ = await read_upload(file, {"image/png", "image/jpeg"})
    try:
        meta = sig.save_signature(user.id, data, mime, EXT[mime], {"rotate": rotate} if rotate else None)
    except SampleImageError as exc:
        raise AppError(str(exc), code="invalid_signature", status_code=422) from exc
    return {"available": True, **meta}


@router.get("/signature/preview.png", response_class=Response)
def signature_preview(color: str = Query("#1f3a93", pattern=r"^#[0-9a-fA-F]{6}$"), user: User = Depends(current_user)):
    png = sig.preview_png(user.id, color)
    if png is None:
        raise NotFound("No signature uploaded")
    return Response(png, media_type="image/png", headers={"Cache-Control": "private, no-store"})


@router.delete("/signature", status_code=204)
def delete_signature(user: User = Depends(current_user)):
    sig.delete_signature(user.id)


@router.get("/assignments/{aid}/fields")
def assignment_fields(aid: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Fill-in fields printed on the original pages (e.g. "Registration Number: ____", signature lines),
    with suggested values found on the pages."""
    import io

    import pdfplumber

    from app.ingestion.fields import detect_fields, suggest_values
    from app.services.assignments import source_document

    a = owned_assignment(db, aid, user)
    doc = source_document(a)
    if doc is None:
        return {"fields": [], "suggestions": {}, "signature_available": sig.signature_meta(user.id) is not None}
    data = get_storage().get(doc.storage_key)
    fields = detect_fields(data)
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        text = "\n".join((p.extract_text() or "") for p in pdf.pages[:3])
    # One entry per distinct field (the same field usually repeats on every page)
    distinct: dict[str, dict] = {}
    for f in fields:
        d = distinct.setdefault(f.key, {"key": f.key, "label": f.label, "kind": f.kind, "pages": []})
        d["pages"].append(f.page + 1)
    return {
        "fields": list(distinct.values()),
        "suggestions": suggest_values(text, fields),
        "signature_available": sig.signature_meta(user.id) is not None,
    }
