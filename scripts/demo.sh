#!/usr/bin/env bash
# ReviewPulse end-to-end demo on the 13-row SYNTHETIC fixture (no download, ~1-2 min).
#
#   bash scripts/demo.sh
#
# Everything is written to a temporary RP_HOME; the repository is not modified.
# Steps: prepare -> split -> train Pop/BPR -> package -> gate (BPR rejected, Pop passes)
#        -> promote (dry-run, execute) -> promote B -> roll back to A -> stale version
#        conflict -> serve the champion and call the API -> monitor summary.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${PYTHON:-python}"
command -v reviewpulse >/dev/null || { echo "reviewpulse CLI not found: run 'pip install -e .' first"; exit 1; }

DEMO_HOME="$(mktemp -d)"
PORT="${RP_DEMO_PORT:-8765}"
SERVER_PID=""
cleanup() {
  if [ -n "$SERVER_PID" ]; then kill "$SERVER_PID" 2>/dev/null || true; fi
  if [ "${RP_DEMO_KEEP:-0}" != "1" ]; then rm -rf "$DEMO_HOME"; fi
}
trap cleanup EXIT

export RP_HOME="$DEMO_HOME"
export RP_USER_KEY="demo-only-key-not-a-secret-0123456789"   # synthetic demo only
export MLFLOW_TRACKING_URI="sqlite:///$("$PY" -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).as_posix())' "$DEMO_HOME/mlflow.db")"
export RP_MLRUNS_DIR="$DEMO_HOME/mlruns"
export MLFLOW_DISABLE_AGENT_HINT=1
export PYTHONUTF8=1

step() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
say() { printf '   %s\n' "$*"; }
json() { "$PY" -c "import json,sys; d=json.load(sys.stdin); print($1)"; }

mkdir -p "$DEMO_HOME/configs"
cp "$REPO"/configs/quality.yaml "$REPO"/configs/runtime.yaml "$DEMO_HOME/configs/"
cp "$REPO"/tests/fixtures/split.yaml "$REPO"/tests/fixtures/train.yaml "$DEMO_HOME/configs/"
cp "$REPO"/tests/fixtures/synthetic_reviews.jsonl "$DEMO_HOME/raw.jsonl"
sed 's#^  raw_path: .*#  raw_path: raw.jsonl#' "$REPO/tests/fixtures/source.yaml" > "$DEMO_HOME/configs/source.yaml"
say "RP_HOME = $DEMO_HOME (temporary)"

step "1. prepare: validate raw reviews, dedupe, hash (data contract)"
reviewpulse data prepare | json '"records:", d["record_counts"], "normalized_sha256:", d["normalized_sha256"][:12]'

step "2. split: global time split train < 2023-01-01 <= valid < 2023-04-01 <= test"
reviewpulse data split | json '{k: v["interactions"] for k, v in d["windows"].items()}'

step "3. train Pop and BPR (2 epochs), evaluate on validation, log to MLflow"
POP_RUN=$(reviewpulse train --model Pop | json 'd[0]["run_id"]')
BPR_RUN=$(reviewpulse train --model BPR 2>/dev/null | json 'd[0]["run_id"]')
POP2_RUN=$(reviewpulse train --model Pop | json 'd[0]["run_id"]')
say "Pop run $POP_RUN, BPR run $BPR_RUN, second Pop run $POP2_RUN"

step "4. package each run into an immutable, hashed release"
A=$(reviewpulse package --run-id "$POP_RUN" | json 'd["release_id"]')
BPR=$(reviewpulse package --run-id "$BPR_RUN" | json 'd["release_id"]')
B=$(reviewpulse package --run-id "$POP2_RUN" | json 'd["release_id"]')
say "Pop A = $A, BPR = $BPR, Pop B = $B"

step "5. quality gate: BPR must beat Pop by min_improvement, Pop is the baseline"
reviewpulse gate --release-id "$BPR" | json '"BPR ->", d["status"], d["reason"], "|", d["detail"]' || true
reviewpulse gate --release-id "$A" | json '"Pop ->", d["status"]'

step "6. promote Pop A: dry-run first (writes nothing), then --execute with expected version 0"
reviewpulse promote --release-id "$A" --expected-version 0 | json 'd["status"], d["detail"]'
reviewpulse promote --release-id "$A" --expected-version 0 --execute | json 'd["status"], "version ->", d["version_counter"]'
say "BPR cannot be promoted:"
reviewpulse promote --release-id "$BPR" --expected-version 1 --execute | json '"  ", d["status"], d["reason"]' || true

step "7. A -> B -> A: promote B (v2), roll back to A (v3)"
reviewpulse promote --release-id "$B" --expected-version 1 --execute | json 'd["status"], "version ->", d["version_counter"]'
reviewpulse rollback --release-id "$A" --expected-version 2 --execute | json 'd["status"], "version ->", d["version_counter"]'
say "someone still holding version 1 tries to promote B: CAS on version_counter rejects it"
reviewpulse promote --release-id "$B" --expected-version 1 --execute | json '"  ", d["status"], d["reason"]' || true
reviewpulse champion show --history 6 | json '"champion:", d["champion"]["release_id"], "version", d["champion"]["version_counter"]'

step "8. serve the champion (loaded once at startup) and call the API"
reviewpulse serve --port "$PORT" >"$DEMO_HOME/serve.log" 2>&1 &
SERVER_PID=$!
for _ in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:$PORT/ready" >/dev/null 2>&1; then break; fi
  sleep 1
done
say "GET /ready -> $(curl -s "http://127.0.0.1:$PORT/ready")"
post() { curl -s -w ' [HTTP %{http_code}]' -X POST "http://127.0.0.1:$PORT/v1/recommend" \
  -H 'Content-Type: application/json' -H 'X-RP-Source: synthetic_demo' -d "$1"; echo; }
say "known user synthetic-u1 (has seen a, b -> gets c, d):"
post '{"user_id": "synthetic-u1", "top_k": 3}'
say "unknown user (no history -> Pop list, fallback_used=true):"
post '{"user_id": "someone-new", "top_k": 3}'
say "invalid argument (top_k=0 -> 422 ApiError):"
post '{"user_id": "synthetic-u1", "top_k": 0}'

step "9. monitor summary (offline NDCG is never shown as an online metric)"
reviewpulse monitor summary --min-events 1 | json '[(g["source"], g["requests"], g["http_4xx"], g["fallback_rate"]) for g in d["groups"]], d["online_recommendation_quality"]["status"]'

step "done"
say "set RP_DEMO_KEEP=1 to keep $DEMO_HOME for inspection"
