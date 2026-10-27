from fastapi import APIRouter, Depends, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.errors import AppError, Conflict, Unauthorized
from app.models import User
from app.ratelimit import check_rate
from app.schemas import Credentials, TokenOut, UserOut
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


@router.delete("/me", status_code=204)
def delete_me(resp: Response, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Delete the account and every file, profile, assignment and export it owns."""
    delete_user(db, db.merge(user))
    resp.delete_cookie(COOKIE_NAME, path="/")
