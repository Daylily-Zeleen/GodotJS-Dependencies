#!/usr/bin/env python3
"""Enable v8's library TLS mode for the static libnode build.

`libnode.a` is only useful if it can be linked into a Godot GDExtension *shared
library*, which is how every consumer of this repo uses it. v8 decides how its
thread-local state is addressed from a compile-time macro, and for a static
(non-shared) build it picks the fastest model - "local-exec" on Linux and macOS:

    deps/v8/src/common/thread-local-storage.h

    #if defined(COMPONENT_BUILD) || defined(V8_TLS_USED_IN_LIBRARY)
    #define V8_TLS_LIBRARY_MODE 1
    ...
    #if V8_TLS_LIBRARY_MODE
    #define V8_TLS_MODEL "local-dynamic"
    #else
    #if defined(V8_TARGET_OS_WIN)        "initial-exec"
    #elif defined(V8_TARGET_OS_ANDROID)  "local-dynamic"
    #else                                "local-exec"     <-- Linux/macOS static
    #endif
    #endif

and the two variables that carry it are declared with that attribute:

    __attribute__((tls_model(V8_TLS_MODEL))) extern thread_local Isolate*
        g_current_isolate_ V8_CONSTINIT;             (execution/isolate.h)
    __attribute__((tls_model(V8_TLS_MODEL))) extern thread_local LocalHeap*
        g_current_local_heap_ V8_CONSTINIT;          (heap/local-heap.h)

"local-exec" emits R_X86_64_TPOFF32 (and the aarch64 equivalent), which a shared
object cannot use:

    relocation R_X86_64_TPOFF32 against hidden symbol
    `_ZN2v88internal18g_current_isolate_E' can not be used when making a shared object

node only defines V8_TLS_USED_IN_LIBRARY when it builds a *shared* library
(deps/v8/tools/v8_gypfiles/v8.gyp: ['node_shared=="true"', ...]), and neither
configure.py nor node.gyp expose it on its own. So the macro is added here, at
the target_defaults scope, which reaches every v8 target:

    patched gyp sets V8_TLS_USED_IN_LIBRARY
      -> V8_TLS_LIBRARY_MODE 1 -> V8_TLS_MODEL "local-dynamic"
      -> the access goes through a non-inlined getter and the object carries no
         local-exec TLS relocation at all

Verified: preprocessing the real header yields V8_TLS_MODEL "local-exec" today
and "local-dynamic" with this define; the reference libnode release (built from
the same node line) contains zero TLS relocations.

This is needed on every platform whose consumer is a shared object. Windows is
excluded only because its model is already "initial-exec" and the project builds
that leg with ClangCL; the define is harmless there but unverified, so it is not
applied.

Fail-closed: if the expected anchor is missing, moved, or already carries the
define, behave deterministically rather than silently producing an archive that
cannot be linked into a shared object.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# The node-wide defines block inside target_defaults. It reaches every target
# (including all of v8's), which is required because g_current_isolate_ and
# g_current_local_heap_ are compiled into several different v8 archives.
DEFINES_ANCHOR = "    'defines': [\n      '_GLIBCXX_USE_CXX11_ABI=1',"
DEFINES_MARKER = "      # libnode is linked into a shared library (Godot GDExtension), where\n"
DEFINES_PATCHED = (
    "    'defines': [\n"
    "      # libnode is linked into a shared library (Godot GDExtension), where\n"
    "      # v8's default thread-local model (\"local-exec\" on linux/macos) is not\n"
    "      # usable: it emits R_X86_64_TPOFF32/TPREL relocations against hidden\n"
    "      # symbols such as v8::internal::g_current_isolate_. Selecting v8's\n"
    "      # library mode routes the access through a getter instead.\n"
    "      'V8_TLS_USED_IN_LIBRARY',\n"
    "      '_GLIBCXX_USE_CXX11_ABI=1',"
)

# Platforms whose consumers link libnode into a shared object. Windows is left
# alone: its tls_model is already "initial-exec" and the define is unverified
# under the MSVC/ClangCL toolchain this project uses there.
SHARED_CONSUMER_PLATFORMS = {"linux", "macos", "ios", "android", "ohos"}


def fail(message: str) -> "NoReturn":
    print(f"tls patch error: {message}", file=sys.stderr)
    raise SystemExit(1)


def patch(node_root: Path, platform: str) -> None:
    path = node_root / "common.gypi"
    if not path.is_file():
        fail(f"common.gypi not found: {path}")
    if platform not in SHARED_CONSUMER_PLATFORMS:
        fail(f"{platform} is not a shared-object platform for this patch")

    text = path.read_text(encoding="utf-8")

    if DEFINES_MARKER in text:
        # Idempotent: the workflow restores a cached node tree, so a re-run sees
        # the already-patched file.
        print(f"{path}: V8_TLS_USED_IN_LIBRARY already present; nothing to do")
        return

    count = text.count(DEFINES_ANCHOR)
    if count != 1:
        fail(
            f"expected exactly one target_defaults defines anchor in common.gypi, "
            f"found {count}; refusing an unverified TLS patch"
        )

    path.write_text(text.replace(DEFINES_ANCHOR, DEFINES_PATCHED), encoding="utf-8")
    print(f"patched {path}: v8 TLS library mode enabled for {platform}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("node_root", type=Path, help="root of the fetched Node.js source tree")
    parser.add_argument("platform", choices=sorted(SHARED_CONSUMER_PLATFORMS))
    args = parser.parse_args()
    patch(args.node_root, args.platform)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
