#!/usr/bin/env bash
# user-audit.sh — one obvious Docker-only path to audit Solidity (no host forge/slither).
#
# Usage:
#   ./scripts/user-audit.sh path/to/Contract.sol [more.sol ...]
#   ./scripts/user-audit.sh --profile static examples/contracts/SafeCounter.sol
#   ./scripts/user-audit.sh --profile default --timeout 180 path/to/Token.sol
#
# Env:
#   AUDITOR_IMAGE          Docker image (default: auditor:local)
#   AUDIT_JOB_ROOT         Host job root (overridden by --job-root)
#   AUDIT_TIMEOUT_SECONDS  Default timeout (overridden by --timeout)
#   OPENROUTER_API_KEY / OPENAI_API_KEY / ANTHROPIC_API_KEY
#   AUDIT_LLM_*            Dual / model settings (see .env.example)
#
# Loads repo-root .env if present. Builds the image via `make build-image` if missing.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

IMAGE="${AUDITOR_IMAGE:-auditor:local}"
PROFILE=""
TIMEOUT="${AUDIT_TIMEOUT_SECONDS:-300}"
# Host job directory only via --job-root (default work/user-jobs). Do not reuse
# AUDIT_JOB_ROOT from .env — that value is often the in-container path /work/jobs.
JOB_ROOT_HOST="$ROOT/work/user-jobs"
NO_LLM=0
PATHS=()

usage() {
  cat <<'EOF'
Usage: ./scripts/user-audit.sh [options] path/to/Contract.sol [more.sol ...]

Docker-only audit: mounts contracts read-only under /corpus/, writes jobs to a
host directory. No host forge/slither required — only Docker.

Options:
  --profile static|default|deep   Pipeline depth (default: static if no LLM key,
                                  else default when a key is present)
  --timeout SECONDS               Job wall-clock timeout (default: 300 or AUDIT_TIMEOUT_SECONDS)
  --job-root DIR                  Host directory for job workspaces (default: work/user-jobs)
  --no-llm                        Force static-friendly run (disables LLM tests; profile static
                                  unless --profile is set)
  -h, --help                      Show this help

Environment:
  AUDITOR_IMAGE                   Image tag (default: auditor:local)
  OPENROUTER_API_KEY / OPENAI_API_KEY / ANTHROPIC_API_KEY
  AUDIT_LLM_DUAL, AUDIT_LLM_PLAN_MODEL, AUDIT_LLM_CODE_MODEL, AUDIT_LLM_MODEL, …

Examples:
  ./scripts/user-audit.sh examples/contracts/SafeCounter.sol
  ./scripts/user-audit.sh --profile static examples/corpus/reentrancy/src/ReentrancyBank.sol
  ./scripts/user-audit.sh --profile default --timeout 180 examples/contracts/OzToken.sol
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile)
      [[ $# -ge 2 ]] || { echo "error: --profile requires a value" >&2; exit 2; }
      PROFILE="$2"
      shift 2
      ;;
    --timeout)
      [[ $# -ge 2 ]] || { echo "error: --timeout requires a value" >&2; exit 2; }
      TIMEOUT="$2"
      shift 2
      ;;
    --job-root)
      [[ $# -ge 2 ]] || { echo "error: --job-root requires a value" >&2; exit 2; }
      JOB_ROOT_HOST="$2"
      shift 2
      ;;
    --no-llm)
      NO_LLM=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --)
      shift
      PATHS+=("$@")
      break
      ;;
    -*)
      echo "error: unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
    *)
      PATHS+=("$1")
      shift
      ;;
  esac
done

if [[ ${#PATHS[@]} -eq 0 ]]; then
  echo "error: at least one .sol file or project directory is required" >&2
  usage >&2
  exit 2
fi

has_llm_key=0
if [[ -n "${OPENROUTER_API_KEY:-}${OPENAI_API_KEY:-}${ANTHROPIC_API_KEY:-}" ]]; then
  has_llm_key=1
fi

# Resolve profile: explicit flag > --no-llm → static > key presence > static
if [[ -z "$PROFILE" ]]; then
  if [[ "$NO_LLM" -eq 1 ]]; then
    PROFILE="static"
  elif [[ "$has_llm_key" -eq 1 ]]; then
    PROFILE="default"
  else
    PROFILE="static"
  fi
fi

case "$PROFILE" in
  static|default|deep) ;;
  *)
    echo "error: invalid --profile $PROFILE (use static|default|deep)" >&2
    exit 2
    ;;
esac

if ! command -v docker >/dev/null 2>&1; then
  echo "error: docker not found on PATH (Docker is required for user-audit.sh)" >&2
  exit 1
fi

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "image $IMAGE not found; building via make build-image ..." >&2
  make build-image
fi

# Resolve host paths and prepare mount specs.
HOST_ABS=()
CORPUS_PATHS=()
MOUNT_FLAGS=()
seen_names=()

for raw in "${PATHS[@]}"; do
  if [[ ! -e "$raw" ]]; then
    echo "error: path does not exist: $raw" >&2
    exit 2
  fi
  # Absolute path without requiring GNU realpath
  if [[ "$raw" = /* ]]; then
    abs="$raw"
  else
    abs="$(cd "$(dirname "$raw")" && pwd)/$(basename "$raw")"
  fi
  base="$(basename "$abs")"
  # Avoid collisions when two inputs share a basename
  name="$base"
  n=1
  while printf '%s\n' "${seen_names[@]+"${seen_names[@]}"}" | grep -Fxq "$name"; do
    n=$((n + 1))
    name="${base}.${n}"
  done
  seen_names+=("$name")
  HOST_ABS+=("$abs")
  CORPUS_PATHS+=("/corpus/$name")
  MOUNT_FLAGS+=(-v "$abs:/corpus/$name:ro")
done

# Ensure host job root exists and is writable for the container user when possible.
mkdir -p "$JOB_ROOT_HOST"
JOB_ROOT_HOST="$(cd "$JOB_ROOT_HOST" && pwd)"

USE_NETWORK=0
if [[ "$NO_LLM" -eq 0 && "$PROFILE" != "static" && "$has_llm_key" -eq 1 ]]; then
  USE_NETWORK=1
fi

RUN_ENV=(
  -e "AUDIT_PROFILE=$PROFILE"
  -e "AUDIT_JOB_ROOT=/work/jobs"
  -e "AUDIT_TIMEOUT_SECONDS=$TIMEOUT"
)

# LLM keys and dual settings (pass through when set)
[[ -n "${OPENROUTER_API_KEY:-}" ]] && RUN_ENV+=(-e "OPENROUTER_API_KEY=${OPENROUTER_API_KEY}")
[[ -n "${OPENAI_API_KEY:-}" ]] && RUN_ENV+=(-e "OPENAI_API_KEY=${OPENAI_API_KEY}")
[[ -n "${ANTHROPIC_API_KEY:-}" ]] && RUN_ENV+=(-e "ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}")
[[ -n "${AUDIT_LLM_DUAL:-}" ]] && RUN_ENV+=(-e "AUDIT_LLM_DUAL=${AUDIT_LLM_DUAL}")
[[ -n "${AUDIT_LLM_MODEL:-}" ]] && RUN_ENV+=(-e "AUDIT_LLM_MODEL=${AUDIT_LLM_MODEL}")
[[ -n "${AUDIT_LLM_PLAN_MODEL:-}" ]] && RUN_ENV+=(-e "AUDIT_LLM_PLAN_MODEL=${AUDIT_LLM_PLAN_MODEL}")
[[ -n "${AUDIT_LLM_CODE_MODEL:-}" ]] && RUN_ENV+=(-e "AUDIT_LLM_CODE_MODEL=${AUDIT_LLM_CODE_MODEL}")
[[ -n "${AUDIT_LLM_REPAIR_MODEL:-}" ]] && RUN_ENV+=(-e "AUDIT_LLM_REPAIR_MODEL=${AUDIT_LLM_REPAIR_MODEL}")

CLI_ARGS=(auditor-cli run "${CORPUS_PATHS[@]}" --profile "$PROFILE" --job-root /work/jobs --timeout "$TIMEOUT")
if [[ "$NO_LLM" -eq 1 ]]; then
  CLI_ARGS+=(--no-llm)
fi

NETWORK_FLAGS=()
if [[ "$USE_NETWORK" -eq 1 ]]; then
  NETWORK_FLAGS+=(-e "AUDIT_NETWORK_POLICY=${AUDIT_NETWORK_POLICY:-allow_llm}")
else
  NETWORK_FLAGS+=(--network none -e "AUDIT_NETWORK_POLICY=${AUDIT_NETWORK_POLICY:-deny}")
fi

echo "=== User audit (Docker) ==="
echo "Image:   $IMAGE"
echo "Profile: $PROFILE"
echo "Timeout: ${TIMEOUT}s"
echo "Jobs:    $JOB_ROOT_HOST  (container: /work/jobs)"
echo "Inputs:  ${PATHS[*]}"
if [[ "$USE_NETWORK" -eq 1 ]]; then
  echo "Network: bridge (LLM egress)"
  echo "Dual:    ${AUDIT_LLM_DUAL:-unset} plan=${AUDIT_LLM_PLAN_MODEL:-} code=${AUDIT_LLM_CODE_MODEL:-}"
else
  echo "Network: none"
fi
echo "---"

set +e
docker run --rm \
  --memory="${AUDIT_DOCKER_MEMORY:-2g}" \
  --cpus="${AUDIT_DOCKER_CPUS:-2}" \
  "${NETWORK_FLAGS[@]}" \
  "${RUN_ENV[@]}" \
  "${MOUNT_FLAGS[@]}" \
  -v "$JOB_ROOT_HOST:/work/jobs" \
  "$IMAGE" \
  "${CLI_ARGS[@]}"
rc=$?
set -e

echo "---"

# Newest UUID-looking job directory
job_id="$(ls -1t "$JOB_ROOT_HOST" 2>/dev/null | grep -E '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' | head -1 || true)"

if [[ -n "$job_id" ]]; then
  job_dir="$JOB_ROOT_HOST/$job_id"
  echo "Job id:  $job_id"
  echo "Success path (host artifacts):"
  echo "  $job_dir/artifacts/"
  echo "  $job_dir/artifacts/manifest.json"
  echo "  $job_dir/artifacts/static/findings.json"
  echo "  $job_dir/artifacts/fingerprint.json"
  if [[ -d "$job_dir/artifacts/llm_tests" ]]; then
    echo "  $job_dir/artifacts/llm_tests/"
  fi
else
  echo "warning: no job directory found under $JOB_ROOT_HOST" >&2
fi

if [[ "$rc" -ne 0 ]]; then
  echo "exit: $rc (non-zero — see logs above)" >&2
  exit "$rc"
fi

echo "exit: 0"
exit 0
