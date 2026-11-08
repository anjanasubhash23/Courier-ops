"""Map service-layer errors to HTTP responses."""

from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import HTTPException, status

from core.actions.policy import PolicyViolation
from core.actions.service import Conflict, Forbidden, NotFound


@contextmanager
def service_errors() -> Iterator[None]:
    try:
        yield
    except NotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None
    except Forbidden as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from None
    except Conflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    except PolicyViolation as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
