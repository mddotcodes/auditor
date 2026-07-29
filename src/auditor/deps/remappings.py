"""Known package remappings for Foundry ``foundry.toml`` / ``remappings.txt``."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True, slots=True)
class KnownPackage:
    """A well-known library layout under ``project/lib/<dir_name>/``."""

    name: str
    """Logical package id (allowlist / vendor pack name)."""

    dir_name: str
    """Directory name under ``lib/``."""

    remappings: tuple[str, ...]
    """Foundry remapping strings, e.g. ``forge-std/=lib/forge-std/src/``."""

    import_prefixes: tuple[str, ...]
    """Import path prefixes this pack satisfies (for detection)."""


KNOWN_PACKAGES: Final[dict[str, KnownPackage]] = {
    "forge-std": KnownPackage(
        name="forge-std",
        dir_name="forge-std",
        remappings=("forge-std/=lib/forge-std/src/",),
        import_prefixes=("forge-std/",),
    ),
    "openzeppelin-contracts": KnownPackage(
        name="openzeppelin-contracts",
        dir_name="openzeppelin-contracts",
        remappings=(
            "@openzeppelin/contracts/=lib/openzeppelin-contracts/contracts/",
            "openzeppelin-contracts/=lib/openzeppelin-contracts/contracts/",
        ),
        import_prefixes=("@openzeppelin/contracts/", "openzeppelin-contracts/"),
    ),
}


def default_remappings(*, packs: tuple[str, ...] | None = None) -> list[str]:
    """Return remapping lines for the given pack names (default: all known)."""
    names = packs if packs is not None else tuple(KNOWN_PACKAGES)
    out: list[str] = []
    for name in names:
        pkg = KNOWN_PACKAGES.get(name)
        if pkg is None:
            continue
        out.extend(pkg.remappings)
    return out


def merge_remappings(*groups: list[str] | tuple[str, ...]) -> list[str]:
    """Merge remapping lists, last definition for a prefix wins, stable unique order."""
    by_prefix: dict[str, str] = {}
    order: list[str] = []
    for group in groups:
        for line in group:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            prefix = line.split("=", 1)[0]
            if prefix not in by_prefix:
                order.append(prefix)
            by_prefix[prefix] = line
    return [by_prefix[p] for p in order]


def detect_required_packs(sources: dict[str, str]) -> set[str]:
    """Heuristic: which known packs are referenced by import statements."""
    needed: set[str] = set()
    for content in sources.values():
        for pkg in KNOWN_PACKAGES.values():
            if pkg.name in needed:
                continue
            for prefix in pkg.import_prefixes:
                # match import "prefix... or import 'prefix...
                if f'"{prefix}' in content or f"'{prefix}" in content:
                    needed.add(pkg.name)
                    break
    # forge-std is commonly needed for tests even if not in user sources
    return needed
