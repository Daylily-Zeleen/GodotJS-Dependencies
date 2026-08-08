#!/usr/bin/env bash
# Build node (Node.js as an embeddable static library) for iOS device (arm64).
# Uses the Xcode iphoneos SDK toolchain; configure.py supports --dest-os=ios.
# Usage: build-ios.sh [node_branch]   (default: v24.x)
set -euo pipefail

NODE_BRANCH="${1:-v24.x}"
WORKSPACE="${GITHUB_WORKSPACE:-$(pwd)}"
IOS_DEPLOYMENT_TARGET="${IOS_DEPLOYMENT_TARGET:-16.0}"

SDK_PATH="$(xcrun --sdk iphoneos --show-sdk-path)"
CLANG="$(xcrun --find clang)"
CLANGPP="$(xcrun --find clang++)"
echo "Using iOS Device SDK: ${SDK_PATH}"

# --- Fetch Node.js source (skip if already fetched by the fetch action) ---
cd "$WORKSPACE"
if [ ! -d "node/.git" ]; then
  git clone --depth 1 --branch "$NODE_BRANCH" https://github.com/nodejs/node.git node
fi

# --- Configure & build with iOS cross toolchain ---
cd node
export CC="${CLANG} -isysroot ${SDK_PATH} -arch arm64 -miphoneos-version-min=${IOS_DEPLOYMENT_TARGET}"
export CXX="${CLANGPP} -isysroot ${SDK_PATH} -arch arm64 -miphoneos-version-min=${IOS_DEPLOYMENT_TARGET}"
export LDFLAGS="-isysroot ${SDK_PATH} -arch arm64"
# Apple clang defaults to C++14; node v24 needs C++20 (abseil + ncrypto operator<=>)
export CXXFLAGS="-std=c++20"

./configure \
  --dest-os=ios \
  --dest-cpu=arm64 \
  --cross-compiling \
  --openssl-no-asm \
  --without-npm \
  --without-inspector \
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
# node v24 has no top-level include/; generate headers via tools/install.py
STAGE_DIR="$WORKSPACE/staging/node/ios_arm64_release"
mkdir -p "$STAGE_DIR"
python3 tools/install.py install --headers-only --dest-dir "$STAGE_DIR" --prefix "/"
if [ -f out/Release/config.gypi ]; then
  cp out/Release/config.gypi "$STAGE_DIR/"
fi
cp "$LIB" "$STAGE_DIR/"

echo "== staging layout =="
find "$WORKSPACE/staging" -type f | sort
