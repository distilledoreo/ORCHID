#!/usr/bin/env bash
# One-command install of the ORCHID gateway with SpecPrefill (production plan, Phase 5).
#
#   scripts/install-specprefill.sh --strata-config PATH [--student DIR] [--no-elastic] [--prefix DIR]
#
# Needs the existing Flash-Next setup (a Strata checkout and its engine config JSON).
# Creates a virtual environment, installs the gateway with the student runtime, asks
# before downloading the student model (1.7 GB, checksummed), and writes
# ~/.config/orchid/specprefill.env. With the CUDA toolkit present it also builds Strata's
# elastic engine from integrations/strata/elastic-vram.patch in a copy (the installed
# Strata is untouched): agents then run like native Flash-Next and the student borrows
# VRAM only when it helps. --no-elastic skips it (plain SpecPrefill for everything).
set -euo pipefail
REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
PREFIX="${XDG_DATA_HOME:-$HOME/.local/share}/orchid"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/orchid"
STRATA_CONFIG="" STUDENT="" ELASTIC=auto YES=0
while [ $# -gt 0 ]; do
  case "$1" in
    --strata-config) STRATA_CONFIG=$2; shift 2 ;;
    --student) STUDENT=$2; shift 2 ;;
    --prefix) PREFIX=$2; shift 2 ;;
    --elastic) ELASTIC=1; shift ;;
    --no-elastic) ELASTIC=0; shift ;;
    --yes) YES=1; shift ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done
[ -f "$STRATA_CONFIG" ] || { echo "--strata-config: the Flash-Next Strata engine config (JSON) is required" >&2; exit 2; }
STRATA_CONFIG=$(cd "$(dirname "$STRATA_CONFIG")" && pwd)/$(basename "$STRATA_CONFIG")

echo "== environment: $PREFIX/venv"
python3 -m venv "$PREFIX/venv"
"$PREFIX/venv/bin/pip" install --quiet --upgrade pip
"$PREFIX/venv/bin/pip" install --quiet "$REPO[specprefill]"
BIN="$PREFIX/venv/bin"

echo "== student model"
if [ -z "$STUDENT" ]; then
  STUDENT="$PREFIX/student"
  "$BIN/orchid-specprefill" fetch-student "$STUDENT" $([ $YES = 1 ] && echo --yes) || true
fi

ELASTIC_ENGINE=""
NVCC=$(command -v nvcc || ls -1 /usr/local/cuda*/bin/nvcc 2>/dev/null | sort -V | tail -1 || true)
if [ "$ELASTIC" = auto ]; then
  if [ -n "$NVCC" ]; then ELASTIC=1; else ELASTIC=0
    echo "(no CUDA toolkit found: plain SpecPrefill; agents run about 1.4x slower per call than with the elastic engine)"
  fi
fi
if [ $ELASTIC = 1 ]; then
  command -v cmake > /dev/null && command -v ninja > /dev/null || "$BIN/pip" install --quiet cmake ninja
  export PATH="$BIN:$PATH"
  echo "== elastic engine (a patched copy of Strata)"
  STRATA=$("$BIN/python" -c "import json,sys; print(json.load(open(sys.argv[1]))['cwd'])" "$STRATA_CONFIG")
  COPY="$PREFIX/strata-elastic"
  rm -rf "$COPY" && mkdir -p "$COPY"
  cp -a "$STRATA"/{CMakeLists.txt,cmake,include,src,sycl,serve,third_party,tests} "$COPY"/
  (cd "$COPY" && patch -p1 --quiet < "$REPO/integrations/strata/elastic-vram.patch")
  CUDA=${NVCC:-/usr/local/cuda/bin/nvcc}
  cmake -S "$COPY" -B "$COPY/build" -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_CUDA_ARCHITECTURES=60 \
    -DCMAKE_CUDA_COMPILER="$CUDA" -DSTRATA_ENABLE_CUDA=ON -DSTRATA_EXPERIMENTAL_SM60=ON -DSTRATA_NATIVE_EXPERTS=ON \
    -DSTRATA_GGML_DIR="$COPY/third_party/llama.cpp" > "$COPY/configure.log"
  ninja -C "$COPY/build" strata > "$COPY/build.log"
  ELASTIC_ENGINE="$COPY/build/strata"
fi

echo "== settings: $CONFIG_DIR/specprefill.env"
mkdir -p "$CONFIG_DIR"
cat > "$CONFIG_DIR/specprefill.env" <<ENV
# ORCHID gateway with SpecPrefill (memory_gateway/specprefill). Unset settings come from
# the measured table for this GPU. ORCHID_SPECPREFILL_ENGINE=native turns SpecPrefill off.
ORCHID_SPECPREFILL_ENABLED=true
ORCHID_SPECPREFILL_ENGINE=lean
ORCHID_SPECPREFILL_PRELOAD=true
ORCHID_SPECPREFILL_STATE_DIR=$PREFIX/state
ORCHID_DB=$PREFIX/gateway.db
ORCHID_SPECPREFILL_CONFIG=$STRATA_CONFIG
ORCHID_SPECPREFILL_STUDENT=$STUDENT
${ELASTIC_ENGINE:+ORCHID_SPECPREFILL_ELASTIC_ENGINE=$ELASTIC_ENGINE}
ENV
"$BIN/orchid-specprefill" doctor || true
echo
echo "Start the gateway:  $BIN/orchid-specprefill serve    (OpenAI API at http://127.0.0.1:18080/v1)"
