"""Dependency and remapping policy for offline-friendly Foundry projects."""

from __future__ import annotations

from auditor.deps.policy import DependencyMode, DependencyPolicy
from auditor.deps.remappings import (
    KNOWN_PACKAGES,
    KnownPackage,
    default_remappings,
    merge_remappings,
)
from auditor.deps.vendor import VendorResult, apply_default_vendor_libs, list_bundled_packs

__all__ = [
    "KNOWN_PACKAGES",
    "DependencyMode",
    "DependencyPolicy",
    "KnownPackage",
    "VendorResult",
    "apply_default_vendor_libs",
    "default_remappings",
    "list_bundled_packs",
    "merge_remappings",
]
