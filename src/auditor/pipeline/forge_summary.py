"""Parse forge test --json output into a compact summary."""

from __future__ import annotations

from typing import Any


def summarize_forge_json(parsed: object | None) -> dict[str, Any]:
    """Return passed/failed counts and failed test names."""
    passed = 0
    failed = 0
    skipped = 0
    failed_names: list[str] = []

    if not isinstance(parsed, dict):
        return {
            "passed": 0,
            "failed": 0,
            "skipped": 0,
            "failed_names": [],
            "ok": False,
        }

    # Shape: { "path:Contract": { "test_results": { "testName": { "status": "Success|Failure" }}}}
    for _suite, body in parsed.items():
        if not isinstance(body, dict):
            continue
        results = body.get("test_results")
        if not isinstance(results, dict):
            continue
        for name, res in results.items():
            if not isinstance(res, dict):
                continue
            status = str(res.get("status") or "").lower()
            if status == "success":
                passed += 1
            elif status == "failure":
                failed += 1
                reason = res.get("reason") or ""
                failed_names.append(f"{name}" + (f" ({reason})" if reason else ""))
            else:
                skipped += 1

    return {
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "failed_names": failed_names[:20],
        "ok": failed == 0 and passed + failed + skipped > 0,
        "total": passed + failed + skipped,
    }
