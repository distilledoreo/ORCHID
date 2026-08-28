#!/usr/bin/env bash
set -euo pipefail

# Linux launcher for the sealed corrected FreetoShop Pi-vs-ORCHID A/B.
# No provider request is made by `check` or `prepare`; paid calls begin at `run`.

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python_executable="${PYTHON_EXECUTABLE:-$root/.venv/bin/python}"
pi_executable="${PI_EXECUTABLE:-$root/.benchmark-tools/node_modules/.bin/pi}"
last_run_env="$root/.benchmark-last-run.env"
key_file="${ORCHID_BENCHMARK_KEY_FILE:-${XDG_CONFIG_HOME:-$HOME/.config}/orchid/benchmark.env}"
command_name="${1:-check}"
export BENCHMARK_SELECTOR_BASE_URL="${BENCHMARK_SELECTOR_BASE_URL:-http://127.0.0.1:1234/v1}"
export BENCHMARK_SELECTOR_MODEL_ID="${BENCHMARK_SELECTOR_MODEL_ID:-qwen3.5-4b@q6_k}"
export BENCHMARK_SELECTOR_API_KEY="${BENCHMARK_SELECTOR_API_KEY:-lm-studio}"

load_recovered_inputs() {
  local recovered_root="$root/../benchmark_recovered_inputs/freetoshop_20260820_recovered_v2"
  if [[ -z "${FREETOSHOP_HISTORICAL_STAGE:-}" && -f "$recovered_root/RECOVERY_MANIFEST.json" ]]; then
    export FREETOSHOP_HISTORICAL_STAGE="$recovered_root"
  fi
  if [[ -z "${FREETOSHOP_ORACLE_ROOT:-}" && -d "$recovered_root/oracle/.git" ]]; then
    export FREETOSHOP_ORACLE_ROOT="$recovered_root/oracle"
  fi
}

load_saved_key() {
  if [[ -f "$key_file" ]]; then
    # The file is user-owned, mode 0600, and contains only this export.
    # shellcheck disable=SC1090
    source "$key_file"
  fi
  if [[ -z "${BENCHMARK_PROVIDER_API_KEY:-}" && -n "${OPENROUTER_API_KEY:-}" ]]; then
    export BENCHMARK_PROVIDER_API_KEY="$OPENROUTER_API_KEY"
  fi
  if [[ "${BENCHMARK_MODEL_ID:-}" == "agnes-2.5-pro-beta" ]]; then
    if [[ "${BENCHMARK_INPUT_USD_PER_MILLION:-0}" == "0" ]]; then
      export BENCHMARK_INPUT_USD_PER_MILLION="0.10"
    fi
    if [[ "${BENCHMARK_OUTPUT_USD_PER_MILLION:-0}" == "0" ]]; then
      export BENCHMARK_OUTPUT_USD_PER_MILLION="0.30"
    fi
  fi
}

save_provider() {
  local key_directory temporary_file provider_url model_id context_tokens input_price output_price provider_api_key
  load_saved_key
  key_directory="$(dirname "$key_file")"
  install -d -m 700 "$key_directory"
  read -rp 'OpenAI-compatible base URL (ending in /v1): ' provider_url
  read -rp 'Model ID: ' model_id
  read -rp 'Context window tokens [524288]: ' context_tokens
  context_tokens="${context_tokens:-524288}"
  read -rp 'Input price USD per million tokens [0]: ' input_price
  input_price="${input_price:-0}"
  read -rp 'Output price USD per million tokens [0]: ' output_price
  output_price="${output_price:-0}"
  read -rsp 'Paste provider API key (Enter reuses the saved key): ' provider_api_key
  echo
  provider_api_key="${provider_api_key:-${BENCHMARK_PROVIDER_API_KEY:-}}"
  if [[ ! "$provider_url" =~ ^https?://.+/v1/?$ ]]; then
    echo "Base URL must be an http(s) OpenAI-compatible URL ending in /v1" >&2
    return 1
  fi
  provider_url="${provider_url%/}"
  if [[ -z "$model_id" || -z "$provider_api_key" ]]; then
    echo "Model ID and API key cannot be empty" >&2
    return 1
  fi
  if [[ ! "$context_tokens" =~ ^[1-9][0-9]*$ ]] || (( context_tokens <= 12288 )); then
    echo "Context window tokens must be an integer greater than 12288" >&2
    return 1
  fi
  if [[ ! "$input_price" =~ ^[0-9]+([.][0-9]+)?$ || ! "$output_price" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
    echo "Token prices must be non-negative numbers" >&2
    return 1
  fi
  umask 077
  temporary_file="$(mktemp "$key_directory/.benchmark.env.XXXXXX")"
  {
    printf 'export BENCHMARK_PROVIDER_API_KEY=%q\n' "$provider_api_key"
    printf 'export BENCHMARK_PROVIDER_BASE_URL=%q\n' "$provider_url"
    printf 'export BENCHMARK_MODEL_ID=%q\n' "$model_id"
    printf 'export BENCHMARK_CONTEXT_TOKENS=%q\n' "$context_tokens"
    printf 'export BENCHMARK_INPUT_USD_PER_MILLION=%q\n' "$input_price"
    printf 'export BENCHMARK_OUTPUT_USD_PER_MILLION=%q\n' "$output_price"
  } > "$temporary_file"
  chmod 600 "$temporary_file"
  mv "$temporary_file" "$key_file"
  unset provider_api_key
  echo "Benchmark provider saved securely to $key_file"
}

require_file() {
  if [[ ! -e "$1" ]]; then
    echo "Missing required path: $1" >&2
    return 1
  fi
}

require_environment() {
  : "${FREETOSHOP_HISTORICAL_STAGE:?Set FREETOSHOP_HISTORICAL_STAGE to the preserved 2026-08-20 benchmark stage}"
  : "${FREETOSHOP_ORACLE_ROOT:?Set FREETOSHOP_ORACLE_ROOT to the controller-private FreetoShop evaluator checkout}"
  : "${BENCHMARK_PROVIDER_API_KEY:?Run setup-provider to save the provider API key}"
  : "${BENCHMARK_PROVIDER_BASE_URL:?Run setup-provider to save the provider base URL}"
  : "${BENCHMARK_MODEL_ID:?Run setup-provider to save the model ID}"
  : "${BENCHMARK_CONTEXT_TOKENS:?Run setup-provider to save the context window}"
  : "${BENCHMARK_SELECTOR_BASE_URL:?Set the local selector base URL}"
  : "${BENCHMARK_SELECTOR_MODEL_ID:?Set the local selector model ID}"
  require_file "$FREETOSHOP_HISTORICAL_STAGE/arm-a-pi/.git"
  require_file "$FREETOSHOP_HISTORICAL_STAGE/arm-b-orchid/.git"
  require_file "$FREETOSHOP_HISTORICAL_STAGE/pi-agent-a"
  require_file "$FREETOSHOP_HISTORICAL_STAGE/pi-agent-b"
  require_file "$FREETOSHOP_ORACLE_ROOT/.git"
  require_file "$FREETOSHOP_ORACLE_ROOT/tests/e2e/editor.spec.ts"
  require_file "$python_executable"
  require_file "$pi_executable"
  [[ "$($pi_executable --version)" == "0.84.2" ]] || {
    echo "Pi must be exactly 0.84.2" >&2
    return 1
  }
  "$python_executable" - "$BENCHMARK_SELECTOR_BASE_URL" "$BENCHMARK_SELECTOR_MODEL_ID" <<'PY'
import json, sys, urllib.request
url = sys.argv[1].rstrip("/") + "/models"
with urllib.request.urlopen(url, timeout=10) as response:
    models = json.load(response)
available = {item.get("id") for item in models.get("data", [])}
if sys.argv[2] not in available:
    raise SystemExit(f"local selector model is not available at {url}")
PY
}

check_readiness() {
  local failed=0
  echo "python=$($python_executable --version 2>&1)"
  echo "pi=$($pi_executable --version 2>&1)"
  if [[ -z "${BENCHMARK_PROVIDER_API_KEY:-}" ]]; then
    echo "provider_api_key=missing"
    failed=1
  else
    echo "provider_api_key=present"
  fi
  if [[ -z "${BENCHMARK_PROVIDER_BASE_URL:-}" ]]; then
    echo "provider_base_url=missing"
    failed=1
  else
    echo "provider_base_url=$BENCHMARK_PROVIDER_BASE_URL"
  fi
  if [[ -z "${BENCHMARK_MODEL_ID:-}" ]]; then
    echo "model_id=missing"
    failed=1
  else
    echo "model_id=$BENCHMARK_MODEL_ID"
  fi
  echo "provider_pricing_usd_per_million=input:${BENCHMARK_INPUT_USD_PER_MILLION:-0},output:${BENCHMARK_OUTPUT_USD_PER_MILLION:-0}"
  if "$python_executable" - "$BENCHMARK_SELECTOR_BASE_URL" "$BENCHMARK_SELECTOR_MODEL_ID" <<'PY'
import json, sys, urllib.request
try:
    with urllib.request.urlopen(sys.argv[1].rstrip("/") + "/models", timeout=5) as response:
        available = {item.get("id") for item in json.load(response).get("data", [])}
except Exception:
    raise SystemExit(1)
raise SystemExit(0 if sys.argv[2] in available else 1)
PY
  then
    echo "selector=$BENCHMARK_SELECTOR_MODEL_ID@$BENCHMARK_SELECTOR_BASE_URL"
  else
    echo "selector=missing_or_unavailable"
    failed=1
  fi
  if [[ -z "${FREETOSHOP_HISTORICAL_STAGE:-}" ]]; then
    echo "FREETOSHOP_HISTORICAL_STAGE=missing"
    failed=1
  elif [[ ! -e "$FREETOSHOP_HISTORICAL_STAGE/arm-a-pi/.git" || ! -e "$FREETOSHOP_HISTORICAL_STAGE/arm-b-orchid/.git" ]]; then
    echo "FREETOSHOP_HISTORICAL_STAGE=invalid"
    failed=1
  else
    echo "FREETOSHOP_HISTORICAL_STAGE=ready"
  fi
  if [[ -z "${FREETOSHOP_ORACLE_ROOT:-}" ]]; then
    echo "FREETOSHOP_ORACLE_ROOT=missing"
    failed=1
  elif [[ ! -e "$FREETOSHOP_ORACLE_ROOT/.git" || ! -e "$FREETOSHOP_ORACLE_ROOT/tests/e2e/editor.spec.ts" ]]; then
    echo "FREETOSHOP_ORACLE_ROOT=invalid"
    failed=1
  else
    echo "FREETOSHOP_ORACLE_ROOT=ready"
  fi
  if [[ -n "$(git -C "$root" status --short --untracked-files=no)" ]]; then
    echo "tracked_worktree=dirty"
    failed=1
  else
    echo "tracked_worktree=clean"
  fi
  if (( failed )); then
    echo "benchmark_environment=blocked"
    return 1
  fi
  echo "benchmark_environment=ready"
}

select_run_paths() {
  local stamp
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  export ORCHID_CORRECTED_RUN_ID="${ORCHID_CORRECTED_RUN_ID:-freetoshop_pi_vs_orchid_corrected_hybrid_rerun_$stamp}"
  export ORCHID_CORRECTED_ARTIFACT="${ORCHID_CORRECTED_ARTIFACT:-$root/artifacts/agent_benchmarks/$ORCHID_CORRECTED_RUN_ID}"
  export ORCHID_CORRECTED_STAGE="${ORCHID_CORRECTED_STAGE:-${ORCHID_BENCHMARK_WORK_ROOT:-$root/../benchmark_work}/$ORCHID_CORRECTED_RUN_ID}"
  export PYTHON_EXECUTABLE="$python_executable"
  export PI_EXECUTABLE="$pi_executable"
  export NPM_EXECUTABLE="${NPM_EXECUTABLE:-$(command -v npm)}"
}

load_run_paths() {
  if [[ -z "${ORCHID_CORRECTED_ARTIFACT:-}" || -z "${ORCHID_CORRECTED_STAGE:-}" ]]; then
    require_file "$last_run_env"
    # This file contains paths only, never credentials.
    # shellcheck disable=SC1090
    source "$last_run_env"
  fi
  export PYTHON_EXECUTABLE="$python_executable"
  export PI_EXECUTABLE="$pi_executable"
  export NPM_EXECUTABLE="${NPM_EXECUTABLE:-$(command -v npm)}"
}

save_run_paths() {
  {
    printf 'export ORCHID_CORRECTED_RUN_ID=%q\n' "$ORCHID_CORRECTED_RUN_ID"
    printf 'export ORCHID_CORRECTED_ARTIFACT=%q\n' "$ORCHID_CORRECTED_ARTIFACT"
    printf 'export ORCHID_CORRECTED_STAGE=%q\n' "$ORCHID_CORRECTED_STAGE"
  } > "$last_run_env"
}

prepare_benchmark() {
  select_run_paths
  require_environment
  "$python_executable" "$root/tools/benchmark_freetoshop_corrected_all_solar.py" freeze
  "$python_executable" "$root/tools/benchmark_freetoshop_corrected_all_solar.py" prepare
  "$python_executable" "$root/tools/benchmark_freetoshop_corrected_all_solar.py" verify
  save_run_paths
  echo "Prepared sealed run: $ORCHID_CORRECTED_RUN_ID"
}

start_gateway() {
  local database="$ORCHID_CORRECTED_STAGE/orchid-state/memory.db"
  local log_dir="$ORCHID_CORRECTED_ARTIFACT/gateway"
  local port="${ORCHID_BENCHMARK_PORT:-7332}"
  local backend_url="${BENCHMARK_PROVIDER_BASE_URL%/}"
  backend_url="${backend_url%/v1}"
  mkdir -p "$log_dir"
  [[ ! -e "$database" ]] || {
    echo "Refusing to reuse benchmark database: $database" >&2
    return 1
  }
  "$python_executable" - "$port" <<'PY'
import socket, sys
port = int(sys.argv[1])
with socket.socket() as sock:
    if sock.connect_ex(("127.0.0.1", port)) == 0:
        raise SystemExit(f"Refusing to reuse occupied benchmark port {port}")
PY
  export ORCHID_DB="$database"
  export ORCHID_BACKEND_URL="$backend_url"
  export ORCHID_BACKEND_API_KEY="$BENCHMARK_PROVIDER_API_KEY"
  export ORCHID_BACKEND_MODEL="$BENCHMARK_MODEL_ID"
  export ORCHID_SELECTOR_URL="$BENCHMARK_SELECTOR_BASE_URL"
  export ORCHID_SELECTOR_API_KEY="$BENCHMARK_SELECTOR_API_KEY"
  export ORCHID_SELECTOR_MODEL="$BENCHMARK_SELECTOR_MODEL_ID"
  export ORCHID_CANONICALIZER_ENABLED="0"
  export ORCHID_CONSOLIDATOR_URL="$BENCHMARK_PROVIDER_BASE_URL"
  export ORCHID_CONSOLIDATOR_API_KEY="$BENCHMARK_PROVIDER_API_KEY"
  export ORCHID_CONSOLIDATOR_MODEL="$BENCHMARK_MODEL_ID"
  export ORCHID_COMPACTION_CONTEXT_TOKENS="$BENCHMARK_CONTEXT_TOKENS"
  export ORCHID_COMPACTION_RESERVED_OUTPUT_TOKENS="8192"
  export ORCHID_COMPACTION_FIXED_OVERHEAD_TOKENS="4096"
  export ORCHID_COMPACTION_SAFETY_MARGIN="0.7"
  export ORCHID_COMPACTION_REQUEST_AMPLIFICATION="3.0"
  export ORCHID_COLD_MEMORY_MODE="off"
  export ORCHID_MODEL_TIMEOUT_SECONDS="180"

  "$python_executable" -m uvicorn memory_gateway.gateway:app --host 127.0.0.1 --port "$port" \
    >"$log_dir/stdout.log" 2>"$log_dir/stderr.log" &
  gateway_pid=$!
  export gateway_pid
  "$python_executable" - "$port" <<'PY'
import sys, time
import httpx
url = f"http://127.0.0.1:{int(sys.argv[1])}/healthz"
deadline = time.monotonic() + 20
while time.monotonic() < deadline:
    try:
        response = httpx.get(url, timeout=1)
        response.raise_for_status()
        break
    except Exception:
        time.sleep(0.25)
else:
    raise SystemExit("ORCHID gateway did not become healthy")
PY
  "$python_executable" - "$log_dir/PID.json" "$gateway_pid" "$database" "$port" <<'PY'
import datetime, json, pathlib, sys
pathlib.Path(sys.argv[1]).write_text(json.dumps({
    "pid": int(sys.argv[2]),
    "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "database": sys.argv[3],
    "port": int(sys.argv[4]),
}, indent=2) + "\n", encoding="utf-8")
PY
}

run_benchmark() {
  load_run_paths
  require_environment
  require_file "$ORCHID_CORRECTED_ARTIFACT/prelaunch/RESULTS.json"
  start_gateway
  trap 'if [[ -n "${gateway_pid:-}" ]]; then kill "$gateway_pid" 2>/dev/null || true; fi' EXIT INT TERM
  node "$root/tools/freetoshop_corrected_all_solar_driver.mjs"
  wait "$gateway_pid" 2>/dev/null || true
  trap - EXIT INT TERM
}

finalize_benchmark() {
  load_run_paths
  require_environment
  "$python_executable" "$root/tools/finalize_freetoshop_corrected_all_solar.py"
}

case "$command_name" in
  setup-key|setup-provider)
    save_provider
    ;;
  check)
    load_saved_key
    load_recovered_inputs
    check_readiness
    ;;
  prepare)
    load_saved_key
    load_recovered_inputs
    prepare_benchmark
    ;;
  run)
    load_saved_key
    load_recovered_inputs
    run_benchmark
    ;;
  finalize)
    load_saved_key
    load_recovered_inputs
    finalize_benchmark
    ;;
  all)
    load_saved_key
    load_recovered_inputs
    prepare_benchmark
    run_benchmark
    finalize_benchmark
    ;;
  *)
    echo "Usage: $0 {setup-provider|check|prepare|run|finalize|all}" >&2
    exit 2
    ;;
esac
