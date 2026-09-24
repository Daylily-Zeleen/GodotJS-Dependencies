#!/usr/bin/env python3
"""Disable LTCG for the Windows libnode build by patching Node's vcbuild.bat.

vcbuild.bat's `release` argument implies `ltcg=1`, which forwards
`--with-ltcg` to configure. That is right for building node.exe, but wrong for a
static library meant to be embedded by an MSVC consumer: with LTCG the objects
inside libnode.lib are LLVM bitcode rather than COFF, and a plain MSVC link
rejects them as corrupt:

    Storage.obj : fatal error LNK1107: invalid or corrupt file: cannot read at 0x1A7BF8

(lld-link consumes bitcode, so the failure only appears in the downstream
embedder, never in node's own build.)

Fail-closed: the exact release-argument line must be present exactly once, and
the patched form must not already be applied.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

UNPATCHED = 'if /i "%1"=="release"       set config=Release&set ltcg=1&set cctest=1&goto arg-ok'
PATCHED = 'if /i "%1"=="release"       set config=Release&set cctest=1&goto arg-ok'


def fail(message: str) -> "NoReturn":
    print(f"ltcg patch error: {message}", file=sys.stderr)
    raise SystemExit(1)


def patch(node_root: Path) -> None:
    path = node_root / "vcbuild.bat"
    if not path.is_file():
        fail(f"vcbuild.bat not found: {path}")
    data = path.read_bytes()
    # vcbuild.bat is CRLF; match on normalized text so line endings cannot make
    # the anchor silently miss.
    text = data.decode("utf-8", "replace").replace("\r\n", "\n")

    if PATCHED in text and UNPATCHED not in text:
        print(f"{path}: LTCG already disabled; nothing to do")
        return

    count = text.count(UNPATCHED)
    if count != 1:
        fail(
            f"expected exactly one occurrence of the release-argument line in "
            f"vcbuild.bat, found {count}; refusing an unverified LTCG patch"
        )

    text = text.replace(UNPATCHED, PATCHED)
    if UNPATCHED in text:
        fail("the release-argument line still enables ltcg after the patch")

    # Restore the original line endings so cmd.exe sees the file it expects.
    path.write_bytes(text.replace("\n", "\r\n").encode("utf-8"))
    print(f"patched {path}: LTCG disabled for the windows libnode build")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("node_root", type=Path, help="root of the fetched Node.js source tree")
    args = parser.parse_args()
    patch(args.node_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
