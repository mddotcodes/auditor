"""Install engine-bundled vendor packs into a Foundry project ``lib/`` tree."""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from auditor.deps.policy import DependencyPolicy
from auditor.deps.remappings import KNOWN_PACKAGES, default_remappings, merge_remappings

# Repo layout: vendor/ at repository root (development) or next to installed package.
_PACKAGE_ROOT = Path(__file__).resolve().parents[2]  # .../src
_REPO_ROOT = Path(__file__).resolve().parents[3]  # repo root when editable


def vendor_root() -> Path:
    """Locate the ``vendor/`` directory containing bundled packs.

    Search order:
    1. ``AUDIT_VENDOR_ROOT`` env
    2. Repository root ``vendor/`` (editable / Docker build context)
    3. ``src/../vendor`` fallback
    """
    import os

    env = os.environ.get("AUDIT_VENDOR_ROOT")
    if env:
        path = Path(env).expanduser().resolve()
        if path.is_dir():
            return path
        msg = f"AUDIT_VENDOR_ROOT={env!r} is not a directory"
        raise FileNotFoundError(msg)

    for candidate in (_REPO_ROOT / "vendor", _PACKAGE_ROOT.parent / "vendor"):
        if candidate.is_dir():
            return candidate.resolve()

    msg = (
        "Bundled vendor directory not found. Set AUDIT_VENDOR_ROOT or install "
        "packs under vendor/ at the repository root. See docs/dependencies.md."
    )
    raise FileNotFoundError(msg)


def list_bundled_packs(root: Path | None = None) -> list[str]:
    """Return pack directory names present under the vendor root."""
    base = root if root is not None else vendor_root()
    return sorted(p.name for p in base.iterdir() if p.is_dir() and not p.name.startswith("."))


@dataclass(slots=True)
class VendorResult:
    """Outcome of applying vendor packs to a project."""

    installed: list[str] = field(default_factory=list)
    skipped_existing: list[str] = field(default_factory=list)
    remappings: list[str] = field(default_factory=list)
    lib_dir: Path | None = None


def _copy_pack(src: Path, dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest, symlinks=False, dirs_exist_ok=False)


def _write_remappings_txt(project_dir: Path, remappings: list[str]) -> None:
    path = project_dir / "remappings.txt"
    path.write_text("\n".join(remappings) + ("\n" if remappings else ""), encoding="utf-8")


def _update_foundry_toml_remappings(project_dir: Path, remappings: list[str]) -> None:
    """Ensure ``foundry.toml`` exists and contains the remappings list.

    Minimal TOML writer: preserves a simple file we own; if user supplied a complex
    foundry.toml, we only append/replace a ``remappings = [...]`` assignment via
    a dedicated block rewrite of known keys when the file is engine-generated.
    """
    path = project_dir / "foundry.toml"
    block = _format_remappings_toml(remappings)
    if not path.is_file():
        path.write_text(
            "\n".join(
                [
                    "[profile.default]",
                    'src = "src"',
                    'out = "out"',
                    'libs = ["lib"]',
                    'solc_version = "0.8.28"',
                    "optimizer = true",
                    "optimizer_runs = 200",
                    block,
                    "",
                ]
            ),
            encoding="utf-8",
        )
        return

    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    out: list[str] = []
    skipping = False
    replaced = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("remappings"):
            # skip multi-line array
            if "[" in stripped and "]" not in stripped:
                skipping = True
                if not replaced:
                    out.append(block)
                    replaced = True
                continue
            if not replaced:
                out.append(block)
                replaced = True
            skipping = False
            continue
        if skipping:
            if "]" in stripped:
                skipping = False
            continue
        out.append(line)
    if not replaced:
        # insert after [profile.default] if present
        inserted = False
        final: list[str] = []
        for line in out:
            final.append(line)
            if line.strip() == "[profile.default]" and not inserted:
                final.append(block)
                inserted = True
        if not inserted:
            final.extend(["", "[profile.default]", block])
        out = final
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def _format_remappings_toml(remappings: list[str]) -> str:
    if not remappings:
        return "remappings = []"
    inner = ", ".join(f'"{r}"' for r in remappings)
    return f"remappings = [{inner}]"


def apply_default_vendor_libs(
    project_dir: Path | str,
    *,
    policy: DependencyPolicy | None = None,
    packs: tuple[str, ...] | None = None,
    force: bool = False,
) -> VendorResult:
    """Copy bundled vendor packs into ``project_dir/lib`` and write remappings.

    Parameters
    ----------
    project_dir:
        Foundry project root (contains ``src/``, will gain ``lib/``).
    policy:
        Dependency policy; defaults to :meth:`DependencyPolicy.from_env`.
    packs:
        Override which packs to install; default from policy.
    force:
        Replace existing ``lib/<pack>`` directories.
    """
    project = Path(project_dir)
    project.mkdir(parents=True, exist_ok=True)
    cfg = policy if policy is not None else DependencyPolicy.from_env()
    result = VendorResult(lib_dir=project / "lib")

    if not cfg.auto_vendor:
        result.remappings = default_remappings(packs=packs or cfg.auto_vendor_packs)
        return result

    wanted = packs if packs is not None else cfg.auto_vendor_packs
    root = vendor_root()
    lib = project / "lib"
    lib.mkdir(parents=True, exist_ok=True)

    installed_names: list[str] = []
    for name in wanted:
        if name not in KNOWN_PACKAGES:
            msg = f"Unknown vendor pack {name!r}; known: {sorted(KNOWN_PACKAGES)}"
            raise ValueError(msg)
        pkg = KNOWN_PACKAGES[name]
        src = root / name
        if not src.is_dir():
            msg = f"Bundled pack {name!r} missing under {root}"
            raise FileNotFoundError(msg)
        dest = lib / pkg.dir_name
        if dest.exists() and not force:
            result.skipped_existing.append(name)
            installed_names.append(name)
            continue
        _copy_pack(src, dest)
        result.installed.append(name)
        installed_names.append(name)

    # Always include remappings for packs that exist under lib/ (BYO or installed).
    present = [name for name, pkg in KNOWN_PACKAGES.items() if (lib / pkg.dir_name).is_dir()]
    # Prefer installed/wanted order, then any BYO known packs
    ordered = list(dict.fromkeys([*installed_names, *present]))
    result.remappings = merge_remappings(default_remappings(packs=tuple(ordered)))
    _write_remappings_txt(project, result.remappings)
    _update_foundry_toml_remappings(project, result.remappings)
    return result
