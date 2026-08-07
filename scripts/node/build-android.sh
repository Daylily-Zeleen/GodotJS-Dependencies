#!/usr/bin/env bash
# Build node (Node.js as an embeddable static library) for Android arm64.
# Uses the Android NDK toolchain; configure.py supports --dest-os=android.
# Usage: build-android.sh [node_branch] [abi]   (default: v24.x arm64)
set -euo pipefail

NODE_BRANCH="${1:-v24.x}"
DEST_CPU="${2:-arm64}"
WORKSPACE="${GITHUB_WORKSPACE:-$(pwd)}"
ANDROID_NDK_VERSION="${ANDROID_NDK_VERSION:-r23c}"
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
TOOLCHAIN="$NDK_ROOT/toolchains/llvm/prebuilt/linux-x86_64"
case "$DEST_CPU" in
  arm64) TRIPLE="aarch64-linux-android" ;;
  arm)   TRIPLE="armv7a-linux-androideabi" ;;
  x64)   TRIPLE="x86_64-linux-android" ;;
  *)     echo "Unsupported dest_cpu: $DEST_CPU" >&2; exit 1 ;;
esac

export CC="$TOOLCHAIN/bin/${TRIPLE}${ANDROID_API}-clang"
export CXX="$TOOLCHAIN/bin/${TRIPLE}${ANDROID_API}-clang++"
export AR="$TOOLCHAIN/bin/llvm-ar"
export LD="$TOOLCHAIN/bin/ld"
export STRIP="$TOOLCHAIN/bin/llvm-strip"
export CPPFLAGS="-D__ANDROID_API__=${ANDROID_API}"

./configure \
  --dest-os=android \
  --dest-cpu="$DEST_CPU" \
  --cross-compiling \
  --without-npm \
  --without-inspector \
  --without-etw \
  --without-dtrace \
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
mkdir -p "$WORKSPACE/staging/node/android_${DEST_CPU}_release"
cp -a include "$WORKSPACE/staging/node/android_${DEST_CPU}_release/include"
if [ -f out/Release/config.gypi ]; then
  cp out/Release/config.gypi "$WORKSPACE/staging/node/android_${DEST_CPU}_release/"
fi
cp "$LIB" "$WORKSPACE/staging/node/android_${DEST_CPU}_release/"

echo "== staging layout =="
find "$WORKSPACE/staging" -type f | sort
