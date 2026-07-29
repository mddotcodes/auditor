"""LLM helpers for test generation and compile auto-fix."""

from auditor.pipeline.llm.client import (
    complete_text,
    dual_mode_enabled,
    llm_available,
    model_for_role,
)

__all__ = [
    "complete_text",
    "dual_mode_enabled",
    "llm_available",
    "model_for_role",
]
