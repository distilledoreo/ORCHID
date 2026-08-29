#!/usr/bin/env bash
set -euo pipefail

# Run the live CapsuleEnvelopeV2 consolidator gate one frozen chunk per process.
# This maximizes provider isolation and supports resume via checkpoint files.

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python_executable="${PYTHON_EXECUTABLE:-$root/.venv/bin/python}"
key_file="${ORCHID_BENCHMARK_KEY_FILE:-${XDG_CONFIG_HOME:-$HOME/.config}/orchid/benchmark.env}"
checkpoint_dir="${ORCHID_CONSOLIDATOR_CHECKPOINT_DIR:-$root/artifacts/selector_contract/consolidator_gate_checkpoints}"
output_report="${ORCHID_CONSOLIDATOR_GATE_REPORT:-$root/artifacts/selector_contract/V2_CONSOLIDATOR_GATE_REPORT.json}"
chunk_count="${ORCHID_CONSOLIDATOR_CHUNK_COUNT:-32}"

if [[ -f "$key_file" ]]; then
  # shellcheck disable=SC1090
  source "$key_file"
fi

export ORCHID_CANONICALIZER_ENABLED="${ORCHID_CANONICALIZER_ENABLED:-false}"
export ORCHID_MEMORY_FORMAT="${ORCHID_MEMORY_FORMAT:-v2_shadow}"
# Always align live consolidator with benchmark provider after sourcing key_file so
# stale shell ORCHID_CONSOLIDATOR_* values cannot override solar-pro4 credentials.
if [[ -n "${BENCHMARK_PROVIDER_BASE_URL:-}" ]]; then
  export ORCHID_CONSOLIDATOR_URL="$BENCHMARK_PROVIDER_BASE_URL"
fi
if [[ -n "${BENCHMARK_PROVIDER_API_KEY:-}" ]]; then
  export ORCHID_CONSOLIDATOR_API_KEY="$BENCHMARK_PROVIDER_API_KEY"
fi
if [[ -n "${BENCHMARK_MODEL_ID:-}" ]]; then
  export ORCHID_CONSOLIDATOR_MODEL="$BENCHMARK_MODEL_ID"
fi
export ORCHID_CONSOLIDATOR_URL="${ORCHID_CONSOLIDATOR_URL:-$BENCHMARK_PROVIDER_BASE_URL}"
export ORCHID_CONSOLIDATOR_API_KEY="${ORCHID_CONSOLIDATOR_API_KEY:-$BENCHMARK_PROVIDER_API_KEY}"
export ORCHID_CONSOLIDATOR_MODEL="${ORCHID_CONSOLIDATOR_MODEL:-$BENCHMARK_MODEL_ID}"
export ORCHID_CONSOLIDATOR_INTER_CHUNK_DELAY_SECONDS="${ORCHID_CONSOLIDATOR_INTER_CHUNK_DELAY_SECONDS:-0}"
export ORCHID_CONSOLIDATOR_SUBPROCESS_CHUNKS="${ORCHID_CONSOLIDATOR_SUBPROCESS_CHUNKS:-true}"
export ORCHID_MODEL_TIMEOUT_SECONDS="${ORCHID_MODEL_TIMEOUT_SECONDS:-300}"
export ORCHID_CONSOLIDATOR_CHUNK_TIMEOUT_SECONDS="${ORCHID_CONSOLIDATOR_CHUNK_TIMEOUT_SECONDS:-360}"

: "${ORCHID_CONSOLIDATOR_URL:?Set ORCHID_CONSOLIDATOR_URL or BENCHMARK_PROVIDER_BASE_URL}"
: "${ORCHID_CONSOLIDATOR_API_KEY:?Set ORCHID_CONSOLIDATOR_API_KEY or BENCHMARK_PROVIDER_API_KEY}"
: "${ORCHID_CONSOLIDATOR_MODEL:?Set ORCHID_CONSOLIDATOR_MODEL or BENCHMARK_MODEL_ID}"

mkdir -p "$checkpoint_dir"

for offset in $(seq 0 $((chunk_count - 1))); do
  chunk_index="$("$python_executable" - "$root/artifacts/selector_contract/FROZEN_REQUEST_HASHES.json" "$offset" <<'PY'
import json, sys
chunks = json.load(open(sys.argv[1], encoding="utf-8"))["chunks"]
print(int(chunks[int(sys.argv[2])]["chunk_index"]))
PY
)"
  checkpoint_file="$checkpoint_dir/chunk_$(printf '%04d' "$chunk_index").generation.json"
  if [[ -f "$checkpoint_file" ]]; then
    echo "checkpoint=skip chunk_index=$chunk_index"
    continue
  fi
  echo "checkpoint=run offset=$offset chunk_index=$chunk_index model=$ORCHID_CONSOLIDATOR_MODEL"
  "$python_executable" "$root/tools/batch_v2_semantic_gate_from_consolidator.py" \
    --run-live \
    --offset "$offset" \
    --limit 1 \
    --checkpoint-dir "$checkpoint_dir" \
    --output "$checkpoint_dir/chunk_$(printf '%04d' "$chunk_index").report.json" \
    || true
done

echo "assembling report -> $output_report"
require_all_flag=()
if [[ "$chunk_count" == "32" ]]; then
  require_all_flag=(--require-all-checkpoints)
fi
"$python_executable" "$root/tools/batch_v2_semantic_gate_from_consolidator.py" \
  --assemble-checkpoints "$checkpoint_dir" \
  "${require_all_flag[@]}" \
  --output "$output_report"

accepted="$("$python_executable" - "$output_report" <<'PY'
import json, sys
print(json.load(open(sys.argv[1], encoding='utf-8'))['accepted'])
PY
)"
rejected="$("$python_executable" - "$output_report" <<'PY'
import json, sys
print(json.load(open(sys.argv[1], encoding='utf-8'))['rejected'])
PY
)"
echo "accepted=$accepted rejected=$rejected report=$output_report"
if [[ "$rejected" != 0 ]]; then
  exit 1
fi
