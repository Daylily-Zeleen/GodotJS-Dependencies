#!/usr/bin/env python3
"""Assert that v8 was configured with its library TLS model, by preprocessing the
real header.

Why this exists
---------------
libnode.a is only useful if it links into a Godot GDExtension *shared library*.
v8's thread-local variables (v8::internal::g_current_isolate_,
g_current_local_heap_) are declared with `__attribute__((tls_model(V8_TLS_MODEL)))`,
and V8_TLS_MODEL is "local-exec" on Linux/macOS unless V8_TLS_USED_IN_LIBRARY is
defined - "local-exec" emits R_X86_64_TPOFF32 / TPREL relocations that a shared
object cannot use:

    relocation R_X86_64_TPOFF32 against hidden symbol
    `_ZN2v88internal18g_current_isolate_E' can not be used when making a shared object

script node/patch_tls.py adds that define to common.gypi. This check reads the
macro back out of the fetched tree *with the build's own compiler and target
defines*, so a regression (patch not applied, node rearranged the logic, the
define not reaching the target) is caught in seconds instead of after the
~90-minute v8 compile and a whole-archive link attempt.
"""
from __future__ import annotations

import argparse
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

HEADER = "deps/v8/src/common/thread-local-storage.h"
# The header is included relative to v8's own root, which is on the include path.
INCLUDE_AS = "src/common/thread-local-storage.h"

# V8_TLS_MODEL values that a shared object cannot use.
UNUSABLE_MODELS = {"local-exec"}


def fail(message: str) -> "NoReturn":
    raise SystemExit(f"v8 TLS configuration error: {message}")


def platform_defines(platform: str) -> list[str]:
    """The target macros v8's header switches on.

    v8config.h errors with "A target OS is defined but V8_HAVE_TARGET_OS is
    unset", so the marker goes with every target-OS define (gyp passes it on
    every v8 compile too).
    """
    per_platform = {
        "linux": "-DV8_TARGET_OS_LINUX",
        "macos": "-DV8_TARGET_OS_MACOS",
        "ios": "-DV8_TARGET_OS_MACOS",
        "android": "-DV8_TARGET_OS_ANDROID",
        "ohos": "-DV8_TARGET_OS_LINUX",
    }
    define = per_platform.get(platform)
    if define is None:
        fail(f"no v8 target-OS define known for platform {platform!r}")
    return ["-DV8_HAVE_TARGET_OS", define]


def tls_define_present(node_root: Path) -> bool:
    """Is V8_TLS_USED_IN_LIBRARY in common.gypi's target_defaults defines?

    This is what scripts/node/patch_tls.py adds, and what gyp turns into the -D
    flag every v8 target is compiled with. Read it rather than assuming it, so
    the check below reflects the tree's actual configuration.
    """
    gypi = node_root / "common.gypi"
    if not gypi.is_file():
        fail(f"common.gypi not found: {gypi}")
    text = gypi.read_text(encoding="utf-8", errors="replace")
    # The flag must be a real define entry, not a mention in a comment.
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if stripped.startswith("'V8_TLS_USED_IN_LIBRARY'"):
            return True
    return False


def read_tls_model(node_root: Path, platform: str, extra_defines: list[str]) -> tuple[str, str]:
    """Preprocess the header and return (V8_TLS_MODEL, V8_TLS_LIBRARY_MODE)."""
    cc = os.environ.get("CXX") or os.environ.get("CC") or "c++"
    v8 = node_root / "deps" / "v8"
    source = f'#include "{INCLUDE_AS}"\nV8_TLS_MODEL\nV8_TLS_LIBRARY_MODE\n'
    cmd = shlex.split(cc) + [
        # v8config.h hard-errors without C++20, so the standard must be stated
        # explicitly: a compiler whose default is older (gcc-12, which the linux
        # node leg uses) fails the probe otherwise.
        "-std=c++20",
        "-E", "-P", "-I", str(v8), "-I", str(v8 / "include"),
        *extra_defines, *platform_defines(platform), "-x", "c++", "-",
    ]
    result = subprocess.run(cmd, input=source, capture_output=True, text=True)
    if result.returncode != 0:
        fail(
            f"could not preprocess {HEADER} with {cc} (exit {result.returncode}); "
            f"the TLS model cannot be verified:\n{result.stderr.strip()}"
        )
    # The two probe lines are emitted last, in the order written above.
    tail = [ln.strip() for ln in result.stdout.splitlines() if ln.strip()]
    if len(tail) < 2:
        fail(f"unexpected preprocessor output; could not read V8_TLS_MODEL:\n{result.stdout[-2000:]}")
    model, mode = tail[-2], tail[-1]
    if not re.fullmatch(r'"[a-z-]+"', model):
        fail(f"could not read V8_TLS_MODEL from the preprocessor output (got {model!r})")
    return model.strip('"'), mode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("node_root", type=Path, help="root of the fetched Node.js source tree")
    parser.add_argument("platform", help="target platform (linux/macos/ios/android/ohos)")
    args = parser.parse_args()

    if not (args.node_root / HEADER).is_file():
        fail(f"{args.node_root / HEADER} not found; is this a node source tree?")

    # Mirror the build: the define only matters if it actually reaches the
    # compiler, so read it out of common.gypi and pass it to the preprocessor.
    patched = tls_define_present(args.node_root)
    define = ["-DV8_TLS_USED_IN_LIBRARY"] if patched else []
    model, mode = read_tls_model(args.node_root, args.platform, define)
    if model in UNUSABLE_MODELS:
        fail(
            f'V8_TLS_MODEL is "{model}" (V8_TLS_LIBRARY_MODE={mode}) for '
            f"{args.platform}. v8's thread_local variables would emit relocations "
            "that a shared object cannot use, so the archive would fail to link "
            "into a Godot GDExtension. scripts/node/patch_tls.py should have "
            "defined V8_TLS_USED_IN_LIBRARY in common.gypi."
        )
    print(f'v8 TLS configuration passed: V8_TLS_MODEL "{model}" (library mode {mode})')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
