#!/usr/bin/env bash
# End-to-end smoke test for `cxas trace` against a real app.
#
# Usage:
#   ./scripts/test_trace.sh                       # uses defaults below
#   APP_DIR=/path/to/pulled/app ./scripts/test_trace.sh
#   CONV_ID=<id> ./scripts/test_trace.sh          # skip the list-and-pick step
#   RUN_BUG_REPORT=1 ./scripts/test_trace.sh      # also exercises bug-report (uploads to GCS)
#
# Requires: gcloud authenticated; app pulled with `cxas pull` so app.json and
# (optionally) environment.json sit under $APP_DIR.

set -euo pipefail

# ---------------------------------------------------------------------------
# Config — override with env vars if needed.
# ---------------------------------------------------------------------------
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Resolve the cxas binary. Prefer the repo's venv (the dev build with `trace`)
# over any globally-installed `cxas` on PATH.
if [[ -x "$REPO_ROOT/.venv/bin/cxas" ]]; then
  CXAS="${CXAS:-$REPO_ROOT/.venv/bin/cxas}"
else
  CXAS="${CXAS:-cxas}"
fi
PYTHON="${PYTHON:-$REPO_ROOT/.venv/bin/python}"

APP_NAME="${APP_NAME:-projects/polysynth-a2a/locations/us/apps/4ce72e10-1d9a-473e-a715-7596d42cf737}"
APP_DIR="${APP_DIR:-$HOME/Projects/humana_cfd_mvp1/cxas_app/_kranthi__humana-cfd-mvp1}"
TIME_FILTER="${TIME_FILTER:-7d}"
CONV_ID="${CONV_ID:-}"
RUN_BUG_REPORT="${RUN_BUG_REPORT:-0}"

OUT_DIR="${OUT_DIR:-./.cxas/trace_smoke}"
mkdir -p "$OUT_DIR"

echo "Using cxas binary: $CXAS"
"$CXAS" --version 2>/dev/null || true

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
if [[ -z "${CXAS_OAUTH_TOKEN:-}" ]]; then
  echo "==> Acquiring access token via gcloud"
  CXAS_OAUTH_TOKEN="$(gcloud auth print-access-token)"
  export CXAS_OAUTH_TOKEN
fi

# Common args repeated for every subcommand.
COMMON=(--app-name "$APP_NAME" --app-dir "$APP_DIR")

step() { printf "\n==> %s\n" "$*"; }

# ---------------------------------------------------------------------------
# 0. Help surface
# ---------------------------------------------------------------------------
step "trace --help"
"$CXAS" trace --help

# ---------------------------------------------------------------------------
# 1. list (table / JSON / CSV / channel filter)
# ---------------------------------------------------------------------------
step "trace list --time-filter $TIME_FILTER (table)"
"$CXAS" trace list "${COMMON[@]}" --time-filter "$TIME_FILTER" --limit 10

step "trace list (JSON, capturing IDs)"
"$CXAS" trace list "${COMMON[@]}" --time-filter "$TIME_FILTER" \
  --limit 10 --format json | tee "$OUT_DIR/list.json" >/dev/null

step "trace list --source LIVE"
"$CXAS" trace list "${COMMON[@]}" --time-filter "$TIME_FILTER" --source LIVE --limit 5

step "trace list --channel AUDIO"
"$CXAS" trace list "${COMMON[@]}" --time-filter "$TIME_FILTER" --channel AUDIO --limit 5 || true

step "trace list (CSV)"
"$CXAS" trace list "${COMMON[@]}" --time-filter "$TIME_FILTER" --limit 5 --format csv \
  | tee "$OUT_DIR/list.csv" >/dev/null

# Pick the first conversation_id if not provided.
if [[ -z "$CONV_ID" ]]; then
  CONV_ID="$("$PYTHON" -c "import json,sys; data=json.load(open('$OUT_DIR/list.json')); print(data[0]['id'] if data else '')")"
  if [[ -z "$CONV_ID" ]]; then
    echo "No conversations found in the last $TIME_FILTER. Set CONV_ID explicitly." >&2
    exit 1
  fi
fi
echo "==> Using CONV_ID=$CONV_ID"

# ---------------------------------------------------------------------------
# 2. open — print CES Console URL
# ---------------------------------------------------------------------------
step "trace open $CONV_ID"
"$CXAS" trace open "${COMMON[@]}" "$CONV_ID"

# ---------------------------------------------------------------------------
# 3. get — every output format
# ---------------------------------------------------------------------------
for fmt in json md text html; do
  step "trace get --format $fmt"
  "$CXAS" trace get "${COMMON[@]}" "$CONV_ID" --format "$fmt" \
    --out "$OUT_DIR/trace.$fmt" >/dev/null
  echo "wrote $OUT_DIR/trace.$fmt ($(wc -c < "$OUT_DIR/trace.$fmt") bytes)"
done

# ---------------------------------------------------------------------------
# 4. logs — both formats
# ---------------------------------------------------------------------------
step "trace logs --level WARNING"
"$CXAS" trace logs "${COMMON[@]}" "$CONV_ID" --level WARNING --format text \
  > "$OUT_DIR/logs.txt" 2>&1 || true
head -20 "$OUT_DIR/logs.txt" || true

step "trace logs --level ERROR (json)"
"$CXAS" trace logs "${COMMON[@]}" "$CONV_ID" --level ERROR --format json \
  > "$OUT_DIR/logs.json" 2>&1 || true
echo "wrote $OUT_DIR/logs.json"

# ---------------------------------------------------------------------------
# 5. audio download + analyze
# ---------------------------------------------------------------------------
step "trace audio download"
if "$CXAS" trace audio download "${COMMON[@]}" "$CONV_ID" --out "$OUT_DIR/audio"; then
  AUDIO_OK=1
else
  echo "(audio download skipped — bucket may not be configured for this conversation)"
  AUDIO_OK=0
fi

if [[ "$AUDIO_OK" == "1" ]]; then
  step "trace audio analyze (audio_cutoff,voice_drift,humanness,background_noise)"
  "$CXAS" trace audio analyze "${COMMON[@]}" "$CONV_ID" \
    --metric audio_cutoff,voice_drift,humanness,background_noise \
    | tee "$OUT_DIR/audio_analysis.json" >/dev/null
  echo "wrote $OUT_DIR/audio_analysis.json"
fi

# ---------------------------------------------------------------------------
# 6. triage — text-only Gemini pass
# ---------------------------------------------------------------------------
step "trace triage"
"$CXAS" trace triage "${COMMON[@]}" "$CONV_ID" \
  > "$OUT_DIR/triage.json" 2>&1 || true
echo "wrote $OUT_DIR/triage.json"

# ---------------------------------------------------------------------------
# 7. replay — re-run user inputs against the current agent and diff
# ---------------------------------------------------------------------------
step "trace replay --diff"
"$CXAS" trace replay "${COMMON[@]}" "$CONV_ID" --format md \
  > "$OUT_DIR/replay.md" 2>&1 || true
head -40 "$OUT_DIR/replay.md" || true

# ---------------------------------------------------------------------------
# 8. stats — aggregate over the same time window
# ---------------------------------------------------------------------------
step "trace stats"
"$CXAS" trace stats "${COMMON[@]}" --time-filter "$TIME_FILTER" \
  --limit 50 --out "$OUT_DIR/stats.md" || true
echo "wrote $OUT_DIR/stats.md"
head -30 "$OUT_DIR/stats.md" || true

# ---------------------------------------------------------------------------
# 9. bundle — zip transcript + logs + audio + report
# ---------------------------------------------------------------------------
step "trace bundle"
"$CXAS" trace bundle "${COMMON[@]}" "$CONV_ID" \
  --out "$OUT_DIR/bundle.zip" --with-analysis --with-triage || true
ls -la "$OUT_DIR/bundle.zip" 2>/dev/null || echo "(no bundle written)"
echo "Contents:"
unzip -l "$OUT_DIR/bundle.zip" 2>/dev/null || true

# ---------------------------------------------------------------------------
# 10. bug-report — uploads to configured GCS bucket; opt-in.
# ---------------------------------------------------------------------------
if [[ "$RUN_BUG_REPORT" == "1" ]]; then
  step "trace bug-report (severity=low)"
  "$CXAS" trace bug-report "${COMMON[@]}" "$CONV_ID" \
    --reason "smoke test from scripts/test_trace.sh" --severity low
else
  echo
  echo "==> Skipping trace bug-report (set RUN_BUG_REPORT=1 to exercise it)."
fi

echo
echo "==> All trace subcommands exercised. Artifacts in $OUT_DIR"
