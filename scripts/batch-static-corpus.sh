#!/usr/bin/env bash
# batch-static-corpus.sh — free multi-contract static calibration (no LLM / OpenRouter).
#
# Runs auditor with --profile static --no-llm over each examples/corpus/* case
# (one job per case directory; all .sol under that case as multi-file).
# Optional: --include-batch for examples/batch/*.sol (one job per file).
#
# Usage:
#   ./scripts/batch-static-corpus.sh
#   ./scripts/batch-static-corpus.sh --include-batch
#   make static-corpus
#
# Prefer Docker image auditor:local (build if missing). If Docker is unavailable,
# fall back to host auditor-cli with a warning.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

IMAGE="${AUDITOR_IMAGE:-auditor:local}"
OUT_ROOT="${STATIC_CORPUS_OUT:-$ROOT/work/static-corpus}"
SUMMARY_JSONL="$OUT_ROOT/summary.jsonl"
REPORT_MD="${STATIC_CORPUS_REPORT:-$ROOT/docs/test-reports/static-corpus-latest.md}"
TIMEOUT="${AUDIT_TIMEOUT_SECONDS:-300}"

INCLUDE_BATCH=0
for arg in "$@"; do
  case "$arg" in
    --include-batch)
      INCLUDE_BATCH=1
      ;;
    --help|-h)
      cat <<'EOF'
Usage: ./scripts/batch-static-corpus.sh [--include-batch]

  Run static-profile audits (no LLM) over examples/corpus/*/src.

  --include-batch   Also run examples/batch/*.sol (one job each)

Outputs:
  work/static-corpus/summary.jsonl
  docs/test-reports/static-corpus-latest.md

Env:
  AUDITOR_IMAGE, STATIC_CORPUS_OUT, STATIC_CORPUS_REPORT, AUDIT_TIMEOUT_SECONDS
EOF
      exit 0
      ;;
    *)
      echo "error: unknown argument: $arg" >&2
      exit 2
      ;;
  esac
done

mkdir -p "$OUT_ROOT"
: >"$SUMMARY_JSONL"

# ---------------------------------------------------------------------------
# Runner selection: Docker preferred; host auditor-cli fallback
# ---------------------------------------------------------------------------
RUN_MODE="" # docker | host

if command -v docker >/dev/null 2>&1; then
  if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
    echo "image $IMAGE not found; building via make build-image ..." >&2
    if make build-image; then
      :
    else
      echo "warning: failed to build $IMAGE" >&2
    fi
  fi
  if docker image inspect "$IMAGE" >/dev/null 2>&1; then
    RUN_MODE=docker
  fi
fi

if [[ -z "$RUN_MODE" ]]; then
  if command -v auditor-cli >/dev/null 2>&1; then
    echo "warning: Docker image $IMAGE unavailable; using host auditor-cli" >&2
    echo "warning: results depend on local forge/slither/aderyn on PATH" >&2
    RUN_MODE=host
  else
    echo "error: neither Docker image $IMAGE nor host auditor-cli available" >&2
    echo "hint: make build-image  OR  make install && ensure forge/slither on PATH" >&2
    exit 1
  fi
fi

# Collect cases: label|kind|host_path
# kind=dir → pass path as directory (multi-file); kind=file → single .sol
CASES=()

if [[ -d examples/corpus ]]; then
  while IFS= read -r -d '' case_dir; do
    label="$(basename "$case_dir")"
    src_dir="$case_dir/src"
    if [[ ! -d "$src_dir" ]]; then
      continue
    fi
    # Skip if no .sol under src
    if ! find "$src_dir" -type f -name '*.sol' -print -quit | grep -q .; then
      continue
    fi
    CASES+=("${label}|dir|${src_dir}")
  done < <(find examples/corpus -mindepth 1 -maxdepth 1 -type d -print0 | sort -z)
fi

if [[ "$INCLUDE_BATCH" -eq 1 ]]; then
  if [[ -d examples/batch ]]; then
    while IFS= read -r -d '' sol; do
      base="$(basename "$sol" .sol)"
      # snake-ish label from filename
      label="batch_$(echo "$base" | sed -E 's/([a-z])([A-Z])/\1_\2/g' | tr '[:upper:]' '[:lower:]')"
      CASES+=("${label}|file|${sol}")
    done < <(find examples/batch -maxdepth 1 -type f -name '*.sol' -print0 | sort -z)
  fi
fi

if [[ ${#CASES[@]} -eq 0 ]]; then
  echo "error: no corpus cases found under examples/corpus/*/src" >&2
  exit 1
fi

echo "=== Static corpus batch (no LLM) ==="
echo "Mode:    $RUN_MODE"
[[ "$RUN_MODE" == "docker" ]] && echo "Image:   $IMAGE"
echo "Out:     $OUT_ROOT"
echo "Report:  $REPORT_MD"
echo "Cases:   ${#CASES[@]}"
echo "Profile: static --no-llm"
echo

# ---------------------------------------------------------------------------
# Run one case
# ---------------------------------------------------------------------------
run_case() {
  local label="$1" kind="$2" host_path="$3"
  local job_dir="$OUT_ROOT/$label"
  mkdir -p "$job_dir"

  local rc=0
  if [[ "$RUN_MODE" == "docker" ]]; then
    local mount_src container_path
    if [[ "$kind" == "dir" ]]; then
      mount_src="$(cd "$host_path" && pwd)"
      container_path="/corpus/${label}"
    else
      mount_src="$(cd "$(dirname "$host_path")" && pwd)/$(basename "$host_path")"
      container_path="/corpus/$(basename "$host_path")"
    fi

    set +e
    docker run --rm \
      --network none \
      --memory=2g --cpus=2 \
      --cap-drop ALL \
      --security-opt no-new-privileges:true \
      -e AUDIT_PROFILE=static \
      -e AUDIT_JOB_ROOT=/work/jobs \
      -e AUDIT_TIMEOUT_SECONDS="$TIMEOUT" \
      -e AUDIT_NETWORK_POLICY=deny \
      -v "${mount_src}:${container_path}:ro" \
      -v "${job_dir}:/work/jobs" \
      "$IMAGE" \
      auditor-cli run "$container_path" \
        --profile static \
        --no-llm \
        --job-root /work/jobs \
        --timeout "$TIMEOUT" \
      | tee "$job_dir/cli.log"
    rc=${PIPESTATUS[0]}
    set -e
  else
    set +e
    auditor-cli run "$host_path" \
      --profile static \
      --no-llm \
      --job-root "$job_dir" \
      --timeout "$TIMEOUT" \
      | tee "$job_dir/cli.log"
    rc=${PIPESTATUS[0]}
    set -e
  fi
  return "$rc"
}

# ---------------------------------------------------------------------------
# Extract summary fields from a job directory
# ---------------------------------------------------------------------------
summarize_job() {
  local job_dir="$1"
  # Newest UUID-looking job id under mount
  local job_id
  job_id="$(ls -1t "$job_dir" 2>/dev/null | grep -E '^[0-9a-f-]{36}$' | head -1 || true)"

  python3 - "$job_dir" "${job_id:-}" <<'PY'
import json
import sys
from pathlib import Path

job_dir = Path(sys.argv[1])
job_id = sys.argv[2] or ""
base = job_dir / job_id if job_id else None

compile_ok = False
findings_n = 0
tools_run: list[str] = []
detectors: list[str] = []
status = "unknown"

if base and base.is_dir():
    manifest_p = base / "artifacts" / "manifest.json"
    findings_p = base / "artifacts" / "static" / "findings.json"

    if manifest_p.is_file():
        try:
            m = json.loads(manifest_p.read_text())
            status = str(m.get("status") or "unknown")
            stages = m.get("stages") or []
            for st in stages:
                if st.get("stage") == "compile":
                    compile_ok = st.get("status") == "completed"
                if st.get("stage") == "finalize" and st.get("status") == "completed":
                    # Some image builds leave top-level status as "running";
                    # treat successful finalize as completed for the summary.
                    if status in ("running", "unknown", ""):
                        status = "completed"
        except (OSError, json.JSONDecodeError):
            pass

    if findings_p.is_file():
        try:
            d = json.loads(findings_p.read_text())
            tools_run = list(d.get("tools_run") or [])
            finds = d.get("findings") or []
            findings_n = len(finds)
            seen: set[str] = set()
            ordered: list[str] = []
            for f in finds:
                det = str(f.get("detector_id") or "").strip()
                if not det or det in seen:
                    continue
                seen.add(det)
                ordered.append(det)
            detectors = ordered
        except (OSError, json.JSONDecodeError):
            pass

out = {
    "job_id": job_id,
    "status": status,
    "compile_ok": compile_ok,
    "findings": findings_n,
    "tools_run": tools_run,
    "detectors": detectors,
}
print(json.dumps(out))
PY
}

n=0
total=${#CASES[@]}
for entry in "${CASES[@]}"; do
  n=$((n + 1))
  label="${entry%%|*}"
  rest="${entry#*|}"
  kind="${rest%%|*}"
  host_path="${rest#*|}"

  echo "========== [$n/$total] $label ($kind: $host_path) =========="

  set +e
  run_case "$label" "$kind" "$host_path"
  rc=$?
  set -e

  job_dir="$OUT_ROOT/$label"
  summary_raw="$(summarize_job "$job_dir")"

  # Merge run meta into summary line
  python3 - "$SUMMARY_JSONL" "$n" "$label" "$kind" "$host_path" "$rc" "$summary_raw" <<'PY'
import json, sys
from pathlib import Path

summary_path = Path(sys.argv[1])
n = int(sys.argv[2])
label = sys.argv[3]
kind = sys.argv[4]
host_path = sys.argv[5]
rc = int(sys.argv[6])
payload = json.loads(sys.argv[7])

status = str(payload.get("status") or "unknown")
compile_ok = bool(payload.get("compile_ok"))
tools = list(payload.get("tools_run") or [])
# Manifest may still say "running" after a successful static job (flush/race).
# Prefer exit + compile/findings signal for the summary status.
if rc == 0 and compile_ok and (tools or int(payload.get("findings") or 0) >= 0):
    if status in ("running", "unknown", ""):
        status = "completed"
elif rc != 0 and status in ("running", "unknown", ""):
    status = "failed"

row = {
    "n": n,
    "label": label,
    "kind": kind,
    "path": host_path,
    "exit": rc,
    "job_id": payload.get("job_id") or "",
    "status": status,
    "compile_ok": compile_ok,
    "findings": int(payload.get("findings") or 0),
    "tools_run": tools,
    "detectors": payload.get("detectors") or [],
}
with summary_path.open("a") as fh:
    fh.write(json.dumps(row) + "\n")
print(
    f"  exit={rc} status={row['status']} compile_ok={row['compile_ok']} "
    f"findings={row['findings']} tools={','.join(row['tools_run']) or '-'}"
)
if row["detectors"]:
    print(f"  detectors: {', '.join(row['detectors'][:20])}")
    if len(row["detectors"]) > 20:
        print(f"  ... +{len(row['detectors']) - 20} more")
PY
  echo
done

# ---------------------------------------------------------------------------
# Write markdown report + terminal table
# ---------------------------------------------------------------------------
python3 - "$SUMMARY_JSONL" "$REPORT_MD" "$RUN_MODE" "$IMAGE" "$TIMEOUT" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

summary_path = Path(sys.argv[1])
report_path = Path(sys.argv[2])
run_mode = sys.argv[3]
image = sys.argv[4]
timeout = sys.argv[5]

rows = []
for line in summary_path.read_text().splitlines():
    line = line.strip()
    if not line:
        continue
    rows.append(json.loads(line))

now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
compile_ok_n = sum(1 for r in rows if r.get("compile_ok"))
failed_n = sum(1 for r in rows if int(r.get("exit") or 0) != 0)

lines = [
    "# Static corpus run summary",
    "",
    f"**Generated:** {now}  ",
    f"**Profile:** `static` + `--no-llm` (offline; no OpenRouter)  ",
    f"**Runner:** `{run_mode}`"
    + (f" (`{image}`)" if run_mode == "docker" else " (host `auditor-cli`)")
    + "  ",
    f"**Timeout:** {timeout}s  ",
    f"**Cases:** {len(rows)} (compile ok: {compile_ok_n}; non-zero exit: {failed_n})  ",
    "",
    "## Suite",
    "",
    "| label | compile ok? | findings | tools_run | detectors (high-level) |",
    "|-------|-------------|----------|-----------|------------------------|",
]

def md_cell(s: str) -> str:
    return s.replace("|", "\\|").replace("\n", " ")

for r in rows:
    tools = ",".join(r.get("tools_run") or []) or "-"
    dets = r.get("detectors") or []
    # Cap for table readability
    det_s = ", ".join(dets[:12]) if dets else "-"
    if len(dets) > 12:
        det_s += f" (+{len(dets) - 12})"
    compile_s = "yes" if r.get("compile_ok") else "no"
    lines.append(
        f"| `{md_cell(r.get('label', ''))}` | {compile_s} | {int(r.get('findings') or 0)} "
        f"| {md_cell(tools)} | {md_cell(det_s)} |"
    )

lines += [
    "",
    "## Notes",
    "",
    "- One job per `examples/corpus/<case>/` (all `src/**/*.sol` in that case).",
    "- Detectors are unique `detector_id` values from `artifacts/static/findings.json` (best-effort).",
    "- No LLM keys required; do not use this script for dual-LLM calibration.",
    f"- Raw JSONL: `{summary_path}`",
    "",
    "## Related",
    "",
    "- [sample-static-corpus.md](./sample-static-corpus.md) — fictional format sample",
    "- [../testing.md](../testing.md) — how to run",
    "",
]

report_path.parent.mkdir(parents=True, exist_ok=True)
report_path.write_text("\n".join(lines))

# Terminal table
print(f"{'label':28} {'compile':8} {'find':5} {'tools':28} detectors")
print("-" * 100)
for r in rows:
    tools = ",".join(r.get("tools_run") or []) or "-"
    dets = ",".join((r.get("detectors") or [])[:8]) or "-"
    if len(r.get("detectors") or []) > 8:
        dets += ",…"
    print(
        f"{str(r.get('label',''))[:28]:28} "
        f"{'yes' if r.get('compile_ok') else 'no':8} "
        f"{int(r.get('findings') or 0):5} "
        f"{tools[:28]:28} "
        f"{dets}"
    )
print()
print(f"Report: {report_path}")
print(f"JSONL:  {summary_path}")
PY

echo "=== Done. Static corpus batch complete (no LLM). ==="
