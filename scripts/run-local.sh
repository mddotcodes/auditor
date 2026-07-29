#!/usr/bin/env bash
# run-local.sh — one-command hardened local runs against auditor:local
#
# Usage:
#   ./scripts/run-local.sh              # versions (default)
#   ./scripts/run-local.sh versions
#   ./scripts/run-local.sh forge --version
#   ./scripts/run-local.sh --llm help   # allow bridge network (LLM later)
#
# Hardening matches docs/security/runtime-defaults.md
set -euo pipefail

IMAGE="${AUDITOR_IMAGE:-auditor:local}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

USE_LLM=0
ARGS=()
for arg in "$@"; do
  case "$arg" in
    --llm)
      USE_LLM=1
      ;;
    --help|-h)
      cat <<'EOF'
Usage: ./scripts/run-local.sh [--llm] [command ...]

  Builds auditor:local if missing, then runs a hardened container.

  Default command: versions

  --llm   Omit --network none (bridge). Prints a warning.
          Intended for later LLM provider access with keys in the environment.

Examples:
  ./scripts/run-local.sh
  ./scripts/run-local.sh forge --version
  ./scripts/run-local.sh --llm env
EOF
      exit 0
      ;;
    *)
      ARGS+=("$arg")
      ;;
  esac
done

if [[ ${#ARGS[@]} -eq 0 ]]; then
  ARGS=(versions)
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "error: docker not found on PATH" >&2
  exit 1
fi

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "image $IMAGE not found; building via make build-image ..." >&2
  make build-image
fi

# Security flags aligned with docs/security/runtime-defaults.md
RUN_FLAGS=(
  --rm
  --read-only
  --tmpfs /tmp:rw,noexec,nosuid,size=256m
  --tmpfs /work:rw,exec,nosuid,size=2g
  --tmpfs /artifacts:rw,noexec,nosuid,size=512m
  --cap-drop ALL
  --security-opt no-new-privileges:true
  --memory 2g
  --cpus 2
  --pids-limit 256
  --user 10001:10001
  -e AUDIT_TIMEOUT_SECONDS="${AUDIT_TIMEOUT_SECONDS:-300}"
  -e AUDIT_TERM_GRACE_SECONDS="${AUDIT_TERM_GRACE_SECONDS:-2}"
  -e AUDIT_JOB_ROOT="${AUDIT_JOB_ROOT:-/work/jobs}"
  -e AUDIT_CPU_LIMIT="${AUDIT_CPU_LIMIT:-2}"
  -e AUDIT_MAX_PIDS="${AUDIT_MAX_PIDS:-256}"
  -e AUDIT_PRINT_VERSIONS="${AUDIT_PRINT_VERSIONS:-0}"
)

if [[ "$USE_LLM" -eq 1 ]]; then
  echo "warning: --llm enables container network (bridge). Prefer network none for compile/test." >&2
  echo "warning: only use with LLM keys when you need provider egress; treat model output as untrusted." >&2
  RUN_FLAGS+=(-e "AUDIT_NETWORK_POLICY=${AUDIT_NETWORK_POLICY:-allow_llm}")
  # Pass through common provider keys if set in the host environment.
  [[ -n "${OPENAI_API_KEY:-}" ]] && RUN_FLAGS+=(-e "OPENAI_API_KEY=${OPENAI_API_KEY}")
  [[ -n "${ANTHROPIC_API_KEY:-}" ]] && RUN_FLAGS+=(-e "ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}")
  [[ -n "${OPENROUTER_API_KEY:-}" ]] && RUN_FLAGS+=(-e "OPENROUTER_API_KEY=${OPENROUTER_API_KEY}")
else
  RUN_FLAGS+=(--network none)
  RUN_FLAGS+=(-e "AUDIT_NETWORK_POLICY=${AUDIT_NETWORK_POLICY:-deny}")
fi

exec docker run "${RUN_FLAGS[@]}" "$IMAGE" "${ARGS[@]}"
