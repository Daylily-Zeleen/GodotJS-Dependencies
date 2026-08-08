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

# node's system root-cert lookup (src/crypto/crypto_context.cc) uses the
# macOS-only SecTrustSettings API (kSecTrustSettings* keys/domains live in
# SecTrustSettings.h, which the iOS SDK does not ship). Inject a shim header
# that defines them for iOS and stubs the API to errSecItemNotFound so
# certificates fall back to SecTrustEvaluateWithError (see
# scripts/node/ios-sectrustsettings.h). macOS builds are unaffected.
cp "$WORKSPACE/Scripts/scripts/node/ios-sectrustsettings.h" src/crypto/
# Insert the shim include AFTER <Security/Security.h> so the shim's
# #define SecTrustSettingsCopyTrustSettings does not rewrite the SDK's own
# declaration (which would cause a redefinition error).
perl -i.bak -pe 's{(#include <Security/Security\.h>)}{$1\n#include "ios-sectrustsettings.h"}' src/crypto/crypto_context.cc

export CC="${CLANG} -isysroot ${SDK_PATH} -arch arm64 -miphoneos-version-min=${IOS_DEPLOYMENT_TARGET}"
# Apple clang defaults to C++14; node v24 needs C++20 (abseil + ncrypto
# operator<=>). Put -std=c++20 in CXX itself so gyp's HOST targets
# (obj.host: mksnapshot/abseil) inherit it too - CXXFLAGS only reaches
# obj.target targets.
export CXX="${CLANGPP} -isysroot ${SDK_PATH} -arch arm64 -miphoneos-version-min=${IOS_DEPLOYMENT_TARGET} -std=c++20"
export LDFLAGS="-isysroot ${SDK_PATH} -arch arm64"
export CXXFLAGS="-std=c++20"
# CRITICAL: gyp separates the host toolset when --cross-compiling
# (configure.py sets want_separate_host_toolset=1) and builds it with
# CC.host/CXX.host, which fall back to the CC/CXX env vars when CC_host/
# CXX_host are unset (gyp make.py GetEnvironFallback). Our CC/CXX carry
# -isysroot iphoneos -arch arm64 -miphoneos-version-min, so without this the
# host tools (icupkg, genccode, mksnapshot, torque, gen-regexp,
# bytecode_builtins_list_generator) are linked as iOS-platform Mach-O
# binaries - macOS refuses to exec those (instant SIGKILL "Killed: 9", which
# we had misread as OOM). Set CC_host/CXX_host to the plain macOS toolchain
# so host tools build as native arm64 macOS binaries and actually run.
export CC_host="clang"
export CXX_host="clang++ -std=c++20"

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
# Bypass the icupkg ACTION. gyp's comment for it is literally "Copy the .dat
# file, swapping endianness if needed" - icu_endianness is the HOST byte order
# (configure.py: icu_endianness = sys.byteorder[0]), and the iOS target is also
# little-endian, so `icupkg -t l` is a byte-identical no-op copy. On macOS
# runners icupkg gets OOM-killed (Killed: 9 / Error 137) even at -j1. Pre-
# touching the output is NOT enough because the ACTION rule also depends on the
# freshly-linked $(builddir)/icupkg binary (always newer than our copy), so
# make re-runs the ACTION. Instead, replace the recipe cmd with `true` (the
# output was pre-created below; the ACTION just needs to exit 0). We tried
# rewriting the command to /bin/cp but that broke quoting for the
# "$(builddir)/icupkg" form (left a dangling quote). `true` has no quoting.
# NOTE: all of the above (icupkg bypass, pre-copy) became UNNECESSARY once
# CC_host/CXX_host make the host tools native macOS binaries - icupkg and
# genccode now run normally. Only the link-rule fixes below (strip GNU-ld
# flags, add CoreFoundation) are still required.
find out \( -name 'Makefile' -o -name '*.mk' \) | while read -r mf; do
  sed -i.bak \
    -e 's/ -Wl,--start-group//g' \
    -e 's/ -Wl,--end-group//g' \
    -e 's/\($(LDFLAGS\.$(TOOLSET))\)/\1 -framework CoreFoundation/g' "$mf"
done
# Limit parallelism: the earlier "OOM-kill" of host tools was actually macOS
# rejecting iOS-platform host binaries at exec (see CC_host comment above) -
# with native host tools the build no longer dies there, but the runner only
# has 7 GB RAM and V8 host tools (torque/mksnapshot) are memory-hungry, so
# stay on -j1. Keep the retry loop as cheap insurance: gyp's make is
# incremental, and we aggressively kill leftover build processes between
# retries.
made=0
for i in 1 2 3; do
  echo "=== make attempt $i (jobs=1) ==="
  if make -C out BUILDTYPE=Release node -j1; then
    made=1
    break
  fi
  echo "attempt $i failed (exit $?); killing leftover build procs and retrying"
  pkill -9 -f 'out/Release' 2>/dev/null || true
  pkill -9 -f 'clang' 2>/dev/null || true
  pkill -9 -f 'cc1' 2>/dev/null || true
  pkill -9 -f 'icupkg' 2>/dev/null || true
  pkill -9 -f 'genccode' 2>/dev/null || true
  pkill -9 -f 'node_js2c' 2>/dev/null || true
  pkill -9 -f 'mksnapshot' 2>/dev/null || true
  pkill -9 -f 'gen-regexp' 2>/dev/null || true
  pkill -9 -f 'bytecode_builtins' 2>/dev/null || true
  pkill -9 -f 'cctz' 2>/dev/null || true
  pkill -9 -f 'gyp-mac-tool' 2>/dev/null || true
  sleep 8
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
# Layout mirrors moluopro/libnode releases: libnode/<platform>/<arch>/libnode.a
# with one shared libnode/include/ (node header install flattened to include/).
LIBDIR="$WORKSPACE/staging/libnode/ios/arm64"
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
