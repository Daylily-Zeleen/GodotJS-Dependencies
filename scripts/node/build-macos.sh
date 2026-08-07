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
./configure \
  --dest-os=mac \
  --dest-cpu="$DEST_CPU" \
  --without-npm \
  --without-inspector \
  --without-etw \
  --without-dtrace \
  --without-report
make -j"$(sysctl -n hw.ncpu || echo 4)"

# --- Locate static library (path varies across versions) ---
LIB="$(find out -name 'libnode.a' -print -quit)"
if [ -z "$LIB" ]; then
  echo "libnode.a not found in build output" >&2
  find out -maxdepth 3 -name '*.a' >&2 || true
  exit 1
fi
echo "Found: $LIB"

# --- Stage artifacts ---
mkdir -p "$WORKSPACE/staging/node/macos_${DEST_CPU}_release"
cp -a include "$WORKSPACE/staging/node/macos_${DEST_CPU}_release/include"
if [ -f out/Release/config.gypi ]; then
  cp out/Release/config.gypi "$WORKSPACE/staging/node/macos_${DEST_CPU}_release/"
fi
cp "$LIB" "$WORKSPACE/staging/node/macos_${DEST_CPU}_release/"

echo "== staging layout =="
find "$WORKSPACE/staging" -type f | sort
