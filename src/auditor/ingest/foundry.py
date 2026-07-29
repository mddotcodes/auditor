"""Foundry project skeleton and safe ``foundry.toml`` handling."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any, Final

from auditor.ingest.errors import InvalidFoundryConfigError
from auditor.ingest.paths import write_text_under_project

FOUNDRY_DIR_NAMES: Final[tuple[str, ...]] = ("src", "test", "lib", "script")

# Path-like fields we validate for absolute / traversal / shell metacharacters.
_STRICT_PATH_KEYS: Final[frozenset[str]] = frozenset(
    {
        "src",
        "test",
        "script",
        "out",
        "cache_path",
        "broadcast",
        "libs",
        "include_paths",
    }
)

_ABS_PATH_RE: Final[re.Pattern[str]] = re.compile(
    r"""(?x)
    ^(?:
        /              # posix absolute
      | [A-Za-z]:[/\\] # windows drive
      | ~              # home
      | \\\\           # unc
    )
    """
)

_SHELL_META_RE: Final[re.Pattern[str]] = re.compile(r"""[`$]|\$\(|\$\{|;\s*\w|\|\||&&""")


def ensure_foundry_dirs(project_dir: Path) -> None:
    """Create standard Foundry directories under ``project_dir`` (idempotent)."""
    project_dir.mkdir(parents=True, exist_ok=True)
    for name in FOUNDRY_DIR_NAMES:
        (project_dir / name).mkdir(parents=True, exist_ok=True)


def render_foundry_toml(*, solc_version: str | None = None) -> str:
    """Render a minimal safe ``foundry.toml`` for offline jobs."""
    lines = [
        "[profile.default]",
        'src = "src"',
        'out = "out"',
        'libs = ["lib"]',
        'test = "test"',
        'script = "script"',
        # Offline by default; remote install is M3.2 policy.
        "auto_detect_solc = true",
        "offline = true",
    ]
    if solc_version is not None:
        # When we have a concrete pin, set it and keep auto_detect as fallback
        # is unnecessary — Foundry uses solc_version when set.
        lines.append(f'solc_version = "{solc_version}"')
    lines.append("")
    return "\n".join(lines)


def _is_unsafe_path_value(value: str) -> bool:
    text = value.strip()
    if not text:
        return True
    if "\x00" in text:
        return True
    if ".." in Path(text).parts or ".." in text.replace("\\", "/").split("/"):
        return True
    if _ABS_PATH_RE.match(text):
        return True
    return bool(_SHELL_META_RE.search(text))


def _validate_path_like(key: str, value: Any, *, path: str) -> None:
    if isinstance(value, str):
        if _is_unsafe_path_value(value):
            msg = f"unsafe path value for {key!r} in {path}: {value!r}"
            raise InvalidFoundryConfigError(msg)
        return
    if isinstance(value, list):
        for item in value:
            if not isinstance(item, str):
                msg = f"expected string entries for {key!r} in {path}"
                raise InvalidFoundryConfigError(msg)
            if _is_unsafe_path_value(item):
                msg = f"unsafe path value for {key!r} in {path}: {item!r}"
                raise InvalidFoundryConfigError(msg)
        return
    msg = f"unexpected type for path key {key!r} in {path}: {type(value).__name__}"
    raise InvalidFoundryConfigError(msg)


def _walk_table(table: dict[str, Any], *, path: str) -> None:
    for key, value in table.items():
        if isinstance(value, dict):
            _walk_table(value, path=f"{path}.{key}" if path else key)
            continue
        if key in _STRICT_PATH_KEYS:
            _validate_path_like(key, value, path=path or "root")
        elif key == "remappings" and isinstance(value, list):
            # Remappings are "alias/=path/" — reject absolute / traversal targets.
            for item in value:
                if not isinstance(item, str):
                    msg = f"remappings entries must be strings in {path}"
                    raise InvalidFoundryConfigError(msg)
                if "\x00" in item or _SHELL_META_RE.search(item):
                    msg = f"unsafe remapping in {path}: {item!r}"
                    raise InvalidFoundryConfigError(msg)
                # Split on first "=" if present.
                target = item.split("=", 1)[-1] if "=" in item else item
                if _is_unsafe_path_value(target):
                    msg = f"unsafe remapping target in {path}: {item!r}"
                    raise InvalidFoundryConfigError(msg)
        elif isinstance(value, str) and _SHELL_META_RE.search(value):
            msg = f"shell metacharacters not allowed in foundry.toml {path}.{key}: {value!r}"
            raise InvalidFoundryConfigError(msg)


def validate_foundry_toml(content: str) -> dict[str, Any]:
    """Parse and safety-check a user-supplied ``foundry.toml``.

    Rejects absolute paths, ``..`` traversal in path fields, and obvious shell
    metacharacters in string values. Does not reimplement remapping policy
    (M3.2); only hard safety gates.
    """
    if "\x00" in content:
        msg = "foundry.toml must not contain NUL bytes"
        raise InvalidFoundryConfigError(msg)

    # Bound size is enforced by ingest limits; still refuse absurd configs.
    try:
        data = tomllib.loads(content)
    except tomllib.TOMLDecodeError as exc:
        msg = f"invalid foundry.toml: {exc}"
        raise InvalidFoundryConfigError(msg) from exc

    _walk_table(data, path="")
    return data


def write_foundry_toml(
    project_dir: Path,
    *,
    solc_version: str | None = None,
    user_toml: str | None = None,
) -> str:
    """Write ``foundry.toml`` under ``project_dir``.

    If ``user_toml`` is provided it is validated and written as-is (user paths
    preserved). Otherwise a minimal offline skeleton is generated, optionally
    pinning ``solc_version``.

    Returns the relative path written (always ``foundry.toml``).
    """
    if user_toml is not None:
        validate_foundry_toml(user_toml)
        # If user did not set solc and we have a pin, we do not silently rewrite
        # their file — remapping/solc merge is M3.2. Documented in docs/ingest.md.
        content = user_toml if user_toml.endswith("\n") else user_toml + "\n"
    else:
        content = render_foundry_toml(solc_version=solc_version)

    write_text_under_project(project_dir, "foundry.toml", content)
    return "foundry.toml"
