#!/usr/bin/env bash
# auditor-entrypoint — thin wrapper for the Auditor image (Phase 1).
# Default command: versions (print forge / slither / python / auditor).
# Later phases may add: serve, run, …
set -euo pipefail

print_versions() {
  echo "=== Auditor image toolchain ==="
  python --version 2>&1 | sed 's/^/python:  /'
  if command -v forge >/dev/null 2>&1; then
    forge --version 2>&1 | head -n1 | sed 's/^/forge:   /'
  else
    echo "forge:   (not found)"
  fi
  if command -v cast >/dev/null 2>&1; then
    cast --version 2>&1 | head -n1 | sed 's/^/cast:    /'
  else
    echo "cast:    (not found)"
  fi
  if command -v slither >/dev/null 2>&1; then
    # slither --version may print to stderr on some builds
    slither --version 2>&1 | head -n1 | sed 's/^/slither: /'
  else
    echo "slither: (not found)"
  fi
  python -c "import auditor; print(f'auditor: {auditor.__version__}')" 2>&1
}

print_help() {
  cat <<'EOF'
Auditor image entrypoint (Phase 1 — no HTTP API yet)

Usage:
  docker run --rm auditor:local [versions|help|<command> ...]

Commands:
  versions   Print forge, cast, slither, python, and auditor versions (default)
  help       Show this help

Any other args are exec'd as a command (e.g. forge --version, bash).

Environment:
  AUDIT_PRINT_VERSIONS=1   Print versions before running a non-versions command
  AUDIT_TIMEOUT_SECONDS    Job timeout hint (default 300; used by future runtime)
  AUDIT_JOB_ROOT           Job workspace root (default /work/jobs)
EOF
}

if [[ "${AUDIT_PRINT_VERSIONS:-0}" == "1" ]]; then
  print_versions
  echo
fi

# Default CMD is ["versions"] when no args are passed.
if [[ $# -eq 0 ]]; then
  set -- versions
fi

case "$1" in
  versions)
    print_versions
    exit 0
    ;;
  help|--help|-h)
    print_help
    exit 0
    ;;
  *)
    exec "$@"
    ;;
esac
