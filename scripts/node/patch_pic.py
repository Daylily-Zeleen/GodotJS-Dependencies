#!/usr/bin/env python3
"""Force -fPIC for the POSIX libnode build by patching Node's common.gypi.

Node only adds -fPIC when it is building a *shared* library
(`node_shared=="true"`) or for Android. The static libnode.a that embedders
link into a Godot GDExtension shared library is therefore built from
non-PIC objects, and the downstream link dies with

    relocation R_X86_64_TPOFF32 against symbol `_ZN5lexer10last_errorE'
    can not be used when making a shared object; recompile with -fPIC

Adding -fPIC to the POSIX cflags/cflags_cc keeps the archive linkable into a
.so. The flags are added to both lists: `cflags` reaches C and C++ objects,
`cflags_cc` is the C++-only list that node's own targets read.

Fail-closed: if the expected POSIX flag block is missing or ambiguous
(upstream layout changed), exit with an error instead of silently producing a
non-PIC archive that only fails in a downstream consumer.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Node's POSIX (linux/android/openharmony/...) flag block. Both anchors must be
# unique in common.gypi - they are asserted to occur exactly once.
CFLAGS_ANCHOR = "'cflags': [ '-Wall', '-Wextra', '-Wno-unused-parameter', ],"
CFLAGS_PATCHED = "'cflags': [ '-Wall', '-Wextra', '-Wno-unused-parameter', '-fPIC', ],"
CFLAGS_CC_ANCHOR = "'-fno-strict-aliasing',"
CFLAGS_CC_PATCHED = "'-fno-strict-aliasing',\n          '-fPIC',"

POSIX_PIC = {"linux", "android", "ohos"}


def fail(message: str) -> "NoReturn":
    print(f"pic patch error: {message}", file=sys.stderr)
    raise SystemExit(1)


def replace_once(text: str, anchor: str, replacement: str, what: str) -> str:
    count = text.count(anchor)
    if count != 1:
        fail(f"expected exactly one occurrence of the {what} anchor in common.gypi, found {count}; refusing an unverified PIC patch")
    return text.replace(anchor, replacement)


def patch(node_root: Path, platform: str) -> None:
    path = node_root / "common.gypi"
    if not path.is_file():
        fail(f"common.gypi not found: {path}")
    text = path.read_text(encoding="utf-8")

    if platform not in POSIX_PIC:
        fail(f"{platform} does not need a PIC patch (Apple platforms are PIC by default, and Android already sets -fPIC)")

    if CFLAGS_PATCHED in text and CFLAGS_CC_PATCHED in text:
        print(f"{path}: -fPIC already present for {platform}; nothing to do")
        return

    text = replace_once(text, CFLAGS_ANCHOR, CFLAGS_PATCHED, "POSIX cflags")
    text = replace_once(text, CFLAGS_CC_ANCHOR, CFLAGS_CC_PATCHED, "POSIX cflags_cc")

    path.write_text(text, encoding="utf-8")
    print(f"patched {path}: -fPIC enabled for {platform}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("node_root", type=Path, help="root of the fetched Node.js source tree")
    parser.add_argument("platform", choices=sorted(POSIX_PIC))
    args = parser.parse_args()
    patch(args.node_root, args.platform)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
