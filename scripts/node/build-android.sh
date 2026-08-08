#!/usr/bin/env bash
# Build node (Node.js as an embeddable static library) for Android arm64.
# Uses the Android NDK toolchain; configure.py supports --dest-os=android.
# Usage: build-android.sh [node_branch] [abi]   (default: v24.x arm64)
set -euo pipefail

NODE_BRANCH="${1:-v24.x}"
DEST_CPU="${2:-arm64}"
WORKSPACE="${GITHUB_WORKSPACE:-$(pwd)}"
ANDROID_NDK_VERSION="${ANDROID_NDK_VERSION:-r25c}"  # r23c clang-12 can't parse target(armv8-a+aes+crc) in zlib crc32_simd
ANDROID_API="${ANDROID_API:-28}"

SUDO=""
if command -v sudo >/dev/null 2>&1; then SUDO="sudo"; fi

# --- Install build dependencies ---
$SUDO apt-get clean || true
$SUDO rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/* || true
$SUDO DEBIAN_FRONTEND=noninteractive apt-get update
$SUDO DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
  git ca-certificates python3 python3-pip make unzip

# --- Fetch Android NDK (cached by workflow if possible) ---
if [ ! -d "$WORKSPACE/android-ndk-${ANDROID_NDK_VERSION}" ]; then
  echo "Downloading NDK ${ANDROID_NDK_VERSION}..."
  curl -sL -o "$WORKSPACE/android-ndk.zip" \
    "https://dl.google.com/android/repository/android-ndk-${ANDROID_NDK_VERSION}-linux.zip"
  unzip -q "$WORKSPACE/android-ndk.zip" -d "$WORKSPACE"
fi
NDK_ROOT="$WORKSPACE/android-ndk-${ANDROID_NDK_VERSION}"

# --- Fetch Node.js source (skip if already fetched by the fetch action) ---
cd "$WORKSPACE"
if [ ! -d "node/.git" ]; then
  git clone --depth 1 --branch "$NODE_BRANCH" https://github.com/nodejs/node.git node
fi

# --- Configure & build with NDK cross toolchain ---
cd node
case "$DEST_CPU" in
  arm64) NDK_ARCH="arm64" ;;
  arm)   NDK_ARCH="arm" ;;
  x64)   NDK_ARCH="x64" ;;
  *)     echo "Unsupported dest_cpu: $DEST_CPU" >&2; exit 1 ;;
esac

# node v24 android builds are driven by ./android-configure (sets GYP_DEFINES
# android_ndk_path). Usage: android-configure <ndk_path> <api> <arch>
./android-configure "$NDK_ROOT" "$ANDROID_API" "$NDK_ARCH"
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
# node v24 has no top-level include/; generate headers via tools/install.py
STAGE_DIR="$WORKSPACE/staging/node/android_${DEST_CPU}_release"
mkdir -p "$STAGE_DIR"
python3 tools/install.py install --headers-only --dest-dir "$STAGE_DIR" --prefix "/"
if [ -f out/Release/config.gypi ]; then
  cp out/Release/config.gypi "$STAGE_DIR/"
fi
cp "$LIB" "$STAGE_DIR/"

echo "== staging layout =="
find "$WORKSPACE/staging" -type f | sort
