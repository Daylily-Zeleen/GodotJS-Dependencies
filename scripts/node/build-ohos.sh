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

# The OHOS toolchain is clang-17 based (same family as NDK r26b clang 17.0.2),
# which SIGSEGVs deterministically in Sema::ActOnAttributedStmt when parsing an
# attributed statement (V8_INLINE_STATEMENT == [[clang::always_inline]]) that
# directly follows a 'case' label, as used by
# FastJsonStringifier::SerializeObjectKey in deps/v8/src/json/json-stringifier.cc.
# V8_INLINE_STATEMENT is only a performance hint (force-inline for a type-switch
# dispatcher), so removing it is semantics-neutral.
python3 - <<'PY'
import io

path = 'deps/v8/src/json/json-stringifier.cc'
with io.open(path, encoding='utf-8') as f:
    s = f.read()

old = 'V8_INLINE_STATEMENT '
n = s.count(old)
if n == 0:
    print('WARNING: V8_INLINE_STATEMENT not found in json-stringifier.cc '
          '(node bumped v8 and the pattern moved?) - skipping clang-17 crash patch')
else:
    print('json-stringifier.cc: removed %d V8_INLINE_STATEMENT statement attribute(s) '
          '(clang-17 ActOnAttributedStmt crash workaround)' % n)
s = s.replace(old, '')

with io.open(path, 'w', encoding='utf-8') as f:
    f.write(s)
PY

./configure \
  --dest-os=openharmony \
  --dest-cpu=arm64 \
  --cross-compiling \
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
# Layout mirrors moluopro/libnode releases: libnode/<platform>/<arch>/libnode.a
# with one shared libnode/include/ (node header install flattened to include/).
LIBDIR="$WORKSPACE/staging/libnode/ohos/arm64"
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
