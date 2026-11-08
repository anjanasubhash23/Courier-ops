"""Passwords (Argon2), login tokens (JWT) and role checks."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Annotated

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.deps import DbSession, get_settings_dep
from app.settings import Settings
from core.db.models import Role, User

_hasher = PasswordHasher()
_oauth2 = OAuth2PasswordBearer(tokenUrl="/auth/token")
ALGORITHM = "HS256"


class CurrentUser(BaseModel):
    id: int
    username: str
    role: Role


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def authenticate(session: Session, username: str, password: str) -> User | None:
    user = session.scalar(select(User).where(User.username == username, User.is_active))
    if user is None:
        _hasher.hash(password)  # same work as a real check, so timing doesn't reveal which usernames exist
        return None
    try:
        _hasher.verify(user.password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return None
    if _hasher.check_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    return user


def create_token(user: User, settings: Settings) -> str:
    now = datetime.now(UTC)
    claims = {"sub": str(user.id), "iat": now, "exp": now + timedelta(minutes=settings.token_minutes)}
    return jwt.encode(claims, settings.jwt_secret.get_secret_value(), algorithm=ALGORITHM)


def current_user(
    token: Annotated[str, Depends(_oauth2)],
    session: DbSession,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> CurrentUser:
    unauthorized = HTTPException(
        status.HTTP_401_UNAUTHORIZED, "invalid or expired token", headers={"WWW-Authenticate": "Bearer"}
    )
    try:
        claims = jwt.decode(token, settings.jwt_secret.get_secret_value(), algorithms=[ALGORITHM])
        user_id = int(claims["sub"])
    except (jwt.InvalidTokenError, KeyError, ValueError):
        raise unauthorized from None
    # Look the user up on every request, so deactivating someone or changing their role takes effect at once.
    user = session.get(User, user_id)
    if user is None or not user.is_active:
        raise unauthorized
    return CurrentUser(id=user.id, username=user.username, role=user.role)


def require(role: Role) -> Callable[[CurrentUser], CurrentUser]:
    def check(user: Annotated[CurrentUser, Depends(current_user)]) -> CurrentUser:
        if user.role.rank < role.rank:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"needs the {role} role")
        return user

    return check


Viewer = Annotated[CurrentUser, Depends(require(Role.VIEWER))]
Operator = Annotated[CurrentUser, Depends(require(Role.OPERATOR))]
Supervisor = Annotated[CurrentUser, Depends(require(Role.SUPERVISOR))]
Admin = Annotated[CurrentUser, Depends(require(Role.ADMIN))]
