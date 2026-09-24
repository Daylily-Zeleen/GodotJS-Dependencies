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


# A self-contained libnode archive must carry the symbols of every library node
# links, not just node's own objects. Before this expectation existed the
# Windows staging step published node's single libnode.lib (192 members /
# ~29k symbols) and still passed validation, while the embedder's link then
# failed with 163 unresolved externals. Each marker below is defined in a
# DIFFERENT archive that node links, so a merged archive must hold all of them.
# All were verified present in moluopro's known-good linux/macOS releases.
NODE_COVERAGE_MARKERS = (
    "node::CreateEnvironment",                          # node's own objects
    "uv_loop_init",                                     # libuv
    "nghttp2_submit_request",                           # nghttp2
    "u_strlen",                                         # ICU
    "v8::ValueSerializer::Delegate::WriteHostObject",    # v8 api objects
    "cppgc::internal::Sweeper",                         # cppgc (v8 heap)
    "EVP_EncryptInit",                                  # OpenSSL
    "deflate",                                          # zlib
    "ZSTD_compress",                                    # zstd
)

# Intactness: a correct merge is a faithful concatenation. A truncated archive
# reports fewer members than the inputs held (~3630 in known-good releases),
# and an archive built by name-keyed extraction loses same-named members: gyp
# archives store only object basenames, so v8's src/heap/sweeper.o and
# src/heap/cppgc/sweeper.o are both stored as "sweeper.o" and the name lookup
# returned the first for both. That dropped the cppgc object and surfaced as
# 131 undefined symbols in the macOS embedder.
NODE_MIN_MEMBERS = 3000


def _raw_members(archive: Path) -> tuple[list[tuple[str, int, int]], bool]:
    """Parse an archive structurally, returning (name, offset, length), thin."""
    with archive.open("rb") as fh:
        magic = fh.read(8)
        if magic not in (b"!<arch>\n", b"!<thin>\n"):
            fail(f"{archive} is not an ar archive (magic {magic!r})")
        thin = magic == b"!<thin>\n"
        offset = 8
        strtab = b""
        members: list[tuple[str, int, int]] = []
        while True:
            fh.seek(offset)
            header = fh.read(60)
            if len(header) < 60:
                break
            if header[58:60] != b"`\n":
                fail(f"{archive}: malformed archive header at offset {offset}")
            raw = header[0:16].decode("ascii", "replace").strip()
            try:
                size = int(header[48:58].decode("ascii").strip() or "0")
            except ValueError:
                fail(f"{archive}: malformed size field at offset {offset}")
            at = offset + 60
            if raw == "//":
                strtab = fh.read(size)
                name, skip = None, 0
            elif raw in ("/", "/SYM64/", "/<ECSYMBOLS>/"):
                name, skip = None, 0
            elif raw.startswith("#1/"):
                nlen = int(raw[3:])
                name = fh.read(min(size, 256))[:nlen].decode("utf-8", "replace").rstrip("\0")
                skip = nlen
            elif raw.startswith("/") and raw[1:].isdigit():
                # GNU ends entries with "/\n", MSVC with NUL and a trailing
                # "\n" for the whole table: take whichever comes first.
                start = int(raw[1:])
                ends = [
                    end for end in (
                        strtab.find(b"/\n", start),
                        strtab.find(b"\n", start),
                        strtab.find(b"\0", start),
                    ) if end != -1
                ]
                end = min(ends) if ends else len(strtab)
                name = strtab[start:end].decode("utf-8", "replace").rstrip("\0")
                skip = 0
            else:
                name = raw[:-1] if raw.endswith("/") else raw
                skip = 0
            if name is not None:
                members.append((name, at + skip, size - skip))
            offset = at + size
            if offset % 2:
                offset += 1
    return members, thin


def validate_node_archive_shape(library: Path, platform: str) -> None:
    """The archive must look like a merged, intact collection."""
    members, _thin = _raw_members(library)
    if not members:
        fail(f"{library} contains no members")
    if len(members) < NODE_MIN_MEMBERS:
        fail(
            f"{library} holds only {len(members)} members; a self-contained "
            f"libnode needs at least {NODE_MIN_MEMBERS} (node's own object "
            "archive alone is ~190). The staging step published a single "
            "un-merged archive."
        )
    print(f"node archive shape passed: {library} ({len(members)} members)")


def validate_node_coverage(library: Path, platform: str, arch: str) -> None:
    """Prove the libraries node links are actually inside this archive."""
    if platform == "windows":
        # dumpbin only exists in a VS developer prompt on hosted runners, and it
        # cannot finish on a multi-GB archive; coverage there is asserted by the
        # member-count check plus the Delegate markers.
        return
    output = node_nm_defined(library)
    missing = [m for m in NODE_COVERAGE_MARKERS if m not in output]
    if missing:
        fail(
            f"{library} is missing symbols from libraries node links: "
            f"{', '.join(missing)}. The archive is not self-contained."
        )
    print(f"node coverage validation passed: {library} holds all {len(NODE_COVERAGE_MARKERS)} cross-library markers")


def validate_node_linkable(library: Path, platform: str, arch: str) -> None:
    """Link the whole archive into a shared object.

    This is the regression test for all three packaging defects at once: a
    non-PIC archive fails with a relocation error, one that wrongly includes
    host-only build tools fails with multiple definitions (verified: the
    published linux archive dies on `multiple definition of
    icu_78::VTimeZone::VTimeZone()` because libicutools was merged in), and a
    missing member shows up as long as --no-undefined is added. Known-good
    moluopro archives pass the plain form on both counts, so no extra
    --no-undefined (which would demand libc++ runtime symbols) is needed.
    """
    if platform != "linux":
        print(f"node link validation skipped: only linux runs the shared-object probe")
        return
    cc = os.environ.get("CC", "cc")
    if shutil.which(cc.split()[0]) is None:
        fail(f"node link validation needs {cc} but it is not on PATH")
    fd, probe = tempfile.mkstemp(prefix="node_link_", suffix=".so")
    os.close(fd)
    try:
        cmd = [
            cc, "-shared", "-fPIC",
            "-Wl,--whole-archive", str(library), "-Wl,--no-whole-archive",
            "-pthread", "-ldl", "-lm", "-o", probe,
        ]
        result = run(cmd)
        if result.returncode != 0:
            detail = result.stderr.strip()
            kind = "non-PIC objects (rebuild with -fPIC)" if "relocation" in detail else \
                   "duplicate or missing members (wrong merge set or lost members)"
            fail(f"{library} failed to link as a shared object - {kind}:\n{detail[-4000:]}")
    finally:
        try:
            os.unlink(probe)
        except OSError:
            pass
    print(f"node link validation passed: {library} links whole-archive into a shared object")


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


def node_nm_defined(library: Path) -> str:
    """`nm -C --defined-only` output for `library`, tolerating Apple nm.

    Apple's nm rejects `--defined-only`; the portable equivalent is `-U`.
    """
    nm = shutil.which("nm")
    if nm is None:
        fail("nm not found; cannot verify libnode symbols")
    result = run([nm, "-C", "--defined-only", str(library)])
    if result.returncode != 0:
        result = run([nm, "-C", "-U", str(library)])
    if result.returncode != 0:
        fail(f"nm failed on {library}: {result.stderr.strip()}")
    return result.stdout


def validate_node_unix(root: Path, platform: str, arch: str) -> None:
    library = root / node_dir(platform, arch) / "libnode.a"
    if not library.is_file():
        fail(f"libnode.a not found: {library}")
    validate_node_archive_shape(library, platform)
    validate_node_coverage(library, platform, arch)
    validate_node_linkable(library, platform, arch)
    output = node_nm_defined(library)
    missing = [symbol for symbol in NODE_DELEGATE_MARKERS if symbol not in output]
    if missing:
        fail(f"{library} is missing Delegate RTTI symbol(s): {', '.join(missing)}")
    print(f"node RTTI validation passed: {library} contains the Delegate RTTI symbols")


def contains_all_markers(library: Path, markers: tuple[str, ...]) -> list[str]:
    """Stream the archive and report which markers are absent.

    A COFF archive stores decorated symbol names verbatim, so scanning the raw
    bytes is equivalent to a symbol dump - and unlike `dumpbin /symbols` it
    finishes on a multi-GB archive. The scan is streamed because the merged
    libnode.lib is ~3 GB and must not be read into memory.
    """
    encoded = [(m, m.encode("utf-8")) for m in markers]
    found = {m: False for m in markers}
    overlap = max(len(b) for _m, b in encoded) - 1
    tail = b""
    with library.open("rb") as fh:
        while True:
            chunk = fh.read(1 << 22)
            if not chunk:
                break
            window = tail + chunk
            for marker, needle in encoded:
                if not found[marker] and needle in window:
                    found[marker] = True
            tail = window[-overlap:] if overlap else b""
    return [m for m, ok in found.items() if not ok]


def validate_node_windows(root: Path) -> None:
    library = root / node_dir("windows", "x86_64") / "libnode.lib"
    if not library.is_file():
        fail(f"libnode.lib not found: {library}")
    validate_node_archive_shape(library, "windows")
    missing = contains_all_markers(library, NODE_WINDOWS_MARKERS)
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
