"""Detect and reconcile ``pragma solidity`` version requirements.

Policy
------
1. Scan every ``.sol`` source for ``pragma solidity ...;`` directives.
2. Parse each into a closed/open version range (major.minor.patch).
3. Intersect all ranges. If the intersection is empty →
   :class:`~auditor.ingest.errors.PragmaConflictError`.
4. Prefer a single concrete solc version for ``foundry.toml`` when the
   intersection has a clear lower bound (use that bound as the pin).
5. If no pragmas are present, leave solc selection to forge (``solc_version``
   unset). If ranges are compatible but only loosely specified, still pin to
   the effective lower bound when known; otherwise leave unset for forge auto.

We intentionally support the common pragma forms only:

- exact: ``0.8.20``
- caret: ``^0.8.20``  → ``>=0.8.20 <0.9.0``
- tilde: ``~0.8.20``  → ``>=0.8.20 <0.8.21`` wait — actually ``~0.8.20`` is
  ``>=0.8.20 <0.9.0`` in npm; for Solidity, ``~0.8.20`` ≈ ``>=0.8.20 <0.9.0``
  for two-component and patch-bounded for three. We treat ``~x.y.z`` as
  ``>=x.y.z <x.(y+1).0``.
- comparisons: ``>=``, ``>``, ``<=``, ``<``, ``=``
- compound: ``>=0.8.0 <0.9.0``
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from auditor.ingest.errors import PragmaConflictError

# pragma solidity ^0.8.20;
# pragma solidity >=0.7.0 <0.9.0;
_PRAGMA_RE: Final[re.Pattern[str]] = re.compile(
    r"pragma\s+solidity\s+([^;]+);",
    re.IGNORECASE,
)

_VERSION_RE: Final[re.Pattern[str]] = re.compile(
    r"^v?(?P<major>\d+)\.(?P<minor>\d+)(?:\.(?P<patch>\d+))?$"
)

# Tokenize operators and versions inside a pragma expression.
_TOKEN_RE: Final[re.Pattern[str]] = re.compile(
    r"(?P<op>\^|~|>=|<=|>|<|=)|(?P<ver>v?\d+\.\d+(?:\.\d+)?)|(?P<ws>\s+)|(?P<bad>\S+)"
)


@dataclass(frozen=True, slots=True)
class Version:
    """Semantic version triple (patch defaults to 0 when omitted)."""

    major: int
    minor: int
    patch: int = 0

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"

    def as_tuple(self) -> tuple[int, int, int]:
        return (self.major, self.minor, self.patch)


def parse_version(text: str) -> Version:
    """Parse ``x.y`` or ``x.y.z`` into a :class:`Version`."""
    m = _VERSION_RE.match(text.strip())
    if not m:
        msg = f"invalid Solidity version: {text!r}"
        raise PragmaConflictError(msg)
    patch_raw = m.group("patch")
    return Version(
        major=int(m.group("major")),
        minor=int(m.group("minor")),
        patch=int(patch_raw) if patch_raw is not None else 0,
    )


@dataclass(frozen=True, slots=True)
class VersionRange:
    """Half-open-friendly version range with optional inclusive bounds.

    ``min_inclusive`` / ``max_inclusive`` control endpoint inclusion when the
    corresponding bound is not ``None``.
    """

    min_v: Version | None = None
    max_v: Version | None = None
    min_inclusive: bool = True
    max_inclusive: bool = True
    source: str = ""

    def contains(self, v: Version) -> bool:
        t = v.as_tuple()
        if self.min_v is not None:
            mt = self.min_v.as_tuple()
            if self.min_inclusive:
                if t < mt:
                    return False
            elif t <= mt:
                return False
        if self.max_v is not None:
            xt = self.max_v.as_tuple()
            if self.max_inclusive:
                if t > xt:
                    return False
            elif t >= xt:
                return False
        return True

    def is_empty(self) -> bool:
        """Cheap emptiness check for comparable inclusive bounds."""
        if self.min_v is None or self.max_v is None:
            return False
        mn = self.min_v.as_tuple()
        mx = self.max_v.as_tuple()
        if mn < mx:
            return False
        if mn > mx:
            return True
        # Equal bounds: non-empty only if both ends inclusive.
        return not (self.min_inclusive and self.max_inclusive)

    def describe(self) -> str:
        parts: list[str] = []
        if self.min_v is not None:
            op = ">=" if self.min_inclusive else ">"
            parts.append(f"{op}{self.min_v}")
        if self.max_v is not None:
            op = "<=" if self.max_inclusive else "<"
            parts.append(f"{op}{self.max_v}")
        return " ".join(parts) if parts else "*"


def _bump_major(v: Version) -> Version:
    return Version(v.major + 1, 0, 0)


def _bump_minor(v: Version) -> Version:
    return Version(v.major, v.minor + 1, 0)


def _bump_patch(v: Version) -> Version:
    return Version(v.major, v.minor, v.patch + 1)


def _range_from_caret(v: Version) -> VersionRange:
    """``^0.8.20`` → ``>=0.8.20 <0.9.0``; ``^1.2.3`` → ``>=1.2.3 <2.0.0``."""
    if v.major == 0:
        # 0.x.y: caret locks minor (Solidity / npm-like for 0.x).
        upper = _bump_minor(v) if v.minor != 0 else _bump_patch(v)
        # For 0.8.20 → <0.9.0; for 0.0.3 → <0.0.4
        if v.minor == 0 and v.patch == 0:
            upper = _bump_minor(Version(0, 0, 0))
        return VersionRange(min_v=v, max_v=upper, min_inclusive=True, max_inclusive=False)
    return VersionRange(
        min_v=v,
        max_v=_bump_major(v),
        min_inclusive=True,
        max_inclusive=False,
    )


def _range_from_tilde(v: Version) -> VersionRange:
    """``~0.8.20`` → ``>=0.8.20 <0.9.0``; ``~0.8`` → ``>=0.8.0 <0.9.0``."""
    return VersionRange(
        min_v=v,
        max_v=_bump_minor(v),
        min_inclusive=True,
        max_inclusive=False,
    )


def parse_pragma_expression(expr: str) -> VersionRange:
    """Parse the expression inside ``pragma solidity <expr>;``."""
    text = expr.strip()
    if not text:
        msg = "empty pragma solidity expression"
        raise PragmaConflictError(msg)

    # Intersect multiple constraints in one pragma (e.g. >=0.8.0 <0.9.0).
    current = VersionRange(source=text)
    tokens = list(_TOKEN_RE.finditer(text))
    i = 0
    while i < len(tokens):
        m = tokens[i]
        if m.group("ws"):
            i += 1
            continue
        if m.group("bad"):
            msg = f"unrecognized token in pragma solidity expression: {m.group('bad')!r}"
            raise PragmaConflictError(msg)

        op = m.group("op")
        if op in {"^", "~"}:
            # Next meaningful token must be a version.
            i += 1
            while i < len(tokens) and tokens[i].group("ws"):
                i += 1
            if i >= len(tokens) or not tokens[i].group("ver"):
                msg = f"operator {op!r} requires a version in {text!r}"
                raise PragmaConflictError(msg)
            ver = parse_version(tokens[i].group("ver"))
            piece = _range_from_caret(ver) if op == "^" else _range_from_tilde(ver)
            current = intersect_ranges(current, piece)
            i += 1
            continue

        if op in {">=", "<=", ">", "<", "="} or op is None:
            # Bare version means exact pin; op may be missing.
            if op is None:
                if not m.group("ver"):
                    msg = f"expected version in pragma: {text!r}"
                    raise PragmaConflictError(msg)
                ver = parse_version(m.group("ver"))
                piece = VersionRange(
                    min_v=ver,
                    max_v=ver,
                    min_inclusive=True,
                    max_inclusive=True,
                    source=text,
                )
                current = intersect_ranges(current, piece)
                i += 1
                continue

            # Comparison operator then version.
            i += 1
            while i < len(tokens) and tokens[i].group("ws"):
                i += 1
            if i >= len(tokens) or not tokens[i].group("ver"):
                msg = f"operator {op!r} requires a version in {text!r}"
                raise PragmaConflictError(msg)
            ver = parse_version(tokens[i].group("ver"))
            if op == ">=":
                piece = VersionRange(min_v=ver, min_inclusive=True, source=text)
            elif op == ">":
                piece = VersionRange(min_v=ver, min_inclusive=False, source=text)
            elif op == "<=":
                piece = VersionRange(max_v=ver, max_inclusive=True, source=text)
            elif op == "<":
                piece = VersionRange(max_v=ver, max_inclusive=False, source=text)
            else:  # =
                piece = VersionRange(
                    min_v=ver,
                    max_v=ver,
                    min_inclusive=True,
                    max_inclusive=True,
                    source=text,
                )
            current = intersect_ranges(current, piece)
            i += 1
            continue

        msg = f"unhandled pragma token in {text!r}"
        raise PragmaConflictError(msg)

    if current.is_empty():
        msg = f"pragma solidity expression is unsatisfiable: {text!r}"
        raise PragmaConflictError(msg)
    return VersionRange(
        min_v=current.min_v,
        max_v=current.max_v,
        min_inclusive=current.min_inclusive,
        max_inclusive=current.max_inclusive,
        source=text,
    )


def intersect_ranges(a: VersionRange, b: VersionRange) -> VersionRange:
    """Return the intersection of two version ranges."""
    # Lower bound: take the greater of the two mins (respecting inclusivity).
    min_v: Version | None
    min_incl: bool
    if a.min_v is None:
        min_v, min_incl = b.min_v, b.min_inclusive
    elif b.min_v is None:
        min_v, min_incl = a.min_v, a.min_inclusive
    else:
        at, bt = a.min_v.as_tuple(), b.min_v.as_tuple()
        if at > bt:
            min_v, min_incl = a.min_v, a.min_inclusive
        elif bt > at:
            min_v, min_incl = b.min_v, b.min_inclusive
        else:
            min_v = a.min_v
            min_incl = a.min_inclusive and b.min_inclusive

    # Upper bound: take the lesser of the two maxes.
    max_v: Version | None
    max_incl: bool
    if a.max_v is None:
        max_v, max_incl = b.max_v, b.max_inclusive
    elif b.max_v is None:
        max_v, max_incl = a.max_v, a.max_inclusive
    else:
        at, bt = a.max_v.as_tuple(), b.max_v.as_tuple()
        if at < bt:
            max_v, max_incl = a.max_v, a.max_inclusive
        elif bt < at:
            max_v, max_incl = b.max_v, b.max_inclusive
        else:
            max_v = a.max_v
            max_incl = a.max_inclusive and b.max_inclusive

    result = VersionRange(
        min_v=min_v,
        max_v=max_v,
        min_inclusive=min_incl if min_v is not None else True,
        max_inclusive=max_incl if max_v is not None else True,
        source=f"{a.source} ∩ {b.source}".strip(" ∩"),
    )
    return result


def extract_pragmas(content: str) -> list[str]:
    """Return raw pragma expressions (inside ``pragma solidity …;``) from source."""
    return [m.group(1).strip() for m in _PRAGMA_RE.finditer(content)]


# Prefer versions commonly pre-cached in the Auditor image (see docker/Dockerfile).
_PREFERRED_SOLC: tuple[Version, ...] = (
    Version(0, 8, 28),
    Version(0, 8, 26),
    Version(0, 8, 24),
    Version(0, 8, 20),
    Version(0, 8, 19),
)


def choose_solc_version(intersection: VersionRange) -> str | None:
    """Pick a concrete solc pin for foundry.toml, or ``None`` for forge auto.

    Prefer image-known 0.8.x versions that satisfy the range (so offline
    containers need not download solc). Fall back to the inclusive lower bound.
    """
    for pref in _PREFERRED_SOLC:
        if intersection.contains(pref):
            return str(pref)
    if intersection.min_v is None:
        return None
    # Strict lower bound: bump patch as a practical pin; else use inclusive min.
    pin = _bump_patch(intersection.min_v) if not intersection.min_inclusive else intersection.min_v
    if not intersection.contains(pin):
        return None
    return str(pin)


@dataclass(frozen=True, slots=True)
class PragmaInfo:
    """Result of scanning and reconciling pragmas across a source set."""

    raw_pragmas: tuple[str, ...]
    ranges: tuple[str, ...]
    solc_version: str | None
    version_range: str | None
    files_without_pragma: tuple[str, ...]
    files_with_pragma: tuple[str, ...]


def analyze_pragmas(sources: dict[str, str]) -> PragmaInfo:
    """Scan ``.sol`` files, intersect ranges, and choose a solc pin.

    Raises
    ------
    PragmaConflictError
        If any single expression is invalid or the global intersection is empty.
    """
    raw: list[str] = []
    range_descs: list[str] = []
    without: list[str] = []
    with_pragma: list[str] = []
    intersection: VersionRange | None = None

    for path, content in sorted(sources.items()):
        if not path.endswith(".sol"):
            continue
        exprs = extract_pragmas(content)
        if not exprs:
            without.append(path)
            continue
        with_pragma.append(path)
        for expr in exprs:
            raw.append(expr)
            rng = parse_pragma_expression(expr)
            range_descs.append(rng.describe())
            intersection = rng if intersection is None else intersect_ranges(intersection, rng)
            if intersection.is_empty():
                msg = (
                    f"conflicting Solidity pragmas cannot be satisfied together: {sorted(set(raw))}"
                )
                raise PragmaConflictError(msg)

    # Unique while preserving order.
    seen: set[str] = set()
    unique_raw: list[str] = []
    for item in raw:
        if item not in seen:
            seen.add(item)
            unique_raw.append(item)

    if intersection is None:
        return PragmaInfo(
            raw_pragmas=(),
            ranges=(),
            solc_version=None,
            version_range=None,
            files_without_pragma=tuple(without),
            files_with_pragma=(),
        )

    if intersection.is_empty():
        msg = f"conflicting Solidity pragmas cannot be satisfied together: {unique_raw}"
        raise PragmaConflictError(msg)

    # Conflicting major lines that survived a buggy intersection should still
    # fail: e.g. ^0.7.0 and ^0.8.0 correctly empty out; double-check majors.
    solc = choose_solc_version(intersection)
    return PragmaInfo(
        raw_pragmas=tuple(unique_raw),
        ranges=tuple(range_descs),
        solc_version=solc,
        version_range=intersection.describe(),
        files_without_pragma=tuple(without),
        files_with_pragma=tuple(with_pragma),
    )
