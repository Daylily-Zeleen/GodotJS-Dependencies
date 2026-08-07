#!/usr/bin/env bash
# Build node (Node.js as an embeddable static library) for Linux x86_64.
# Usage: build-linux.sh [node_branch]   (default: v24.x)
set -euo pipefail

NODE_BRANCH="${1:-v24.x}"
WORKSPACE="${GITHUB_WORKSPACE:-$(pwd)}"

# --- Install build dependencies ---
SUDO=""
if command -v sudo >/dev/null 2>&1; then SUDO="sudo"; fi
$SUDO apt-get clean || true
$SUDO rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/* || true
$SUDO DEBIAN_FRONTEND=noninteractive apt-get update
$SUDO DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
  git ca-certificates python3 python3-pip gcc-12 g++-12 make

# --- Fetch Node.js source (skip if already fetched by the fetch action) ---
cd "$WORKSPACE"
if [ ! -d "node/.git" ]; then
  git clone --depth 1 --branch "$NODE_BRANCH" https://github.com/nodejs/node.git node
fi

# --- Configure & build ---
cd node
export CC=gcc-12
export CXX=g++-12
./configure \
  --dest-cpu=x64 \
  --without-npm \
  --without-inspector \
  --without-report
make -j"$(nproc)"

# --- Locate static library (path varies across versions) ---
LIB="$(find out -name 'libnode.a' -print -quit)"
if [ -z "$LIB" ]; then
  echo "libnode.a not found in build output" >&2
  find out -maxdepth 3 -name '*.a' >&2 || true
  exit 1
fi
echo "Found: $LIB"

# --- Stage artifacts ---
mkdir -p "$WORKSPACE/staging/node/linux_x86_64_release"
cp -a include "$WORKSPACE/staging/node/linux_x86_64_release/include"
if [ -f out/Release/config.gypi ]; then
  cp out/Release/config.gypi "$WORKSPACE/staging/node/linux_x86_64_release/"
fi
cp "$LIB" "$WORKSPACE/staging/node/linux_x86_64_release/"

echo "== staging layout =="
find "$WORKSPACE/staging" -type f | sort
