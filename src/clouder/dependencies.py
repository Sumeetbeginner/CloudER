from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from clouder.config import Settings
from clouder.security import Principal, authenticate

bearer = HTTPBearer(auto_error=False)


def session_for(request: Request) -> Iterator[Session]:
    with request.app.state.db.transaction() as session:
        yield session


SessionDep = Annotated[Session, Depends(session_for, scope="function")]


def principal_for(
    session: SessionDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> Principal:
    if not credentials:
        raise HTTPException(
            401, "A bearer API key is required", headers={"WWW-Authenticate": "Bearer"}
        )
    return authenticate(session, credentials.credentials)


PrincipalDep = Annotated[Principal, Depends(principal_for)]


def settings_for(request: Request) -> Settings:
    return request.app.state.settings  # type: ignore[no-any-return]


SettingsDep = Annotated[Settings, Depends(settings_for)]


def idempotency_key(
    value: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_-]+$"),
    ],
) -> str:
    return value


KeyDep = Annotated[str, Depends(idempotency_key)]
