#!/usr/bin/env python3
"""Enable RTTI for the libnode build by patching Node's common.gypi.

Node's common.gypi turns C++ RTTI off, which suppresses the `typeinfo for ...`
symbols that downstream embedders need when they subclass
v8::ValueSerializer::Delegate and v8::ValueDeserializer::Delegate. This script
rewrites the RTTI-off flags for the target platform before ./configure reads
the file:

- linux / android / ohos: POSIX cflags_cc '-fno-rtti' -> '-frtti'
- windows: MSVC 'RuntimeTypeInfo': 'false' -> 'true'
- macos / ios: the gyp make generator ignores xcode_settings, so RTTI stays on
  even though 'GCC_ENABLE_CPP_RTTI': 'NO' is set; the layout is only verified.

Fail-closed: if the platform's expected RTTI-off pattern is missing (upstream
layout changed), exit with an error instead of silently building without RTTI.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

CFLAGS_OFF = "'-fno-rtti',"
CFLAGS_ON = "'-frtti',"
MSVC_OFF = "'RuntimeTypeInfo': 'false',"
MSVC_ON = "'RuntimeTypeInfo': 'true',"
MAC_RTTI_MARKER = "'GCC_ENABLE_CPP_RTTI'"

POSIX_RTTI_OFF = {"linux", "android", "ohos"}
MSVC_RTTI_OFF = {"windows"}
XCODE_RTTI_OFF = {"macos", "ios"}


def fail(message: str) -> "NoReturn":
    print(f"rtti patch error: {message}", file=sys.stderr)
    raise SystemExit(1)


def patch(node_root: Path, platform: str) -> None:
    path = node_root / "common.gypi"
    if not path.is_file():
        fail(f"common.gypi not found: {path}")
    text = path.read_text(encoding="utf-8")

    if platform in POSIX_RTTI_OFF:
        if CFLAGS_OFF in text:
            text = text.replace(CFLAGS_OFF, CFLAGS_ON)
            if CFLAGS_OFF in text:
                fail("an RTTI-off flag remains after the patch")
        elif CFLAGS_ON not in text:
            fail(f"{platform}: expected {CFLAGS_OFF} or {CFLAGS_ON} in common.gypi but neither is present; refusing an unverified RTTI patch")
    elif platform in MSVC_RTTI_OFF:
        if MSVC_OFF in text:
            text = text.replace(MSVC_OFF, MSVC_ON)
            if MSVC_OFF in text:
                fail("an RTTI-off flag remains after the patch")
        elif MSVC_ON not in text:
            fail(f"{platform}: expected {MSVC_OFF} or {MSVC_ON} in common.gypi but neither is present; refusing an unverified RTTI patch")
    else:
        if CFLAGS_OFF in text:
            text = text.replace(CFLAGS_OFF, CFLAGS_ON)
            if CFLAGS_OFF in text:
                fail("an RTTI-off flag remains after the patch")
        if MAC_RTTI_MARKER not in text:
            fail(f"{platform}: expected {MAC_RTTI_MARKER} in common.gypi but it is missing; refusing an unverified RTTI patch")

    path.write_text(text, encoding="utf-8")
    print(f"patched {path}: RTTI enabled for {platform}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("node_root", type=Path, help="root of the fetched Node.js source tree")
    parser.add_argument("platform", choices=sorted(POSIX_RTTI_OFF | MSVC_RTTI_OFF | XCODE_RTTI_OFF))
    args = parser.parse_args()
    patch(args.node_root, args.platform)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
