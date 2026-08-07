#!/usr/bin/env bash
# Build node (Node.js as an embeddable static library) for HarmonyOS (OpenHarmony) arm64.
# Uses the HarmonyOS Native SDK (OpenHarmony); configure.py supports --dest-os=openharmony.
# Requires OHOS_NATIVE_HOME (path to the HarmonyOS Native SDK) or OHOS_SDK_URL to download it.
# Usage: build-ohos.sh [node_branch]   (default: v24.x)
set -euo pipefail

NODE_BRANCH="${1:-v24.x}"
WORKSPACE="${GITHUB_WORKSPACE:-$(pwd)}"

# --- Locate HarmonyOS Native SDK ---
if [ -z "${OHOS_NATIVE_HOME:-}" ] || [ ! -x "${OHOS_NATIVE_HOME}/llvm/bin/aarch64-unknown-linux-ohos-clang" ]; then
  if [ -n "${OHOS_SDK_URL:-}" ]; then
    echo "Downloading HarmonyOS SDK from ${OHOS_SDK_URL}..."
    SDK_ROOT="$WORKSPACE/ohos-sdk"
    mkdir -p "$SDK_ROOT"
    curl --fail --location --retry 3 --output "$WORKSPACE/ohos-sdk.tar.gz" "${OHOS_SDK_URL}"
    tar -xzf "$WORKSPACE/ohos-sdk.tar.gz" -C "$SDK_ROOT"
    while IFS= read -r package_file; do
      package_dir="$(dirname "${package_file}")"
      echo "Extracting HarmonyOS SDK package: ${package_file}"
      unzip -q -o "${package_file}" -d "${package_dir}"
    done < <(find "$SDK_ROOT" -type f -name '*.zip' | sort)
    CLANG_PATH="$(find "$SDK_ROOT" -path '*/native/llvm/bin/aarch64-unknown-linux-ohos-clang' -print -quit)"
    if [ -z "${CLANG_PATH}" ]; then
      echo "HarmonyOS Native SDK clang not found in downloaded SDK." >&2
      exit 1
    fi
    OHOS_NATIVE_HOME="$(cd "$(dirname "${CLANG_PATH}")/../.." && pwd)"
    echo "Installed HarmonyOS Native SDK: ${OHOS_NATIVE_HOME}"
  else
    echo "OHOS_NATIVE_HOME is not set and no OHOS_SDK_URL provided. Cannot build for HarmonyOS." >&2
    exit 1
  fi
fi

# --- Fetch Node.js source (skip if already fetched by the fetch action) ---
cd "$WORKSPACE"
if [ ! -d "node/.git" ]; then
  git clone --depth 1 --branch "$NODE_BRANCH" https://github.com/nodejs/node.git node
fi

# --- Configure & build with OHOS cross toolchain ---
cd node
export CC="$OHOS_NATIVE_HOME/llvm/bin/aarch64-unknown-linux-ohos-clang"
export CXX="$OHOS_NATIVE_HOME/llvm/bin/aarch64-unknown-linux-ohos-clang++"
export AR="$OHOS_NATIVE_HOME/llvm/bin/llvm-ar"
export LD="$OHOS_NATIVE_HOME/llvm/bin/ld.lld"
export STRIP="$OHOS_NATIVE_HOME/llvm/bin/llvm-strip"

./configure \
  --dest-os=openharmony \
  --dest-cpu=arm64 \
  --cross-compiling \
  --without-npm \
  --without-inspector \
  --without-etw \
  --without-dtrace \
  --without-perfctr \
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
mkdir -p "$WORKSPACE/staging/node/ohos_arm64_release"
cp -a include "$WORKSPACE/staging/node/ohos_arm64_release/include"
if [ -f out/Release/config.gypi ]; then
  cp out/Release/config.gypi "$WORKSPACE/staging/node/ohos_arm64_release/"
fi
cp "$LIB" "$WORKSPACE/staging/node/ohos_arm64_release/"

echo "== staging layout =="
find "$WORKSPACE/staging" -type f | sort
