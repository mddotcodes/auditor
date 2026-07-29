"""HTTP/WebSocket control plane for the audit engine."""

from __future__ import annotations

from auditor.api.app import create_app

__all__ = ["create_app"]
