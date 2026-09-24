#!/usr/bin/env python3
"""Drop debug info from the libnode build by patching Node's common.gypi.

For mac/ios builds the gyp make generator runs XcodeSettings, which appends
-gdwarf-2 for every object (GCC_GENERATE_DEBUGGING_SYMBOLS defaults to YES and
node never turns it off for a non-debug build). Node's Release build therefore
ships a static archive with full DWARF for V8, ICU and OpenSSL: our staged
macOS libnode.a came out at ~10 GB with 2.3 MB members, against 172 MB / 47 KB
in the official moluopro/libnode release (BUILD-METADATA.json: DEBUG_INFO
"stripped").

Setting GCC_GENERATE_DEBUGGING_SYMBOLS to NO is the switch XcodeSettings reads,
and it is honoured by the gyp make generator too (make.py constructs
XcodeSettings for the mac/ios flavors and merges GetCflags into cflags).

Fail-closed: if the mac xcode_settings block is missing (upstream layout
changed), exit with an error instead of shipping a multi-gigabyte archive.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Unique inside the mac/ios xcode_settings block in common.gypi.
ANCHOR = "'ALWAYS_SEARCH_USER_PATHS': 'NO',"
PATCHED = "'ALWAYS_SEARCH_USER_PATHS': 'NO',\n        'GCC_GENERATE_DEBUGGING_SYMBOLS': 'NO',"

XCODE_PLATFORMS = {"macos", "ios"}


def fail(message: str) -> "NoReturn":
    print(f"debug info patch error: {message}", file=sys.stderr)
    raise SystemExit(1)


def patch(node_root: Path, platform: str) -> None:
    if platform not in XCODE_PLATFORMS:
        fail(f"{platform} does not use xcode_settings; refusing to run")
    path = node_root / "common.gypi"
    if not path.is_file():
        fail(f"common.gypi not found: {path}")
    text = path.read_text(encoding="utf-8")

    if PATCHED in text:
        print(f"{path}: debug info already disabled for {platform}; nothing to do")
        return

    count = text.count(ANCHOR)
    if count != 1:
        fail(f"expected exactly one occurrence of {ANCHOR!r} in common.gypi, found {count}; refusing an unverified patch")

    path.write_text(text.replace(ANCHOR, PATCHED), encoding="utf-8")
    print(f"patched {path}: GCC_GENERATE_DEBUGGING_SYMBOLS=NO for {platform}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("node_root", type=Path, help="root of the fetched Node.js source tree")
    parser.add_argument("platform", choices=sorted(XCODE_PLATFORMS))
    args = parser.parse_args()
    patch(args.node_root, args.platform)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
