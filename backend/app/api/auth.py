from fastapi import APIRouter, Depends, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.errors import AppError, Conflict, Unauthorized
from app.models import User
from app.ratelimit import check_rate
from app.schemas import AnswerPrefsIn, Credentials, ModelRoutingIn, ModelRoutingOut, TokenOut, UserOut
from app.security import COOKIE_NAME, create_token, current_user, hash_password, verify_password
from app.services.retention import delete_user

router = APIRouter(prefix="/auth", tags=["auth"])


def _set_cookie(resp: Response, token: str) -> None:
    s = get_settings()
    resp.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        secure=s.cookie_secure,
        samesite="lax",
        max_age=s.access_token_ttl_minutes * 60,
        path="/",
    )


@router.post("/register", response_model=TokenOut, status_code=201)
def register(body: Credentials, resp: Response, db: Session = Depends(get_db)):
    if not get_settings().allow_registration:
        raise AppError("Registration is disabled", code="registration_disabled", status_code=403)
    check_rate(body.email.lower(), "register", 5)
    email = body.email.lower()
    if db.scalar(select(User).where(User.email == email)):
        raise Conflict("An account with this email already exists")
    user = User(email=email, password_hash=hash_password(body.password), display_name=body.display_name)
    db.add(user)
    db.commit()
    token = create_token(user.id)
    _set_cookie(resp, token)
    return TokenOut(access_token=token, user=UserOut.model_validate(user))


@router.post("/login", response_model=TokenOut)
def login(body: Credentials, resp: Response, db: Session = Depends(get_db)):
    check_rate(body.email.lower(), "login", 10)
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    if user is None or not verify_password(body.password, user.password_hash):
        raise Unauthorized("Incorrect email or password")
    token = create_token(user.id)
    _set_cookie(resp, token)
    return TokenOut(access_token=token, user=UserOut.model_validate(user))


@router.post("/logout", status_code=204)
def logout(resp: Response):
    resp.delete_cookie(COOKIE_NAME, path="/")


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(current_user)):
    return user


@router.get("/preferences")
def get_preferences(user: User = Depends(current_user)):
    """Answer preferences used by all your assignments (defaults filled in)."""
    from app.ai.prompts import AnswerPreferences

    return AnswerPreferences.model_validate(user.answer_preferences or {}).model_dump()


@router.put("/preferences")
def put_preferences(body: AnswerPrefsIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Set the answer preferences for all your assignments. The subject stays per assignment."""
    from app.ai.prompts import AnswerPreferences

    u = db.merge(user)
    u.answer_preferences = {**(u.answer_preferences or {}), **body.model_dump(exclude_none=True)}
    db.commit()
    return AnswerPreferences.model_validate(u.answer_preferences).model_dump()


TASK_INFO = {
    "answer": ("Writing answers", "Most questions."),
    "answer_hard": ("Hard answers", "Questions with figures, maths or calculations, 15+ marks, or detailed answers."),
    "transcribe": ("Reading images", "Scanned assignment pages and your handwriting photos."),
    "verify": ("Checking letters", "Confirms each letter cut out of your handwriting (results are cached)."),
}


def _routing_out(user: User) -> dict:
    from app.ai.pricing import price_for
    from app.ai.providers import TASKS, default_task_model, model_choices, task_model

    chosen = user.model_routing or {}
    choices = []
    for m in model_choices():
        p = price_for(m)
        choices.append(
            {
                "model": m,
                "input_per_mtok": p.input_per_mtok if p else None,
                "output_per_mtok": p.output_per_mtok if p else None,
            }
        )
    return {
        "tasks": [
            {
                "task": t,
                "label": TASK_INFO[t][0],
                "description": TASK_INFO[t][1],
                "model": task_model(t, chosen),
                "default": default_task_model(t),
                "custom": chosen.get(t) == task_model(t, chosen),
            }
            for t in TASKS
        ],
        "choices": choices,
        "fallback": get_settings().llm_model,
    }


@router.get("/model-routing", response_model=ModelRoutingOut)
def get_model_routing(user: User = Depends(current_user)):
    """Which AI model does each kind of work for your account."""
    return _routing_out(user)


@router.put("/model-routing", response_model=ModelRoutingOut)
def put_model_routing(body: ModelRoutingIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Choose a model per task; null or an empty string goes back to the server default."""
    from app.ai.providers import TASKS, model_choices

    allowed = set(model_choices())
    routing = dict(user.model_routing or {})
    for task, model in body.routing.items():
        if task not in TASKS:
            raise AppError(f"Unknown task: {task}", code="validation_error", status_code=422)
        if not model:
            routing.pop(task, None)
        elif model not in allowed:
            raise AppError(f"Model not available: {model}", code="validation_error", status_code=422)
        else:
            routing[task] = model
    u = db.merge(user)
    u.model_routing = routing
    db.commit()
    return _routing_out(u)


@router.delete("/me", status_code=204)
def delete_me(resp: Response, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Delete the account and every file, profile, assignment and export it owns."""
    delete_user(db, db.merge(user))
    resp.delete_cookie(COOKIE_NAME, path="/")
