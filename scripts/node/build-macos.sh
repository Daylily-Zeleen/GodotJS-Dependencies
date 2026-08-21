#!/usr/bin/env bash
# Build node (Node.js as an embeddable static library) for macOS arm64.
# Usage: build-macos.sh [node_branch] [dest_cpu]   (default: v24.x arm64)
set -euo pipefail

NODE_BRANCH="${1:-v24.x}"
DEST_CPU="${2:-arm64}"
WORKSPACE="${GITHUB_WORKSPACE:-$(pwd)}"

# --- Ensure CMake/python available (macOS runners) ---
if ! command -v python3 >/dev/null 2>&1; then
  brew install python || true
fi

# --- Fetch Node.js source (skip if already fetched by the fetch action) ---
cd "$WORKSPACE"
if [ ! -d "node/.git" ]; then
  git clone --depth 1 --branch "$NODE_BRANCH" https://github.com/nodejs/node.git node
fi

# --- Configure & build ---
cd node
bash "$WORKSPACE/Scripts/scripts/node/apply_icu_profile.sh" "$PWD"
python3 "$WORKSPACE/Scripts/scripts/node/patch_rtti.py" "$PWD" macos
./configure \
  --dest-os=mac \
  --dest-cpu="$DEST_CPU" \
  --with-intl=small-icu \
  --with-icu-locales=root,en,en_GB,en_US,es,es_ES,es_MX,fr,fr_CA,fr_FR,ru,ru_RU,zh,zh_Hans,zh_Hans_CN,zh_Hans_HK,zh_Hant,zh_Hant_HK,zh_Hant_TW \
  --without-npm \
  --without-inspector \
  --without-report
python3 "$WORKSPACE/Scripts/scripts/node/verify_icu_config.py" config.gypi
# macos-latest is an M1 runner with only 7GB RAM; V8 host tools
# (mksnapshot/torque) are memory-hungry and full ncpu parallelism can OOM
# them. gyp's make is incremental, so on failure kill leftover build procs
# and retry at -j1 - the retry only compiles the remaining files under much
# lower memory pressure (same pattern as the android/ios scripts).
made=0
JOBS="$(sysctl -n hw.ncpu || echo 4)"
for i in 1 2 3; do
  echo "=== make attempt $i (jobs=$JOBS) ==="
  if make -j"$JOBS"; then
    made=1
    break
  fi
  echo "attempt $i failed (exit $?); killing leftover build procs and retrying"
  pkill -9 -f 'out/Release' 2>/dev/null || true
  pkill -9 -f 'clang' 2>/dev/null || true
  pkill -9 -f 'cc1' 2>/dev/null || true
  pkill -9 -f 'icupkg' 2>/dev/null || true
  pkill -9 -f 'mksnapshot' 2>/dev/null || true
  pkill -9 -f 'genccode' 2>/dev/null || true
  pkill -9 -f 'node_js2c' 2>/dev/null || true
  sleep 8
  JOBS=1
done
if [ "$made" -ne 1 ]; then
  echo "node make failed after 3 attempts" >&2
  exit 1
fi
python3 "$WORKSPACE/Scripts/scripts/node/verify_icu_data.py" out

# --- Locate static library (path varies across versions) ---
LIB="$(find out -name 'libnode.a' -print -quit)"
if [ -z "$LIB" ]; then
  echo "libnode.a not found in build output" >&2
  find out -maxdepth 3 -name '*.a' >&2 || true
  exit 1
fi
echo "Found: $LIB"

# --- Stage artifacts ---
# Layout mirrors moluopro/libnode releases: libnode/<platform>/<arch>/libnode.a
# with one shared libnode/include/ (node header install flattened to include/).
LIBDIR="$WORKSPACE/staging/libnode/macos/${DEST_CPU}"
HDRS="$WORKSPACE/staging/libnode/include"
rm -rf "$HDRS"
python3 tools/install.py install --headers-only --dest-dir "$HDRS" --prefix "/"
# install.py lays headers out under include/node/; flatten to include/ so the
# package matches the upstream libnode release layout.
mv "$HDRS"/include/node/* "$HDRS"/
rm -rf "$HDRS"/include
if [ -f out/Release/config.gypi ]; then
  cp out/Release/config.gypi "$HDRS/"
fi
mkdir -p "$LIBDIR"
cp "$LIB" "$LIBDIR/"

echo "== staging layout =="
find "$WORKSPACE/staging" -type f | sort
