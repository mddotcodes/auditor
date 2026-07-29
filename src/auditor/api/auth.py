"""Optional API token auth for cloud embedding."""

from __future__ import annotations

from typing import Annotated

from fastapi import Header, HTTPException, Request, status


def require_api_token(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    x_api_token: Annotated[str | None, Header(alias="X-API-Token")] = None,
) -> None:
    """If ``AUDIT_API_TOKEN`` is configured, require Bearer or X-API-Token."""
    auditor_state = getattr(request.app.state, "auditor_state", None)
    expected: str | None = getattr(auditor_state, "api_token", None) if auditor_state else None
    if not expected:
        return
    provided: str | None = None
    if x_api_token:
        provided = x_api_token.strip()
    elif authorization:
        parts = authorization.split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            provided = parts[1].strip()
        else:
            provided = authorization.strip()
    if not provided or provided != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": {"code": "unauthorized", "message": "Invalid or missing API token"}},
        )
