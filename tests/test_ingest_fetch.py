"""Tests for GitHub Gist fetch (M3.3) — no real network."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from auditor.ingest import (
    FetchNotAllowedError,
    GistFetchError,
    InvalidGistUrlError,
    PayloadTooLargeError,
    fetch_gist_sources,
    parse_gist_id,
)
from auditor.security import NetworkPolicy

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeHTTPResponse:
    """Minimal context-manager response for urlopen mocks."""

    def __init__(self, data: bytes, *, status: int = 200) -> None:
        self._buf = io.BytesIO(data)
        self.status = status

    def read(self, n: int = -1) -> bytes:
        return self._buf.read(n)

    def __enter__(self) -> _FakeHTTPResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None


def _gist_api_payload(
    files: dict[str, dict[str, Any]],
    *,
    gist_id: str = "abc123def456",
) -> dict[str, Any]:
    return {
        "id": gist_id,
        "files": files,
    }


def _file_entry(
    filename: str,
    content: str,
    *,
    truncated: bool = False,
    raw_url: str | None = None,
) -> dict[str, Any]:
    encoded = content.encode("utf-8")
    entry: dict[str, Any] = {
        "filename": filename,
        "type": "text/plain",
        "language": "Solidity",
        "size": len(encoded),
        "truncated": truncated,
        "content": "" if truncated else content,
    }
    if raw_url is not None:
        entry["raw_url"] = raw_url
    elif truncated:
        entry["raw_url"] = f"https://gist.githubusercontent.com/u/{filename}/raw"
    return entry


# ---------------------------------------------------------------------------
# parse_gist_id
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://gist.github.com/alice/abc123def456", "abc123def456"),
        ("https://gist.github.com/alice/abc123def456/", "abc123def456"),
        ("https://gist.github.com/alice/abc123def456/raw", "abc123def456"),
        ("https://gist.github.com/alice/abc123def456/raw/main/Foo.sol", "abc123def456"),
        ("http://gist.github.com/alice/ABC123DEF456", "abc123def456"),
        ("https://gist.github.com/abc123def456", "abc123def456"),
        (
            "https://gist.githubusercontent.com/alice/abc123def456/raw/deadbeef/Foo.sol",
            "abc123def456",
        ),
        ("https://api.github.com/gists/abc123def456", "abc123def456"),
        ("abc123def456", "abc123def456"),
    ],
)
def test_parse_gist_id_common_shapes(url: str, expected: str) -> None:
    assert parse_gist_id(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "",
        "   ",
        "https://github.com/alice/repo",
        "https://example.com/gist/abc",
        "not a url",
        "https://gist.github.com/",
        "https://gist.github.com/alice/not-hex-id",
    ],
)
def test_parse_gist_id_rejects_bad(url: str) -> None:
    with pytest.raises(InvalidGistUrlError):
        parse_gist_id(url)


# ---------------------------------------------------------------------------
# Fail closed
# ---------------------------------------------------------------------------


def test_fail_closed_deny_does_not_call_network(tmp_path: Path) -> None:
    with patch("urllib.request.urlopen") as urlopen:
        with pytest.raises(FetchNotAllowedError, match="allow_fetch"):
            fetch_gist_sources(
                "https://gist.github.com/alice/abc123def456",
                dest_dir=tmp_path / "gist",
                network_policy=NetworkPolicy.DENY,
            )
        urlopen.assert_not_called()


def test_fail_closed_allow_llm_does_not_call_network(tmp_path: Path) -> None:
    with patch("urllib.request.urlopen") as urlopen:
        with pytest.raises(FetchNotAllowedError):
            fetch_gist_sources(
                "https://gist.github.com/alice/abc123def456",
                dest_dir=tmp_path / "cache",
                network_policy=NetworkPolicy.ALLOW_LLM,
            )
        urlopen.assert_not_called()


# ---------------------------------------------------------------------------
# Happy path + cache
# ---------------------------------------------------------------------------


def test_fetch_multi_file_gist_writes_cache_and_sources_map(tmp_path: Path) -> None:
    gist_id = "deadbeef01"
    foo = "pragma solidity ^0.8.20;\ncontract Foo {}"
    bar = "pragma solidity ^0.8.20;\ncontract Bar {}"
    payload = _gist_api_payload(
        {
            "Foo.sol": _file_entry("Foo.sol", foo),
            "lib/Bar.sol": _file_entry("lib/Bar.sol", bar),
        },
        gist_id=gist_id,
    )
    body = json.dumps(payload).encode("utf-8")
    dest = tmp_path / "artifacts" / "source" / "gist"

    with patch(
        "urllib.request.urlopen",
        return_value=_FakeHTTPResponse(body),
    ) as urlopen:
        sources = fetch_gist_sources(
            f"https://gist.github.com/alice/{gist_id}",
            dest_dir=dest,
            network_policy=NetworkPolicy.ALLOW_FETCH,
            timeout_seconds=5.0,
        )
        assert urlopen.call_count == 1
        req = urlopen.call_args[0][0]
        assert req.full_url == f"https://api.github.com/gists/{gist_id}"
        assert req.get_header("User-agent") == "auditor-engine"
        assert "application/vnd.github+json" in req.get_header("Accept")

    assert sources == {
        "src/Foo.sol": foo,
        "lib/Bar.sol": bar,
    }
    assert (dest / "src" / "Foo.sol").read_text(encoding="utf-8") == foo
    assert (dest / "lib" / "Bar.sol").read_text(encoding="utf-8") == bar
    meta = json.loads((dest / ".gist_cache.json").read_text(encoding="utf-8"))
    assert meta["gist_id"] == gist_id
    assert set(meta["files"]) == {"src/Foo.sol", "lib/Bar.sol"}


def test_cache_hit_skips_network_even_under_deny(tmp_path: Path) -> None:
    gist_id = "cafebabe02"
    content = "pragma solidity ^0.8.0;\ncontract C {}"
    dest = tmp_path / "cache"
    # Seed cache as a prior ALLOW_FETCH phase would.
    payload = _gist_api_payload(
        {"C.sol": _file_entry("C.sol", content)},
        gist_id=gist_id,
    )
    with patch(
        "urllib.request.urlopen",
        return_value=_FakeHTTPResponse(json.dumps(payload).encode()),
    ):
        first = fetch_gist_sources(
            f"https://gist.github.com/u/{gist_id}",
            dest_dir=dest,
            network_policy=NetworkPolicy.ALLOW_FETCH,
        )
    assert first == {"src/C.sol": content}

    with patch("urllib.request.urlopen") as urlopen:
        second = fetch_gist_sources(
            f"https://gist.github.com/u/{gist_id}",
            dest_dir=dest,
            network_policy=NetworkPolicy.DENY,
        )
        urlopen.assert_not_called()
    assert second == first


# ---------------------------------------------------------------------------
# Size limits
# ---------------------------------------------------------------------------


def test_payload_too_large_total(tmp_path: Path) -> None:
    big = "x" * 1000
    payload = _gist_api_payload({"Big.sol": _file_entry("Big.sol", big)})
    body = json.dumps(payload).encode("utf-8")

    with (
        patch("urllib.request.urlopen", return_value=_FakeHTTPResponse(body)),
        pytest.raises(PayloadTooLargeError),
    ):
        fetch_gist_sources(
            "https://gist.github.com/alice/abc123",
            dest_dir=tmp_path / "g",
            network_policy=NetworkPolicy.ALLOW_FETCH,
            max_total_bytes=100,
            max_file_bytes=5000,
        )


def test_payload_too_large_per_file(tmp_path: Path) -> None:
    content = "y" * 200
    payload = _gist_api_payload({"Y.sol": _file_entry("Y.sol", content)})
    body = json.dumps(payload).encode("utf-8")

    with (
        patch("urllib.request.urlopen", return_value=_FakeHTTPResponse(body)),
        pytest.raises(PayloadTooLargeError, match="per-file"),
    ):
        fetch_gist_sources(
            "https://gist.github.com/alice/abc123",
            dest_dir=tmp_path / "g",
            network_policy=NetworkPolicy.ALLOW_FETCH,
            max_total_bytes=10_000,
            max_file_bytes=50,
        )


def test_http_response_body_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Oversized API body is rejected before JSON parse."""
    import auditor.ingest.fetch as fetch_mod

    monkeypatch.setattr(fetch_mod, "_RESPONSE_OVERHEAD_BYTES", 50)
    huge = b"x" * 200
    with (
        patch("urllib.request.urlopen", return_value=_FakeHTTPResponse(huge)),
        pytest.raises(PayloadTooLargeError, match="HTTP response"),
    ):
        fetch_gist_sources(
            "https://gist.github.com/alice/abc123",
            dest_dir=tmp_path / "g",
            network_policy=NetworkPolicy.ALLOW_FETCH,
            max_total_bytes=100,
        )


# ---------------------------------------------------------------------------
# API / content errors
# ---------------------------------------------------------------------------


def test_empty_gist_files(tmp_path: Path) -> None:
    payload = {"id": "abc123", "files": {}}
    body = json.dumps(payload).encode()
    with (
        patch("urllib.request.urlopen", return_value=_FakeHTTPResponse(body)),
        pytest.raises(GistFetchError, match="no files"),
    ):
        fetch_gist_sources(
            "https://gist.github.com/alice/abc123",
            dest_dir=tmp_path / "g",
            network_policy=NetworkPolicy.ALLOW_FETCH,
        )


def test_path_escape_filename_rejected(tmp_path: Path) -> None:
    payload = _gist_api_payload({"evil": _file_entry("../evil.sol", "pragma solidity ^0.8.0;")})
    body = json.dumps(payload).encode()
    with (
        patch("urllib.request.urlopen", return_value=_FakeHTTPResponse(body)),
        pytest.raises(GistFetchError, match="path escape"),
    ):
        fetch_gist_sources(
            "https://gist.github.com/alice/abc123",
            dest_dir=tmp_path / "g",
            network_policy=NetworkPolicy.ALLOW_FETCH,
        )


def test_http_error_surfaced(tmp_path: Path) -> None:
    import urllib.error

    err = urllib.error.HTTPError(
        url="https://api.github.com/gists/abc123",
        code=404,
        msg="Not Found",
        hdrs=None,  # type: ignore[arg-type]
        fp=io.BytesIO(b'{"message":"Not Found"}'),
    )
    with (
        patch("urllib.request.urlopen", side_effect=err),
        pytest.raises(GistFetchError, match="404") as ei,
    ):
        fetch_gist_sources(
            "https://gist.github.com/alice/abc123",
            dest_dir=tmp_path / "g",
            network_policy=NetworkPolicy.ALLOW_FETCH,
        )
    assert ei.value.status == 404


def test_truncated_file_fetches_raw(tmp_path: Path) -> None:
    gist_id = "abc001def002"
    full = "pragma solidity ^0.8.20;\n// " + ("z" * 100)
    raw_url = f"https://gist.githubusercontent.com/u/{gist_id}/raw/Foo.sol"
    payload = _gist_api_payload(
        {
            "Foo.sol": _file_entry(
                "Foo.sol",
                "",
                truncated=True,
                raw_url=raw_url,
            ),
        },
        gist_id=gist_id,
    )
    # Fix size to match full content for pre-check.
    payload["files"]["Foo.sol"]["size"] = len(full.encode("utf-8"))

    def fake_urlopen(req: Any, timeout: float = 0) -> _FakeHTTPResponse:
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if "api.github.com" in url:
            return _FakeHTTPResponse(json.dumps(payload).encode())
        if url == raw_url:
            return _FakeHTTPResponse(full.encode("utf-8"))
        raise AssertionError(f"unexpected URL {url}")

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        sources = fetch_gist_sources(
            f"https://gist.github.com/u/{gist_id}",
            dest_dir=tmp_path / "g",
            network_policy=NetworkPolicy.ALLOW_FETCH,
        )
    assert sources["src/Foo.sol"] == full


def test_invalid_timeout_kwarg(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="timeout"):
        fetch_gist_sources(
            "https://gist.github.com/alice/abc123",
            dest_dir=tmp_path,
            network_policy=NetworkPolicy.ALLOW_FETCH,
            timeout_seconds=0,
        )


def test_request_uses_timeout(tmp_path: Path) -> None:
    payload = _gist_api_payload({"A.sol": _file_entry("A.sol", "contract A {}")})
    mock_open = MagicMock(return_value=_FakeHTTPResponse(json.dumps(payload).encode()))
    with patch("urllib.request.urlopen", mock_open):
        fetch_gist_sources(
            "https://gist.github.com/alice/abc123",
            dest_dir=tmp_path / "g",
            network_policy=NetworkPolicy.ALLOW_FETCH,
            timeout_seconds=12.5,
        )
    assert mock_open.call_args.kwargs.get("timeout") == 12.5
