"""FastAPI application factory."""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse, PlainTextResponse, Response

from auditor import __version__
from auditor.api.auth import require_api_token
from auditor.api.serializers import to_artifacts, to_job_status
from auditor.api.state import AppState
from auditor.contracts.enums import JobStatus
from auditor.contracts.jobs import AuditRequest, AuditSubmitResponse
from auditor.observability.logging import configure_logging
from auditor.observability.prometheus import get_metrics, metrics_enabled
from auditor.pipeline.profiles import AuditProfile, profile_from_env

logger = logging.getLogger(__name__)


def get_state(request: Request) -> AppState:
    return request.app.state.auditor_state  # type: ignore[no-any-return]


StateDep = Annotated[AppState, Depends(get_state)]
AuthDep = Annotated[None, Depends(require_api_token)]


def create_app(*, state: AppState | None = None) -> FastAPI:
    """Build the ASGI app (injectable state for tests)."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app_state: AppState = app.state.auditor_state
        logger.info(
            "auditor API starting (max_inflight=%s job_root=%s auth=%s metrics=%s)",
            app_state.max_inflight,
            app_state.job_root,
            bool(app_state.api_token),
            metrics_enabled(),
            extra={
                "max_inflight": app_state.max_inflight,
                "job_root": str(app_state.job_root),
                "auth_enabled": bool(app_state.api_token),
                "metrics_enabled": metrics_enabled(),
            },
        )
        try:
            yield
        finally:
            logger.info("auditor API shutting down")
            app_state.shutdown(wait=True, cancel=True)

    app = FastAPI(
        title="Auditor Execution Engine API",
        version="1.0.0",
        description="Isolated EVM smart contract audit engine",
        lifespan=lifespan,
    )
    app_state = state if state is not None else AppState.from_env()
    app.state.auditor_state = app_state

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/metrics", response_class=PlainTextResponse, include_in_schema=True)
    def prometheus_metrics() -> Response:
        """Prometheus text exposition for cloud scrapers (optional).

        Disabled with ``AUDIT_METRICS_ENABLED=false`` (returns 404).
        Unauthenticated by design so sidecar scrapers need no API token —
        bind the server to a private network in production.
        """
        if not metrics_enabled():
            return JSONResponse(
                status_code=status.HTTP_404_NOT_FOUND,
                content={
                    "error": {
                        "code": "metrics_disabled",
                        "message": "Set AUDIT_METRICS_ENABLED=true to enable /metrics",
                    }
                },
            )
        body = get_metrics().render(version=__version__)
        return Response(
            content=body,
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    @app.post(
        "/v1/audit",
        response_model=AuditSubmitResponse,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def submit_audit(
        body: AuditRequest,
        app_state: StateDep,
        _: AuthDep,
    ) -> AuditSubmitResponse:
        if not app_state.try_acquire_slot():
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "error": {
                        "code": "too_many_jobs",
                        "message": f"Max in-flight jobs ({app_state.max_inflight}) reached",
                    }
                },
            )
        try:
            profile = _resolve_profile()
            ctx = app_state.runner.submit(
                body,
                profile=profile,
                run_inline=False,
            )
            app_state.submit_job(ctx)
        except Exception:
            app_state.release_slot()
            raise
        return AuditSubmitResponse(job_id=ctx.job_id, status=JobStatus.QUEUED)

    @app.get("/v1/jobs/{job_id}")
    def get_job(job_id: str, app_state: StateDep, _: AuthDep) -> Any:
        ctx = app_state.runner.store.get(job_id)
        if ctx is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": "not_found", "message": f"Unknown job {job_id}"}},
            )
        return to_job_status(ctx)

    @app.get("/v1/jobs/{job_id}/artifacts")
    def get_artifacts(job_id: str, app_state: StateDep, _: AuthDep) -> Any:
        ctx = app_state.runner.store.get(job_id)
        if ctx is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": "not_found", "message": f"Unknown job {job_id}"}},
            )
        return to_artifacts(ctx)

    @app.post("/v1/metrics")
    def metrics(body: AuditRequest, _: AuthDep) -> dict[str, Any]:
        from auditor.pipeline.metrics import compute_metrics, metrics_to_dict

        sources = dict(body.sources or {})
        if not sources:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": {
                        "code": "invalid_request",
                        "message": "metrics requires sources map",
                    }
                },
            )
        return metrics_to_dict(compute_metrics(sources=sources))

    from auditor.api.ws import register_ws

    register_ws(app)

    @app.exception_handler(HTTPException)
    async def http_exc_handler(_request: Request, exc: HTTPException) -> JSONResponse:
        detail: Any = exc.detail
        content: Any
        if type(detail) is dict and "error" in detail:
            content = detail
        else:
            content = {"error": {"code": "http_error", "message": str(detail)}}
        return JSONResponse(status_code=exc.status_code, content=content)

    return app


def _resolve_profile() -> AuditProfile:
    try:
        return profile_from_env()
    except ValueError:
        return AuditProfile.DEFAULT


def main() -> None:
    """CLI entry: ``auditor-serve`` / ``python -m auditor.api.app``."""
    import uvicorn

    # Prefer JSON logs in cloud; local text if AUDIT_LOG_FORMAT unset.
    configure_logging()
    host = os.environ.get("AUDIT_HOST", "0.0.0.0")
    port = int(os.environ.get("AUDIT_PORT", "8080"))
    log_level = os.environ.get("AUDIT_LOG_LEVEL", "info")
    uvicorn.run(
        "auditor.api.app:create_app",
        factory=True,
        host=host,
        port=port,
        log_level=log_level,
        # Rely on configure_logging() root handlers (JSON or text).
        log_config=None,
    )


if __name__ == "__main__":
    main()
