#!/usr/bin/env bash
# gen-test-report.sh — run pytest and write a short markdown summary
#
# Usage:
#   ./scripts/gen-test-report.sh              # default: --tb=no -q
#   ./scripts/gen-test-report.sh -k static    # extra pytest args
#   make test-report
#
# Writes: docs/test-reports/latest.md
# Echoes the path written. Does not require LLM or network.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

OUT_DIR="$ROOT/docs/test-reports"
OUT_FILE="$OUT_DIR/latest.md"

if [[ -n "${PYTHON:-}" ]]; then
  :
elif [[ -x "$ROOT/.venv/bin/python" ]]; then
  PYTHON="$ROOT/.venv/bin/python"
else
  PYTHON="python3"
fi

mkdir -p "$OUT_DIR"

# Capture pytest output even when tests fail (report still useful).
set +e
OUTPUT="$("$PYTHON" -m pytest --tb=no -q "$@" 2>&1)"
EXIT_CODE=$?
set -e

# Best-effort parse of common pytest summary lines, e.g.:
#   42 passed in 1.23s
#   40 passed, 2 failed, 1 skipped in 2.00s
#   ========== 5 passed, 1 xfailed in 0.50s ==========
#   1 error in 0.30s
_parse_count() {
  # $1 = word (passed|failed|skipped|error)
  printf '%s\n' "$OUTPUT" | sed -nE "s/.*(^|[^0-9])([0-9]+) ${1}s?.*/\\2/p" | tail -1
}

PASSED="$(_parse_count passed)"
FAILED="$(_parse_count failed)"
SKIPPED="$(_parse_count skipped)"
ERROR="$(_parse_count error)"
DURATION="$(printf '%s\n' "$OUTPUT" | sed -nE 's/.* in ([0-9.]+s).*/\1/p' | tail -1)"

: "${PASSED:=0}"
: "${FAILED:=0}"
: "${SKIPPED:=0}"
: "${ERROR:=0}"
: "${DURATION:=unknown}"

if [[ "$EXIT_CODE" -eq 0 ]]; then
  RESULT="passed"
else
  RESULT="failed"
fi

TIMESTAMP="$(date -u +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null || date -u)"
ARGS_DISPLAY="${*:-"(none)"}"

# Last non-empty summary-ish line for humans (short).
SUMMARY_LINE="$(printf '%s\n' "$OUTPUT" | grep -E 'passed|failed|error|skipped' | tail -1 || true)"
if [[ -z "$SUMMARY_LINE" ]]; then
  SUMMARY_LINE="(could not parse pytest summary; exit $EXIT_CODE)"
fi

{
  echo "# Test report"
  echo
  echo "**Generated:** $TIMESTAMP"
  echo
  echo "## Result"
  echo
  echo "| Field | Value |"
  echo "|-------|--------|"
  echo "| Overall | **$RESULT** (exit $EXIT_CODE) |"
  echo "| Passed | $PASSED |"
  echo "| Failed | $FAILED |"
  echo "| Skipped | $SKIPPED |"
  echo "| Errors | $ERROR |"
  echo "| Duration | $DURATION |"
  echo "| Extra pytest args | \`$ARGS_DISPLAY\` |"
  echo
  echo "## Pytest summary line"
  echo
  echo '```'
  echo "$SUMMARY_LINE"
  echo '```'
  echo
  echo "## Notes"
  echo
  echo "- Counts are best-effort parses of pytest quiet output."
  echo "- Default command: \`python -m pytest --tb=no -q\`."
  echo "- Prefer CI artifacts over committing every \`latest.md\` run; see [README.md](./README.md)."
  echo "- No LLM and no RPC in this report path."
  echo
  echo "## Related"
  echo
  echo "- [testing.md](../testing.md)"
  echo "- [sample-static-corpus.md](./sample-static-corpus.md)"
} >"$OUT_FILE"

echo "$OUT_FILE"
exit "$EXIT_CODE"
