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

# The Delegate RTTI typeinfo symbols are what the embedder's subclass vtable
# needs: jsb::Serialization::VariantSerializerDelegate derives from
# v8::ValueSerializer::Delegate, so its own typeinfo refers to the base class's
# typeinfo and the link fails with
#   "typeinfo for v8::ValueSerializer::Delegate", referenced from
#       typeinfo for jsb::Serialization::VariantSerializerDelegate
# when node was built with RTTI off. Compiling without RTTI still emits the
# vtable ('_ZTV...') but drops the typeinfo ('_ZTI...') and type string
# ('_ZTS...'), so asserting the out-of-line virtual functions alone passes an
# archive that cannot be linked -- which is precisely how the macOS leg shipped
# broken while this script reported success. Assert the typeinfo itself.
#
# Names are the Itanium manglings with the platform's symbol prefix: Mach-O
# symbols carry one extra leading underscore. "ValueSerializer" is 15 chars and
# "ValueDeserializer" 17, which is encoded in the mangled length field.
NODE_DELEGATE_TYPEINFO_MARKERS = {
    "darwin": (
        "__ZTIN2v815ValueSerializer8DelegateE",
        "__ZTIN2v817ValueDeserializer8DelegateE",
    ),
    "elf": (
        "_ZTIN2v815ValueSerializer8DelegateE",
        "_ZTIN2v817ValueDeserializer8DelegateE",
    ),
}
# The out-of-line default virtual function implementations, which prove the
# Delegate code itself was linked into the single self-contained libnode.a.
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
    """Parse an archive structurally, returning (name, offset, length), thin.

    Delegates to merge_libnode.scan: this file used to carry its own copy of the
    ar walker, and that copy carried the same two defects the merger had - it
    advanced by the header size in GNU *thin* archives (where a regular member
    stores no payload, so the size field describes the REFERENCED file) and it
    only accepted a bare "/N" long-name reference. Two implementations of one
    format is what let the bug survive; there is now one.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent / "node"))
    import merge_libnode  # noqa: PLC0415 - keeps the CLI's import graph lazy

    entries, thin = merge_libnode.scan(archive)
    return [(e.name, e.payload_at, e.payload_len) for e in entries], thin


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
    validate_node_delegate_typeinfo(library, platform)
    print(f"node RTTI validation passed: {library} contains the Delegate RTTI symbols")


def validate_node_delegate_typeinfo(library: Path, platform: str) -> None:
    """Assert the Delegate typeinfo symbols are DEFINED, not merely mentioned.

    A byte scan alone would also match a relocation or an undefined reference,
    so this parses the symbol tables and requires a defined (not 'U') entry of
    the right type/visibility class.
    """
    flavour = "darwin" if platform in ("macos", "ios") else "elf"
    wanted = NODE_DELEGATE_TYPEINFO_MARKERS[flavour]
    defined: set[str] = set()
    for name, data in _iter_member_payloads(library):
        for sym in _defined_symbol_names(data, flavour):
            if sym in wanted:
                defined.add(sym)
    missing = [s for s in wanted if s not in defined]
    if missing:
        fail(
            f"{library} does not DEFINE the Delegate RTTI typeinfo symbol(s): "
            f"{', '.join(missing)}. Node was probably built with RTTI disabled; "
            "check scripts/node/patch_rtti.py ran for this platform."
        )
    print(
        f"node Delegate typeinfo validation passed: {library} defines "
        f"{len(wanted)} Delegate typeinfo symbol(s)"
    )


def _iter_member_payloads(library: Path):
    """Yield (member name, payload bytes) for every archive member.

    Reuses the same structural parser the shape check uses, so thin archives,
    padded long names and BSD __.SYMDEF members are all handled in one place.
    """
    members, _thin = _raw_members(library)
    with library.open("rb") as fh:
        for name, offset, length in members:
            fh.seek(offset)
            yield name, fh.read(length)


def _defined_symbol_names(data: bytes, flavour: str) -> set[str]:
    """Names this object DEFINES with external visibility.

    `nm` is unusable here: it cannot read a Mach-O archive from a Linux runner,
    and Apple's nm has different switches. Parsing the tables directly also
    keeps the check independent of the host toolchain.
    """
    if data[:4] == b"\x7fELF":
        return _defined_symbol_names_elf(data)
    if data[:4] in (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe"):
        return _defined_symbol_names_macho(data)
    return set()


def _defined_symbol_names_macho(data: bytes) -> set[str]:
    """Names this object defines AND exports, for a Mach-O object file.

    N_EXT (0x01) marks a symbol visible outside the object; N_PEXT (0x10) marks
    a private-external (effectively hidden) one, which cannot satisfy a
    downstream reference and so is rejected. N_TYPE 0x0 is N_UNDF.
    """
    import struct

    magic = data[:4]
    is64 = magic == b"\xcf\xfa\xed\xfe"
    header = "<IiiIIII" + ("I" if is64 else "")
    fields = struct.unpack_from(header, data, 0)
    ncmds = fields[4]
    off = struct.calcsize(header)
    symtab = None
    for _ in range(ncmds):
        if off + 8 > len(data):
            break
        cmd, cmdsize = struct.unpack_from("<II", data, off)
        if cmd == 0x2:  # LC_SYMTAB
            symoff, nsyms, stroff, _strsize = struct.unpack_from("<IIII", data, off + 8)
            symtab = (symoff, nsyms, stroff)
            break
        if cmdsize == 0:
            break
        off += cmdsize
    if not symtab:
        return set()
    symoff, nsyms, stroff = symtab
    entsize = 16 if is64 else 12
    out: set[str] = set()
    for i in range(nsyms):
        e = symoff + i * entsize
        if e + entsize > len(data):
            break
        n_strx, n_type = struct.unpack_from("<IB", data, e)
        if (n_type & 0x0E) == 0x00:  # N_UNDF
            continue
        if not (n_type & 0x01):  # not N_EXT: invisible outside this object
            continue
        if n_type & 0x10:  # N_PEXT: private external (hidden)
            continue
        if n_strx == 0:
            continue
        base = stroff + n_strx
        end = data.index(b"\x00", base)
        out.add(data[base:end].decode("latin1"))
    return out


def _defined_symbol_names_elf(data: bytes) -> set[str]:
    """Names this object defines with external visibility.

    LOCAL (static) symbols and HIDDEN ones are excluded on purpose: neither can
    satisfy the embedder's reference, so accepting them would recreate the
    blind spot this check exists to close. Verified on the known-good linux
    archive: both Delegate typeinfos are WEAK/OBJECT/default there, so the
    requirement matches the artefact that actually links.
    """
    import struct

    (_, _, _, _, _, _, shoff, _, _, _, _, shentsize, shnum, shstrndx) = struct.unpack_from(
        "<16sHHIQQQIHHHHHH", data, 0
    )
    sections = []
    for i in range(shnum):
        off = shoff + i * shentsize
        name, typ, _flags, _addr, soff, size, link, _info, _align, entsize = struct.unpack_from(
            "<IIQQQQIIQQ", data, off
        )
        sections.append({"name_off": name, "type": typ, "off": soff, "size": size,
                         "link": link, "entsize": entsize})

    out: set[str] = set()
    for sec in sections:
        if sec["type"] not in (2, 11):  # SHT_SYMTAB, SHT_DYNSYM
            continue
        if sec["link"] >= len(sections):
            continue
        strtab = sections[sec["link"]]
        entsize = sec["entsize"] or 24
        for j in range(sec["size"] // entsize):
            off = sec["off"] + j * entsize
            st_name, st_info, st_other, st_shndx = struct.unpack_from("<IBBH", data, off)
            if st_shndx == 0 or st_name == 0:  # undefined reference
                continue
            binding = st_info >> 4
            if binding == 0:  # STB_LOCAL
                continue
            if (st_other & 0x3) == 0x2:  # STV_HIDDEN
                continue
            base = strtab["off"] + st_name
            end = data.index(b"\x00", base)
            out.add(data[base:end].decode("latin1"))
    _ = shstrndx
    return out


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
