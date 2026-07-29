"""Bytecode fingerprint + Sourcify-oriented metadata."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from auditor.contracts.manifest import CompilerSettings, Fingerprint, FingerprintContract

_CBOR_MARKER = bytes.fromhex("a264")  # rough; solc metadata often ends with cbor + solc length


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_hex(path.read_bytes())


def extract_metadata_hash(runtime_bytecode_hex: str) -> str | None:
    """Best-effort CBOR metadata hash from runtime bytecode suffix.

    Solc appends CBOR-encoded metadata and a 2-byte length. We return a hex
    digest of the trailing metadata section when present.
    """
    h = runtime_bytecode_hex.lower().removeprefix("0x")
    if len(h) < 4:
        return None
    try:
        raw = bytes.fromhex(h)
    except ValueError:
        return None
    if len(raw) < 2:
        return None
    meta_len = int.from_bytes(raw[-2:], "big")
    if meta_len <= 0 or meta_len + 2 > len(raw):
        return None
    meta = raw[-(meta_len + 2) : -2]
    return "0x" + sha256_hex(meta)


def build_fingerprint(
    project_dir: Path,
    *,
    bytecode_dir: Path | None = None,
    solc_version: str | None = None,
) -> Fingerprint:
    """Collect per-contract hashes and compiler settings."""
    settings = CompilerSettings(
        solc_version=solc_version or _solc_from_toml(project_dir),
        optimizer_enabled=_toml_bool(project_dir, "optimizer"),
        optimizer_runs=_toml_int(project_dir, "optimizer_runs"),
        via_ir=_toml_bool(project_dir, "via_ir"),
    )
    contracts: list[FingerprintContract] = []
    bc_dir = bytecode_dir
    if bc_dir and bc_dir.is_dir():
        runtime_map: dict[str, Path] = {}
        creation_map: dict[str, Path] = {}
        for p in bc_dir.glob("*.hex"):
            if p.name.endswith(".runtime.hex"):
                runtime_map[p.name[: -len(".runtime.hex")]] = p
            elif p.name.endswith(".creation.hex"):
                creation_map[p.name[: -len(".creation.hex")]] = p
        for name in sorted(set(runtime_map) | set(creation_map)):
            rt = runtime_map.get(name)
            cr = creation_map.get(name)
            rt_hex = rt.read_text(encoding="utf-8").strip() if rt else None
            cr_hex = cr.read_text(encoding="utf-8").strip() if cr else None
            source_path = _guess_source(project_dir, name)
            contracts.append(
                FingerprintContract(
                    name=name,
                    source_path=source_path or f"src/{name}.sol",
                    creation_bytecode_sha256=sha256_hex(cr_hex.encode()) if cr_hex else None,
                    runtime_bytecode_sha256=sha256_hex(rt_hex.encode()) if rt_hex else None,
                    metadata_hash=extract_metadata_hash(rt_hex) if rt_hex else None,
                )
            )

    sources_blob = _canonical_sources_blob(project_dir)
    return Fingerprint(
        compiler=settings,
        contracts=contracts,
        sources_sha256=sha256_hex(sources_blob) if sources_blob else None,
    )


def sourcify_oriented_meta(fp: Fingerprint, project_dir: Path) -> dict[str, Any]:
    """Fields useful for external Sourcify-style verification (no network)."""
    files: dict[str, str] = {}
    src = project_dir / "src"
    if src.is_dir():
        for path in sorted(src.rglob("*")):
            if path.is_file():
                rel = path.relative_to(project_dir).as_posix()
                files[rel] = sha256_file(path)
    return {
        "compiler_version": fp.compiler.solc_version,
        "optimizer": {
            "enabled": fp.compiler.optimizer_enabled,
            "runs": fp.compiler.optimizer_runs,
        },
        "via_ir": fp.compiler.via_ir,
        "sources_sha256": fp.sources_sha256,
        "file_digests": files,
        "contracts": [c.model_dump(mode="json") for c in fp.contracts],
    }


def _canonical_sources_blob(project_dir: Path) -> bytes:
    src = project_dir / "src"
    if not src.is_dir():
        return b""
    parts: list[bytes] = []
    for path in sorted(src.rglob("*.sol")):
        rel = path.relative_to(project_dir).as_posix().encode()
        parts.append(rel + b"\0" + path.read_bytes() + b"\0")
    return b"".join(parts)


def _guess_source(project_dir: Path, contract_name: str) -> str | None:
    src = project_dir / "src"
    if not src.is_dir():
        return None
    direct = src / f"{contract_name}.sol"
    if direct.is_file():
        return direct.relative_to(project_dir).as_posix()
    for path in src.rglob("*.sol"):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if re.search(rf"\bcontract\s+{re.escape(contract_name)}\b", text):
            return path.relative_to(project_dir).as_posix()
    return None


def _solc_from_toml(project_dir: Path) -> str | None:
    return _toml_str(project_dir, "solc_version")


def _toml_str(project_dir: Path, key: str) -> str | None:
    path = project_dir / "foundry.toml"
    if not path.is_file():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        if key in line and "=" in line:
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def _toml_bool(project_dir: Path, key: str) -> bool | None:
    raw = _toml_str(project_dir, key)
    if raw is None:
        return None
    return raw.lower() in {"true", "1", "yes"}


def _toml_int(project_dir: Path, key: str) -> int | None:
    raw = _toml_str(project_dir, key)
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None
