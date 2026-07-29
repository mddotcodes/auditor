#!/usr/bin/env bash
# Full dual-LLM Docker audits for a fixed fixture list (user-style, no host toolchain).
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
  echo "error: need an LLM API key in the environment" >&2
  exit 2
fi

IMAGE="${AUDITOR_IMAGE:-auditor:local}"
OUT_ROOT="${BATCH_OUT:-$ROOT/work/batch-runs}"
SUMMARY="$OUT_ROOT/summary.jsonl"
mkdir -p "$OUT_ROOT"
: >"$SUMMARY"

# path_on_host|label
FIXTURES=(
  "examples/batch/CleanCounter.sol|clean_counter"
  "examples/contracts/OzToken.sol|oz_token_clean"
  "examples/contracts/SafeCounter.sol|safe_counter"
  "examples/corpus/unprotected_function/src/Unprotected.sol|unprotected_mint"
  "examples/corpus/unchecked_call/src/UncheckedCall.sol|unchecked_call"
  "examples/corpus/weird_erc20/src/MissingReturns.sol|weird_missing_returns"
  "examples/corpus/weird_erc20/src/TransferFee.sol|weird_transfer_fee"
  "examples/corpus/weird_erc20/src/ReentrantToken.sol|weird_reentrant_token"
  "examples/batch/SimpleVault.sol|simple_vault"
  "examples/batch/MultiSigLite.sol|multisig_lite"
)

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "Building $IMAGE ..."
  make build-image
fi

echo "=== Batch user audits (Docker dual-LLM) ==="
echo "Image: $IMAGE"
echo "Out:   $OUT_ROOT"
echo "Dual:  ${AUDIT_LLM_DUAL:-true} plan=${AUDIT_LLM_PLAN_MODEL:-} code=${AUDIT_LLM_CODE_MODEL:-}"
echo

n=0
for entry in "${FIXTURES[@]}"; do
  n=$((n + 1))
  host_path="${entry%%|*}"
  label="${entry##*|}"
  if [[ ! -f "$host_path" ]]; then
    echo "[$n] SKIP missing $host_path"
    continue
  fi
  job_dir="$OUT_ROOT/$label"
  mkdir -p "$job_dir"
  base="$(basename "$host_path")"
  echo "========== [$n/${#FIXTURES[@]}] $label ($base) =========="

  set +e
  docker run --rm \
    --memory=2g --cpus=2 \
    -e OPENROUTER_API_KEY \
    -e OPENAI_API_KEY \
    -e ANTHROPIC_API_KEY \
    -e AUDIT_LLM_DUAL="${AUDIT_LLM_DUAL:-true}" \
    -e AUDIT_LLM_PLAN_MODEL="${AUDIT_LLM_PLAN_MODEL:-deepseek/deepseek-v4-pro}" \
    -e AUDIT_LLM_CODE_MODEL="${AUDIT_LLM_CODE_MODEL:-deepseek/deepseek-v4-flash}" \
    -e AUDIT_LLM_MODEL="${AUDIT_LLM_MODEL:-deepseek/deepseek-v4-flash}" \
    -e AUDIT_PROFILE=default \
    -e AUDIT_JOB_ROOT=/work/jobs \
    -e AUDIT_TIMEOUT_SECONDS="${AUDIT_TIMEOUT_SECONDS:-300}" \
    -v "$ROOT/$host_path:/corpus/$base:ro" \
    -v "$job_dir:/work/jobs" \
    "$IMAGE" \
    auditor-cli run "/corpus/$base" \
      --profile default \
      --job-root /work/jobs \
      --timeout "${AUDIT_TIMEOUT_SECONDS:-300}" \
    | tee "$job_dir/cli.log"
  rc=${PIPESTATUS[0]}
  set -e

  # Find newest job id under mount
  job_id="$(ls -1t "$job_dir" 2>/dev/null | grep -E '^[0-9a-f-]{36}$' | head -1 || true)"
  findings=0
  tools=""
  status="unknown"
  if [[ -n "$job_id" && -f "$job_dir/$job_id/artifacts/static/findings.json" ]]; then
    findings="$(python3 -c "import json; d=json.load(open('$job_dir/$job_id/artifacts/static/findings.json')); print(len(d.get('findings',[])))")"
    tools="$(python3 -c "import json; d=json.load(open('$job_dir/$job_id/artifacts/static/findings.json')); print(','.join(d.get('tools_run') or []))")"
  fi
  if [[ -n "$job_id" && -f "$job_dir/$job_id/artifacts/manifest.json" ]]; then
    status="$(python3 -c "import json; print(json.load(open('$job_dir/$job_id/artifacts/manifest.json')).get('status','?'))")"
  fi
  printf '{"n":%s,"label":"%s","file":"%s","exit":%s,"job_id":"%s","status":"%s","findings":%s,"tools_run":"%s"}\n' \
    "$n" "$label" "$base" "$rc" "${job_id:-}" "$status" "$findings" "$tools" | tee -a "$SUMMARY"
  echo
done

echo "=== Done. Summary: $SUMMARY ==="
python3 - <<'PY' "$SUMMARY"
import json, sys
from pathlib import Path
p = Path(sys.argv[1])
print(f"{'label':28} {'st':10} {'find':5} tools")
print("-" * 70)
for line in p.read_text().splitlines():
    if not line.strip():
        continue
    d = json.loads(line)
    print(f"{d['label'][:28]:28} {d.get('status','?'):10} {d.get('findings',0):5} {d.get('tools_run','')}")
PY
