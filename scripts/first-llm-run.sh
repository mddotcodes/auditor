#!/usr/bin/env bash
# First paid LLM smoke: ONE contract, default dual profile via Docker (user-audit.sh).
# Prefer ./scripts/user-audit.sh for all user-facing audits; this wrapper only
# asserts an API key and points at a single starter contract.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

if [[ -z "${OPENROUTER_API_KEY:-}${OPENAI_API_KEY:-}${ANTHROPIC_API_KEY:-}" ]]; then
  echo "error: set OPENROUTER_API_KEY (or OPENAI/ANTHROPIC) in the environment or .env" >&2
  exit 2
fi

CONTRACT="${1:-examples/corpus/reentrancy/src/ReentrancyBank.sol}"
TIMEOUT="${AUDIT_TIMEOUT_SECONDS:-300}"

# Dual plan→code defaults when unset (see .env.example); user-audit passes them through.
export AUDIT_LLM_DUAL="${AUDIT_LLM_DUAL:-true}"

echo "First LLM run via Docker (preferred user path: scripts/user-audit.sh)"
echo "Contract: $CONTRACT"
echo "Profile:  default  dual=${AUDIT_LLM_DUAL}"
echo "---"

exec "$ROOT/scripts/user-audit.sh" \
  --profile default \
  --timeout "$TIMEOUT" \
  "$CONTRACT"
