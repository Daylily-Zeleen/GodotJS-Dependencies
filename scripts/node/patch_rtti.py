#!/usr/bin/env python3
"""Enable RTTI for the libnode build by patching Node's common.gypi.

Node's common.gypi turns C++ RTTI off, which suppresses the `typeinfo for ...`
symbols that downstream embedders need when they subclass
v8::ValueSerializer::Delegate and v8::ValueDeserializer::Delegate. This script
rewrites the RTTI-off flags for the target platform before ./configure reads
the file:

- linux / android / ohos: POSIX cflags_cc '-fno-rtti' -> '-frtti'
- windows: MSVC 'RuntimeTypeInfo': 'false' -> 'true'
- macos / ios: xcode_settings 'GCC_ENABLE_CPP_RTTI': 'NO' -> 'YES'

The macos/ios case is NOT a no-op. gyp's make generator builds its compile
lines from gyp.xcode_emulation.XcodeSettings, and that code maps the setting
straight onto the flag:

    if self._Test("GCC_ENABLE_CPP_RTTI", "NO", default="YES"):
        cflags_cc.append("-fno-rtti")

So the darwin targets really do get -fno-rtti, and the earlier "the make
generator ignores xcode_settings, so RTTI stays on" comment was simply wrong:
it left the v8 objects compiled without RTTI, which emitted
'__ZTVN2v815ValueSerializer8DelegateE' (the vtable) but no
'__ZTIN...' typeinfo, and the embedder's subclass vtable then failed to link
with "typeinfo for v8::ValueSerializer::Delegate, referenced from typeinfo for
jsb::Serialization::VariantSerializerDelegate". Verified in the macOS CI logs:
the api.cc compile line carried -fno-rtti and no -frtti at all.

Fail-closed: if the platform's expected RTTI-off pattern is missing (upstream
layout changed), exit with an error instead of silently building without RTTI.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

CFLAGS_OFF = "'-fno-rtti',"
CFLAGS_ON = "'-frtti',"
MSVC_OFF = "'RuntimeTypeInfo': 'false',"
MSVC_ON = "'RuntimeTypeInfo': 'true',"
XCODE_OFF = "'GCC_ENABLE_CPP_RTTI': 'NO',"
XCODE_ON = "'GCC_ENABLE_CPP_RTTI': 'YES',"

POSIX_RTTI_OFF = {"linux", "android", "ohos"}
MSVC_RTTI_OFF = {"windows"}
XCODE_RTTI_OFF = {"macos", "ios"}

# Matches the whole 'GCC_ENABLE_CPP_RTTI': 'NO', line including its trailing
# comment, so the "# -fno-rtti" explanation can be rewritten to match.
XCODE_RTTI_RE = re.compile(
    r"'GCC_ENABLE_CPP_RTTI'\s*:\s*'NO'\s*,(?P<comment>\s*#[^\n]*)?")


def _xcode_enable(match: re.Match[str]) -> str:
    """Rewrite one 'GCC_ENABLE_CPP_RTTI': 'NO' setting to 'YES'.

    The trailing comment is rewritten too, so it does not keep claiming
    -fno-rtti next to a setting that now means -frtti.
    """
    comment = match.group("comment")
    if comment:
        comment = re.sub(r"-fno-rtti", "-frtti", comment)
        return f"{XCODE_ON}{comment}"
    return XCODE_ON


def _replace_flag(text: str, path: Path, platform: str, off: str, on: str) -> None:
    """Rewrite a single RTTI-off spelling, tolerating an already-patched file."""
    if off in text:
        text = text.replace(off, on)
        if off in text:
            fail("an RTTI-off flag remains after the patch")
    elif on not in text:
        fail(
            f"{platform}: expected {off} or {on} in common.gypi but neither is "
            "present; refusing an unverified RTTI patch"
        )
    path.write_text(text, encoding="utf-8")
    print(f"patched {path}: RTTI enabled for {platform}")


def fail(message: str) -> "NoReturn":
    print(f"rtti patch error: {message}", file=sys.stderr)
    raise SystemExit(1)


def patch(node_root: Path, platform: str) -> None:
    path = node_root / "common.gypi"
    if not path.is_file():
        fail(f"common.gypi not found: {path}")
    text = path.read_text(encoding="utf-8")

    if platform in POSIX_RTTI_OFF:
        _replace_flag(text, path, platform, CFLAGS_OFF, CFLAGS_ON)
        return
    if platform in MSVC_RTTI_OFF:
        _replace_flag(text, path, platform, MSVC_OFF, MSVC_ON)
        return

    # macos / ios: the darwin targets carry their RTTI setting as an
    # xcode_setting rather than a raw flag, and gyp's make generator turns that
    # setting into -fno-rtti (see the module docstring).
    new_text, count = XCODE_RTTI_RE.subn(_xcode_enable, text)
    if count == 0:
        # Tolerate an already-patched tree, like the POSIX/MSVC branches do.
        if XCODE_ON in text:
            print(f"{path}: RTTI already enabled for {platform}")
            return
        fail(
            f"{platform}: expected {XCODE_OFF!r} or {XCODE_ON!r} in common.gypi "
            "but neither is present; refusing an unverified RTTI patch"
        )
    path.write_text(new_text, encoding="utf-8")
    print(f"patched {path}: RTTI enabled for {platform} ({count} setting(s))")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("node_root", type=Path, help="root of the fetched Node.js source tree")
    parser.add_argument("platform", choices=sorted(POSIX_RTTI_OFF | MSVC_RTTI_OFF | XCODE_RTTI_OFF))
    args = parser.parse_args()
    patch(args.node_root, args.platform)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
