"""Audit depth profiles — which stages run."""

from __future__ import annotations

import os
from enum import StrEnum

from auditor.contracts.enums import JobStage


class AuditProfile(StrEnum):
    """How far the pipeline goes."""

    STATIC = "static"
    """compile → static → metamorphic → finalize"""

    DEFAULT = "default"
    """static + llm_tests (if key) + forge fuzz"""

    DEEP = "deep"
    """default + echidna when available/properties exist"""


# Stage names as registry keys (may match JobStage values).
STAGE_MATERIALIZE = "materialize"
STAGE_COMPILE = "compile"
STAGE_STATIC = "static"
STAGE_METAMORPHIC = "metamorphic"
STAGE_LLM_TESTS = "llm_tests"
STAGE_FORGE_FUZZ = "fuzz"
STAGE_ECHIDNA = "echidna"
STAGE_FINALIZE = "finalize"
STAGE_MYTHRIL = "mythril"  # plugin, not in default profiles


def profile_from_env() -> AuditProfile:
    raw = os.environ.get("AUDIT_PROFILE", AuditProfile.DEFAULT.value)
    try:
        return AuditProfile(raw.strip().lower())
    except ValueError as exc:
        allowed = ", ".join(p.value for p in AuditProfile)
        msg = f"AUDIT_PROFILE must be one of: {allowed} (got {raw!r})"
        raise ValueError(msg) from exc


def stages_for_profile(profile: AuditProfile) -> list[str]:
    """Ordered stage keys for a profile (optional stages still registered; runner may skip)."""
    base = [
        STAGE_MATERIALIZE,
        STAGE_COMPILE,
        STAGE_STATIC,
        STAGE_METAMORPHIC,
    ]
    if profile is AuditProfile.STATIC:
        return [*base, STAGE_FINALIZE]
    if profile is AuditProfile.DEFAULT:
        return [*base, STAGE_LLM_TESTS, STAGE_FORGE_FUZZ, STAGE_FINALIZE]
    # deep
    return [*base, STAGE_LLM_TESTS, STAGE_FORGE_FUZZ, STAGE_ECHIDNA, STAGE_FINALIZE]


def job_stage_for_key(key: str) -> JobStage | None:
    mapping = {
        STAGE_MATERIALIZE: JobStage.MATERIALIZE,
        STAGE_COMPILE: JobStage.COMPILE,
        STAGE_STATIC: JobStage.STATIC,
        STAGE_METAMORPHIC: JobStage.METAMORPHIC,
        STAGE_LLM_TESTS: JobStage.LLM_TESTS,
        STAGE_FORGE_FUZZ: JobStage.FUZZ,
        STAGE_ECHIDNA: JobStage.ECHIDNA,
        STAGE_FINALIZE: JobStage.FINALIZE,
    }
    return mapping.get(key)
