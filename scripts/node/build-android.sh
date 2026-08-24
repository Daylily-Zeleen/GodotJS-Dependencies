#!/usr/bin/env bash
# Build node (Node.js as an embeddable static library) for Android arm64.
# Uses the Android NDK toolchain; configure.py supports --dest-os=android.
# Usage: build-android.sh [node_branch] [abi]   (default: v24.x arm64)
set -euo pipefail

NODE_BRANCH="${1:-v24.x}"
DEST_CPU="${2:-arm64}"
WORKSPACE="${GITHUB_WORKSPACE:-$(pwd)}"
ANDROID_NDK_VERSION="${ANDROID_NDK_VERSION:-r26b}"  # clang 17 supports target(armv8-a+aes+crc) in zlib crc32_simd (r23c=clang12/r25c=clang14 crash)
ANDROID_API="${ANDROID_API:-28}"

SUDO=""
if command -v sudo >/dev/null 2>&1; then SUDO="sudo"; fi

# --- Install build dependencies ---
$SUDO apt-get clean || true
$SUDO rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/* || true
$SUDO DEBIAN_FRONTEND=noninteractive apt-get update
$SUDO DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
  git ca-certificates python3 python3-pip make unzip gcc-12 g++-12

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
bash "$WORKSPACE/Scripts/scripts/node/apply_icu_profile.sh" "$PWD"
python3 "$WORKSPACE/Scripts/scripts/node/patch_rtti.py" "$PWD" android
case "$DEST_CPU" in
  arm64) NDK_ARCH="arm64" ;;
  arm)   NDK_ARCH="arm" ;;
  x64)   NDK_ARCH="x64" ;;
  *)     echo "Unsupported dest_cpu: $DEST_CPU" >&2; exit 1 ;;
esac

# android-configure exports CC/CXX=NDK clang for ALL targets, but host tools
# (obj.host: mksnapshot, torque, etc.) must use the system toolchain - NDK
# bionic lacks glibc-only backtrace_symbols used by v8 stack_trace_posix.cc.
# IMPORTANT: gyp reads CC_host/CXX_host when generating Makefiles, so they
# must be exported BEFORE ./android-configure runs.
export CC_host="${CC_host:-gcc-12}"
export CXX_host="${CXX_host:-g++-12}"
export AR_host="${AR_host:-ar}"

# node.gypi unconditionally adds the openssl-cli test tool as a dependency of
# libnode (node_use_openssl && !node_shared_openssl). On Android it is not
# needed for the embedded static library and older Node/gyp combinations try
# to link it without the NDK cpufeatures implementation, so drop it BEFORE
# configure (gyp reads the gypi at configure time).
sed -i.bak '/openssl.gyp:openssl-cli/d' node.gypi

# Node's bundled zlib still uses the removed NDK cpu-features library on
# Android. NDK r26b no longer ships <cpu-features.h> / android_getCpuFeatures;
# use the supported getauxval() HWCAP interface instead. Patch the fetched
# Node source before configure so gyp compiles the same runtime feature
# detection for arm64 and arm32 without an extra library.
python3 - <<'PY'
from pathlib import Path

path = Path('deps/zlib/cpu_features.c')
s = path.read_text(encoding='utf-8')

old_include = '''#if defined(ARMV8_OS_ANDROID)
#include <cpu-features.h>
#elif defined(ARMV8_OS_LINUX)'''
new_include = '''#if defined(ARMV8_OS_ANDROID)
#include <asm/hwcap.h>
#include <sys/auxv.h>
#elif defined(ARMV8_OS_LINUX)'''

old_android = '''#if defined(ARMV8_OS_ANDROID) && defined(__aarch64__)
    uint64_t features = android_getCpuFeatures();
    arm_cpu_enable_crc32 = !!(features & ANDROID_CPU_ARM64_FEATURE_CRC32);
    arm_cpu_enable_pmull = !!(features & ANDROID_CPU_ARM64_FEATURE_PMULL);
#elif defined(ARMV8_OS_ANDROID) /* aarch32 */
    uint64_t features = android_getCpuFeatures();
    arm_cpu_enable_crc32 = !!(features & ANDROID_CPU_ARM_FEATURE_CRC32);
    arm_cpu_enable_pmull = !!(features & ANDROID_CPU_ARM_FEATURE_PMULL);
#elif defined(ARMV8_OS_LINUX) && defined(__aarch64__)'''
new_android = '''#if defined(ARMV8_OS_ANDROID) && defined(__aarch64__)
    unsigned long features = getauxval(AT_HWCAP);
#if defined(HWCAP_CRC32)
    arm_cpu_enable_crc32 = !!(features & HWCAP_CRC32);
#else
    arm_cpu_enable_crc32 = 0;
#endif
#if defined(HWCAP_PMULL)
    arm_cpu_enable_pmull = !!(features & HWCAP_PMULL);
#else
    arm_cpu_enable_pmull = 0;
#endif
#elif defined(ARMV8_OS_ANDROID) /* aarch32 */
#if defined(AT_HWCAP2)
    unsigned long features = getauxval(AT_HWCAP2);
#else
    unsigned long features = 0;
#endif
#if defined(HWCAP2_CRC32)
    arm_cpu_enable_crc32 = !!(features & HWCAP2_CRC32);
#else
    arm_cpu_enable_crc32 = 0;
#endif
#if defined(HWCAP2_PMULL)
    arm_cpu_enable_pmull = !!(features & HWCAP2_PMULL);
#else
    arm_cpu_enable_pmull = 0;
#endif
#elif defined(ARMV8_OS_LINUX) && defined(__aarch64__)'''

if 'android_getCpuFeatures();' not in s and '#include <cpu-features.h>' not in s:
    print('zlib cpu feature detection already uses a supported Android API')
else:
    if old_include not in s or old_android not in s:
        raise SystemExit('ERROR: unsupported Node zlib cpu_features.c layout; refusing an unverified Android patch')
    s = s.replace(old_include, new_include, 1)
    s = s.replace(old_android, new_android, 1)
    if 'android_getCpuFeatures();' in s or '#include <cpu-features.h>' in s:
        raise SystemExit('ERROR: obsolete Android cpu-features implementation remains after patch')
    path.write_text(s, encoding='utf-8')
    print('zlib cpu_features.c: replaced NDK cpu-features with getauxval HWCAP detection')

# Keep this workaround auditable: the build must not continue if the expected
# obsolete API is still present after a supposedly successful patch.
patched = path.read_text(encoding='utf-8')
if '#include <cpu-features.h>' in patched or 'android_getCpuFeatures();' in patched:
    raise SystemExit('ERROR: obsolete Android cpu-features implementation remains')
PY

# v8.gyp (tools/v8_gypfiles) adds the wasm trap-handler sources
# (handler-inside-posix.cc / handler-outside-posix.cc) and the simulator probe
# helper (handler-outside-simulator.cc) to v8_base_without_compiler only under
# OS in "linux mac ios openharmony" / "linux mac win openharmony" (upstream
# riscv64/loong64 fix, nodejs/node#52888). OS=="android" is missing, so when
# cross-compiling for Android arm64 the host mksnapshot (built under the arm64
# simulator on x64) links v8_base_without_compiler WITHOUT those objects and
# fails with undefined references to trap_handler::TryHandleSignal /
# RegisterDefaultTrapHandler and v8_internal_simulator_ProbeMemory. Patch the
# OS lists to include android BEFORE configure (gyp reads v8.gyp to emit the
# Makefiles). Exact-string replacements; each pattern appears only in the
# arm64 blocks (both the .cc sources and the v8_internal_headers .h lists).
python3 - <<'PY'
import io

path = 'tools/v8_gypfiles/v8.gyp'
with io.open(path, encoding='utf-8') as f:
    s = f.read()

repls = [
    ('and (OS in "linux mac ios openharmony")',
     'and (OS in "linux mac ios openharmony android")'),
    ('and (OS in "linux mac openharmony")',
     'and (OS in "linux mac openharmony android")'),
    ('and (OS in "linux mac win openharmony")',
     'and (OS in "linux mac win openharmony android")'),
]
for old, new in repls:
    n = s.count(old)
    if n == 0:
        print('WARNING: v8.gyp pattern not found: %s' % old)
    else:
        print('v8.gyp: patched %d occurrence(s) of %s' % (n, old))
    s = s.replace(old, new)

with io.open(path, 'w', encoding='utf-8') as f:
    f.write(s)
PY

# NDK r26b ships clang 17.0.2, which SIGSEGVs deterministically in
# Sema::ActOnAttributedStmt when parsing an attributed statement
# (V8_INLINE_STATEMENT == [[clang::always_inline]]) that directly follows a
# 'case' label, as used by FastJsonStringifier::SerializeObjectKey in
# deps/v8/src/json/json-stringifier.cc. The same file compiles fine with the
# host gcc-12 and with Apple clang on iOS. V8_INLINE_STATEMENT is only a
# performance hint (force-inline for a type-switch dispatcher), so removing it
# is semantics-neutral. The macro is defined in objects/string-inl.h and used
# only by json-stringifier.cc; other TUs that parse the same macro under case
# labels (e.g. String::VisitStringTypedDispatch) compile fine, so we only
# patch this one file.
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
          '(NDK r26b clang-17 ActOnAttributedStmt crash workaround)' % n)
s = s.replace(old, '')

with io.open(path, 'w', encoding='utf-8') as f:
    f.write(s)
PY

# node v24 android builds are driven by ./android-configure (sets GYP_DEFINES
# android_ndk_path). The upstream wrapper does not forward configure flags, so
# install the exact profile and add the ICU flags to its configure command before invoking it.
python3 - <<'PY'
from pathlib import Path

path = Path('android_configure.py')
s = path.read_text(encoding='utf-8')
old = 'os.system("./configure --dest-cpu=" + DEST_CPU + " --dest-os=android --openssl-no-asm --cross-compiling")'
new = ('os.system("./configure --dest-cpu=" + DEST_CPU + " --dest-os=android "\n'
       '          "--with-intl=small-icu "\n'
       '          "--with-icu-locales=root,en,en_001,en_GB,en_US,es,es_419,es_ES,es_MX,fr,fr_CA,fr_FR,ru,ru_RU,zh,zh_Hans,zh_Hans_CN,zh_Hans_HK,zh_Hant,zh_Hant_HK,zh_Hant_TW "\n'
       '          "--openssl-no-asm --cross-compiling")')
if old not in s:
    raise SystemExit('ERROR: unexpected android_configure.py layout; refusing an unverified ICU patch')
path.write_text(s.replace(old, new, 1), encoding='utf-8')
PY
./android-configure "$NDK_ROOT" "$ANDROID_API" "$NDK_ARCH"
python3 "$WORKSPACE/Scripts/scripts/node/verify_icu_config.py" config.gypi
# Build ONLY the 'node' target (which depends on libnode.a). The top-level
# 'make' builds ALL gyp targets including the android-only openssl-cli tool
# which fails to link (undefined android_getCpuFeatures from NDK cpufeatures,
# not linked by gyp). configure only writes out/Makefile; the out/Release/ dir
# is created by gyp during the build, so pass BUILDTYPE.
# Full parallelism occasionally crashes clang (SIGSEGV, exit 139) on large
# v8 TUs (e.g. json-stringifier.cc) under peak memory. gyp's make is
# incremental, so retry with fewer jobs: the retry only compiles the
# remaining files with much lower memory pressure.
made=0
JOBS="$(nproc)"
for i in 1 2 3; do
  echo "=== make attempt $i (jobs=$JOBS) ==="
  if make -C out BUILDTYPE=Release node -j"$JOBS"; then
    made=1
    break
  fi
  echo "attempt $i failed (exit $?); killing leftover build procs and retrying"
  # A clang frontend crash (exit 139, e.g. json-stringifier.cc) under peak
  # memory can leave sibling build processes behind. Kill leftovers before
  # retrying and drop parallelism hard on each retry.
  pkill -9 -f 'out/Release' 2>/dev/null || true
  pkill -9 -f 'clang' 2>/dev/null || true
  pkill -9 -f 'cc1' 2>/dev/null || true
  pkill -9 -f 'icupkg' 2>/dev/null || true
  pkill -9 -f 'mksnapshot' 2>/dev/null || true
  pkill -9 -f 'genccode' 2>/dev/null || true
  pkill -9 -f 'node_js2c' 2>/dev/null || true
  sleep 5
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
LIBDIR="$WORKSPACE/staging/libnode/android/${DEST_CPU}"
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
python3 "$WORKSPACE/Scripts/scripts/node/merge_libnode.py" out/Release "$LIBDIR/libnode.a"

echo "== staging layout =="
find "$WORKSPACE/staging" -type f | sort
