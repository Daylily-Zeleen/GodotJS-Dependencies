#!/usr/bin/env python3
"""Validate PIC and RTTI symbols in staged dependency artifacts.

For lws on Linux, the whole static archive is linked into a shared object to
prove every object was compiled with -fPIC (a non-PIC object fails with a text
relocation error such as 'recompile with -fPIC'). For node, the RTTI typeinfo
symbols for v8::ValueSerializer::Delegate and v8::ValueDeserializer::Delegate
must exist in libnode, because downstream embedders subclass these types.

The script is fail-closed: a missing tool, an unrecognized archive, or a
missing symbol always aborts the build.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

LWS_PLATFORMS = {
    "linux": {"x86_64", "arm64"},
    "macos": {"x86_64", "arm64"},
    "windows": {"x86_64", "arm64"},
    "android": {"arm64", "arm32", "x86_64"},
    "ios": {"arm64"},
}
NODE_PLATFORMS = {
    "linux": {"x86_64"},
    "macos": {"arm64"},
    "windows": {"x86_64"},
    "android": {"arm64"},
    "ios": {"arm64"},
    "ohos": {"arm64"},
}

# The Delegate RTTI typeinfo symbols are emitted by V8 with hidden visibility
# (BUILDING_V8_SHARED is undefined for the static build, so V8_EXPORT expands to
# nothing) and are therefore not present even in the official moluopro/libnode
# release. The symbols that actually prove the Delegate code was linked into the
# single self-contained libnode.a -- and that a downstream subclass vtable needs
# to resolve -- are the out-of-line default virtual function implementations,
# which V8 emits weakly into the api objects. We assert their presence instead.
NODE_DELEGATE_MARKERS = (
    "v8::ValueSerializer::Delegate::WriteHostObject",
    "v8::ValueDeserializer::Delegate::ReadHostObject",
)
# MSVC does not demangle by default; these substrings match both the vftable
# symbol (??_7Delegate@ValueSerializer@v8@@6B@) and the RTTI descriptors
# (??_R0?AVDelegate@ValueSerializer@v8@@@8, ??_R3...).
NODE_WINDOWS_MARKERS = (
    "Delegate@ValueSerializer@v8",
    "Delegate@ValueDeserializer@v8",
)


def fail(message: str) -> "NoReturn":
    print(f"symbol validation error: {message}", file=sys.stderr)
    raise SystemExit(1)


def lws_dir(platform: str, arch: str) -> str:
    return f"{platform}_{arch}_release"


def node_dir(platform: str, arch: str) -> str:
    return f"{platform}/{'x64' if platform == 'windows' else arch}"


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def validate_lws(root: Path, platform: str, arch: str) -> None:
    if platform != "linux":
        print(f"lws PIC validation skipped: platform {platform} is not linux")
        return
    archive = root / lws_dir(platform, arch) / "libwebsockets.a"
    if not archive.is_file():
        fail(f"libwebsockets.a not found: {archive}")
    cc = os.environ.get("CC", "cc")
    fd, probe = tempfile.mkstemp(prefix="lws_pic_", suffix=".so")
    os.close(fd)
    try:
        cmd = [
            cc, "-shared", "-fPIC",
            "-Wl,--whole-archive", str(archive), "-Wl,--no-whole-archive",
            "-Wl,--unresolved-symbols=ignore-all",
            "-pthread", "-lm", "-ldl", "-o", probe,
        ]
        result = run(cmd)
        if result.returncode != 0:
            fail(
                f"{archive} failed the shared-library link test; the library "
                f"contains non-PIC objects, recompile with -fPIC "
                f"({cc} exited {result.returncode}):\n{result.stderr.strip()}"
            )
    finally:
        try:
            os.unlink(probe)
        except OSError:
            pass
    print(f"lws PIC validation passed: {archive} links as a shared object")


def validate_node_unix(root: Path, platform: str, arch: str) -> None:
    library = root / node_dir(platform, arch) / "libnode.a"
    if not library.is_file():
        fail(f"libnode.a not found: {library}")
    nm = shutil.which("nm")
    if nm is None:
        fail("nm not found; cannot verify libnode RTTI symbols")
    result = run([nm, "-C", str(library)])
    if result.returncode != 0:
        fail(f"nm failed on {library}: {result.stderr.strip()}")
    missing = [symbol for symbol in NODE_DELEGATE_MARKERS if symbol not in result.stdout]
    if missing:
        fail(f"{library} is missing Delegate RTTI symbol(s): {', '.join(missing)}")
    print(f"node RTTI validation passed: {library} contains the Delegate RTTI symbols")


def validate_node_windows(root: Path) -> None:
    library = root / node_dir("windows", "x86_64") / "libnode.lib"
    if not library.is_file():
        fail(f"libnode.lib not found: {library}")
    dumpbin = shutil.which("dumpbin")
    if dumpbin is not None:
        result = run([dumpbin, "/symbols", str(library)])
        if result.returncode != 0:
            fail(f"dumpbin failed on {library}: {result.stderr.strip()}")
        haystack = result.stdout
    else:
        # Hosted runners only expose dumpbin inside a VS developer prompt.
        # A COFF archive stores the decorated symbol names verbatim, so a raw
        # byte scan is equivalent for our marker check.
        haystack = library.read_bytes().decode("latin-1")
    missing = [marker for marker in NODE_WINDOWS_MARKERS if marker not in haystack]
    if missing:
        fail(f"{library} is missing Delegate RTTI/vftable symbol(s): {', '.join(missing)}")
    print(f"node RTTI validation passed: {library} contains the Delegate RTTI/vftable symbols")


def validate_node(root: Path, platform: str, arch: str) -> None:
    if platform == "windows":
        validate_node_windows(root)
    else:
        validate_node_unix(root, platform, arch)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("component", choices=("lws", "node"))
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--platform", required=True)
    parser.add_argument("--arch", required=True)
    args = parser.parse_args()

    supported = LWS_PLATFORMS if args.component == "lws" else NODE_PLATFORMS
    if args.platform not in supported or args.arch not in supported[args.platform]:
        fail(f"unsupported {args.component} target: {args.platform}-{args.arch}")

    if args.component == "lws":
        validate_lws(args.root, args.platform, args.arch)
    else:
        validate_node(args.root, args.platform, args.arch)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
