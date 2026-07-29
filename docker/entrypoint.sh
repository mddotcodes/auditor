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
  if command -v aderyn >/dev/null 2>&1; then
    aderyn --version 2>&1 | head -n1 | sed 's/^/aderyn:  /'
  else
    echo "aderyn:  (not found)"
  fi
  python -c "import auditor; print(f'auditor: {auditor.__version__}')" 2>&1
}

print_help() {
  cat <<'EOF'
Auditor image entrypoint

Usage:
  docker run --rm -p 8080:8080 auditor:local [versions|help|serve|<command> ...]

Commands:
  versions   Print forge, cast, slither, python, and auditor versions (default)
  serve      Start HTTP/WebSocket API on AUDIT_PORT (default 8080)
  help       Show this help

Any other args are exec'd as a command (e.g. forge --version, auditor-cli metrics …).

Environment:
  AUDIT_PRINT_VERSIONS=1   Print versions before running a non-versions command
  AUDIT_TIMEOUT_SECONDS    Job wall-clock timeout (default 300)
  AUDIT_JOB_ROOT           Job workspace root (default /work/jobs)
  AUDIT_API_TOKEN          Optional API bearer / X-API-Token
  AUDIT_MAX_INFLIGHT_JOBS  Concurrent audit jobs (default 2)
  AUDIT_HOST / AUDIT_PORT  Bind address for serve (0.0.0.0:8080)
  AUDIT_LOG_FORMAT         text|json structured logs (prefer json in cloud)
  AUDIT_METRICS_ENABLED    true|false — GET /metrics Prometheus scrape
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
  serve)
    shift
    exec python -m auditor.api.app "$@"
    ;;
  help|--help|-h)
    print_help
    exit 0
    ;;
  *)
    exec "$@"
    ;;
esac
