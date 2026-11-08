"""Log in and see who you are."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm

from app.auth import CurrentUser, authenticate, create_token, current_user
from app.deps import DbSession, get_settings_dep
from app.schemas import Me, Token
from app.settings import Settings
from core.actions.service import audit

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/token")
def login(
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
    session: DbSession,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> Token:
    user = authenticate(session, form.username, form.password)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "wrong username or password")
    audit(session, user.username, "user.login", "user", user.id)
    return Token(access_token=create_token(user, settings))


@router.get("/me")
def me(user: Annotated[CurrentUser, Depends(current_user)]) -> Me:
    return Me.model_validate(user.model_dump())
