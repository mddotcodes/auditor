"""Dependency installation policy — remote installs off by default.

Strict mode keeps the sandbox offline: only engine-bundled vendor packs and
user-supplied ``lib/`` (or sources that already include ``lib/``) are allowed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum
from typing import Final


class DependencyMode(StrEnum):
    """How the engine resolves Solidity library dependencies."""

    STRICT = "strict"
    """No network, no ``forge install``. Bundled vendor + user ``lib/`` only."""

    ALLOWLIST = "allowlist"
    """Future: ``forge install`` only for packages on the allowlist (still off by default)."""

    PERMISSIVE = "permissive"
    """Future: unrestricted remote install — **not implemented**; rejected if selected."""


DEFAULT_MODE: Final[DependencyMode] = DependencyMode.STRICT

# Packages we may auto-copy from the engine ``vendor/`` tree in strict mode.
DEFAULT_AUTO_VENDOR: Final[tuple[str, ...]] = (
    "forge-std",
    "openzeppelin-contracts",
)


def _env_bool(name: str, *, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class DependencyPolicy:
    """Resolved dependency policy for one materialize/compile run."""

    mode: DependencyMode = DEFAULT_MODE
    """Install mode (default :attr:`DependencyMode.STRICT`)."""

    auto_vendor: bool = True
    """Copy bundled packs into ``project/lib/`` when missing."""

    auto_vendor_packs: tuple[str, ...] = DEFAULT_AUTO_VENDOR
    """Which bundled packs to install when ``auto_vendor`` is true."""

    allow_remote_install: bool = False
    """Master switch for ``forge install`` / git. Always false in strict mode."""

    remote_allowlist: tuple[str, ...] = ()
    """Package names permitted if remote install is ever enabled."""

    @classmethod
    def from_env(cls) -> DependencyPolicy:
        """Load policy from environment (container / local)."""
        mode_raw = os.environ.get("AUDIT_DEPENDENCY_MODE", DEFAULT_MODE.value)
        try:
            mode = DependencyMode(mode_raw.strip().lower())
        except ValueError as exc:
            allowed = ", ".join(m.value for m in DependencyMode)
            msg = f"AUDIT_DEPENDENCY_MODE must be one of: {allowed} (got {mode_raw!r})"
            raise ValueError(msg) from exc

        if mode is DependencyMode.PERMISSIVE:
            msg = (
                "AUDIT_DEPENDENCY_MODE=permissive is not supported "
                "(would allow unrestricted remote installs)"
            )
            raise ValueError(msg)

        allow_remote = _env_bool("AUDIT_ALLOW_REMOTE_INSTALL", default=False)
        if mode is DependencyMode.STRICT:
            allow_remote = False
        elif mode is DependencyMode.ALLOWLIST and allow_remote:
            # Allowlist remote path is reserved; still refuse until implemented.
            msg = (
                "Remote install allowlist is not implemented yet; "
                "use strict mode with bundled vendor packs or bring-your-own lib/"
            )
            raise ValueError(msg)

        packs_raw = os.environ.get("AUDIT_AUTO_VENDOR_PACKS")
        if packs_raw is None or packs_raw.strip() == "":
            packs = DEFAULT_AUTO_VENDOR
        else:
            packs = tuple(p.strip() for p in packs_raw.split(",") if p.strip())

        return cls(
            mode=mode,
            auto_vendor=_env_bool("AUDIT_AUTO_VENDOR", default=True),
            auto_vendor_packs=packs,
            allow_remote_install=allow_remote,
            remote_allowlist=(),  # populated when allowlist install ships
        )

    def assert_remote_install_allowed(self, package: str) -> None:
        """Raise if a remote install of ``package`` is not permitted."""
        if self.mode is DependencyMode.STRICT or not self.allow_remote_install:
            msg = (
                f"Remote install of {package!r} is disabled "
                f"(mode={self.mode.value}, allow_remote_install={self.allow_remote_install}). "
                "Use bundled vendor packs or supply lib/ in the job sources. "
                "See docs/dependencies.md."
            )
            raise PermissionError(msg)
        if self.mode is DependencyMode.ALLOWLIST and package not in self.remote_allowlist:
            msg = f"Package {package!r} is not on the remote install allowlist"
            raise PermissionError(msg)
