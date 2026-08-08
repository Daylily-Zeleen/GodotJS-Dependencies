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

# node.gypi unconditionally adds the openssl-cli test tool as a dependency of
# libnode (node_use_openssl && !node_shared_openssl). On iOS it cannot link:
# gyp emits GNU ld flags (--start-group/--end-group) that Apple ld64 rejects.
# We don't need this test binary, so drop it BEFORE configure (gyp reads the
# gypi at configure time to generate the Makefiles).
sed -i.bak '/openssl.gyp:openssl-cli/d' node.gypi
export CC="${CLANG} -isysroot ${SDK_PATH} -arch arm64 -miphoneos-version-min=${IOS_DEPLOYMENT_TARGET}"
# Apple clang defaults to C++14; node v24 needs C++20 (abseil + ncrypto
# operator<=>). Put -std=c++20 in CXX itself so gyp's HOST targets
# (obj.host: mksnapshot/abseil) inherit it too - CXXFLAGS only reaches
# obj.target targets.
export CXX="${CLANGPP} -isysroot ${SDK_PATH} -arch arm64 -miphoneos-version-min=${IOS_DEPLOYMENT_TARGET} -std=c++20"
export LDFLAGS="-isysroot ${SDK_PATH} -arch arm64"
export CXXFLAGS="-std=c++20"

./configure \
  --dest-os=ios \
  --dest-cpu=arm64 \
  --cross-compiling \
  --openssl-no-asm \
  --without-npm \
  --without-inspector \
  --without-report

# c-ares ships a macOS config (config/darwin/ares_config.h) which defines
# HAVE_SYS_RANDOM_H - that header exists on macOS but NOT on iOS. Undefine it
# so ares_rand.c falls back to arc4random_buf (available on iOS).
sed -i.bak 's/^#define HAVE_SYS_RANDOM_H 1/\/\* #undef HAVE_SYS_RANDOM_H *\//' \
  deps/cares/config/darwin/ares_config.h

# Build ONLY the 'node' target (which depends on libnode.a). The top-level
# 'make' builds ALL gyp targets including test programs (nop,
# overlapped-checker) that fail to link on iOS (gyp adds GNU ld --start-group
# which Apple ld64 rejects). configure only writes out/Makefile; the
# out/Release/ dir is created by gyp during the build, so pass BUILDTYPE.
# node configure sets gyp flavor to 'ios', which gyp's make generator does not
# recognize, so it falls back to LINK_COMMANDS_LINUX which embeds GNU-ld-only
# -Wl,--start-group/--end-group (Apple ld64 rejects) and drops the
# -framework CoreFoundation that abseil's cctz needs (attached via
# OTHER_LDFLAGS, an Xcode-only field). Patch the generated makefiles: strip
# start/end-group and append the CoreFoundation framework to the link rule.
find out -name 'Makefile' -o -name '*.mk' | while read -r mf; do
  sed -i.bak \
    -e 's/ -Wl,--start-group//g' \
    -e 's/ -Wl,--end-group//g' \
    -e 's/\($(LDFLAGS\.$(TOOLSET))\)/\1 -framework CoreFoundation/g' "$mf"
done

# Bypass the icupkg ACTION. gyp's comment for it is literally "Copy the .dat
# file, swapping endianness if needed" - icu_endianness is the HOST byte order
# (configure.py: icu_endianness = sys.byteorder[0]), and the iOS target is also
# little-endian, so `icupkg -t l` is a byte-identical no-op copy. The input
# deps/icu-tmp/icudt78l.dat already exists after configure (configure.py errors
# if it is missing). On macOS runners icupkg gets OOM-killed (Killed: 9 / Error
# 137) even at -j1, so skip it by pre-creating the expected gyp output with a
# fresh timestamp - make then skips the ACTION (output newer than input).
# Note: SHARED_INTERMEDIATE_DIR is out/Release/obj/gen (per the icupkg log).
mkdir -p out/Release/obj/gen
if [ -f deps/icu-tmp/icudt78l.dat ]; then
  cp deps/icu-tmp/icudt78l.dat out/Release/obj/gen/icudt78l.dat
  touch out/Release/obj/gen/icudt78l.dat
  echo "bypassed icupkg: copied deps/icu-tmp/icudt78l.dat -> out/Release/obj/gen/icudt78l.dat (byte-identical for little-endian)"
else
  echo "WARNING: deps/icu-tmp/icudt78l.dat not found - icupkg will run normally" >&2
fi
# Limit parallelism: macos runners OOM-kill icupkg (generates icudt78l.dat)
# when -j is too high (all cores compile + icupkg peak memory). -j2 is
# usually safe but the OOM is flaky, so retry: gyp's make is incremental, so
# after a killed job the retry only re-runs the remaining targets with much
# lower memory pressure (icupkg runs almost alone).
made=0
JOBS=2
for i in 1 2 3; do
  echo "=== make attempt $i (jobs=$JOBS) ==="
  if make -C out BUILDTYPE=Release node -j"$JOBS"; then
    made=1
    break
  fi
  echo "attempt $i failed (exit $?); killing leftover build procs and retrying"
  # When a parallel make dies (OOM Error 137 on icupkg), sibling clang/icupkg
  # child processes can be left running and keep consuming memory, starving the
  # retry (observed: -j1 retries die in ~1s). Kill leftovers before retrying.
  pkill -9 -f 'clang' 2>/dev/null || true
  pkill -9 -f 'cc1' 2>/dev/null || true
  pkill -9 -f 'icupkg' 2>/dev/null || true
  pkill -9 -f 'genccode' 2>/dev/null || true
  pkill -9 -f 'node_js2c' 2>/dev/null || true
  pkill -9 -f 'mksnapshot' 2>/dev/null || true
  pkill -9 -f 'gen-regexp' 2>/dev/null || true
  pkill -9 -f 'bytecode_builtins' 2>/dev/null || true
  sleep 5
  JOBS=1
done
if [ "$made" -ne 1 ]; then
  echo "node make failed after 3 attempts" >&2
  exit 1
fi

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
