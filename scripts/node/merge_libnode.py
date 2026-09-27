#!/usr/bin/env python3
"""Merge the static libraries Node.js links into one self-contained libnode
archive, mirroring the upstream moluopro/libnode release layout (a single
archive an embedder links against).

Two independent defects are fixed here, both of which only surface in the
downstream embedder:

1. WRONG MERGE SET. The previous implementation merged *every* .a/.lib found
   under out/Release. Node links only a subset; the rest are host build tools
   and test binaries. On Linux that injected libicutools.a (ICU built for the
   *build host*) and libv8_init.a (which carries setup-isolate-full, clashing
   with the setup-isolate-deserialize in libv8_snapshot.a), producing 12,634
   "multiple definition" errors plus a host-ICU undefined symbol when the
   embedder linked the archive into a shared object. The merge set is now read
   from Node's own link metadata.

2. LOST MEMBERS. Members were extracted with `ar p <archive> <member>`, which
   resolves by *name*. gyp creates Node's archives with `ar crs` from object
   paths such as heap/sweeper.o and heap/cppgc/sweeper.o, and ar stores only
   the basename - so one archive legitimately holds "sweeper.o" twice and
   `ar p` returns the first match for both. The known-good moluopro archives
   contain 58 duplicate-name groups (all with differing content), so this is
   normal rather than exotic. The macOS embedder failed to link with 131
   undefined symbols (TorqueGenerated*Print, cppgc::internal::Sweeper, ...) for
   exactly this reason. Members are now extracted positionally by walking the
   archive structure, so no name is ever used as a key and nothing is dropped.

Usage: merge_libnode.py <build_out_dir> <output_lib_path> [--list-only]
  build_out_dir  : node/out/Release (link metadata and archives are read here)
  output_lib_path: path written for the merged archive (.a or .lib)
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import BinaryIO, NamedTuple

ARCHIVE_MAGIC = b"!<arch>\n"
THIN_MAGIC = b"!<thin>\n"
HEADER_SIZE = 60


def fail(message: str) -> "NoReturn":
    print(f"merge_libnode error: {message}", file=sys.stderr)
    raise SystemExit(1)


# --------------------------------------------------------------------------
# Archive reading (positional - names are diagnostics only)
# --------------------------------------------------------------------------


class Entry(NamedTuple):
    name: str
    payload_at: int  # absolute offset of the member's object bytes
    payload_len: int


# BSD/macOS linker members: the ranlib symbol index of the archive that produced
# them. They are not object files, and handing one to libtool only earns "not a
# mach-o" before it is dropped - so the merged archive ends up with FEWER members
# than were extracted (observed on macos: 3630 members from 37 archives became
# 3594, the missing 36 being SYMDEF). macOS stores the name BSD-style, i.e.
# embedded in the payload behind "#1/<len>", so this has to be matched against
# the RESOLVED name rather than the 16-byte header field.
_LINKER_MEMBERS = frozenset({
    "__.SYMDEF",
    "__.SYMDEF SORTED",
    "__.SYMDEF_64",
    "__.SYMDEF_64 SORTED",
})


def _resolve_name(raw: str, head: bytes, strtab: bytes) -> tuple[str | None, int]:
    """Resolve an ar member name. Returns (name, bytes_to_skip_in_payload)."""
    if raw == "//":
        return None, 0  # GNU/MSVC long-name string table
    if raw in ("/", "/SYM64/", "/<ECSYMBOLS>/"):
        return None, 0  # archive symbol table / linker members
    if raw.startswith("#1/"):
        # BSD style: the real name lives at the front of the payload.
        nlen = int(raw[3:])
        name = head[:nlen].decode("utf-8", "replace").rstrip("\0")
        if name in _LINKER_MEMBERS:
            return None, 0
        return name, nlen
    # GNU style: an offset into the '//' table. The 16-byte name field is padded
    # with spaces, and some binutils versions additionally close it with '/', so
    # "/0", "/0       " and "/0             /" all denote offset 0. Requiring a
    # bare "/N" made the padded forms fall through to the short-name path below,
    # which surfaced the raw field ("/0             ") as a bogus absolute path -
    # precisely the member name CI reported as missing.
    ref = raw[1:].strip().rstrip("/").strip() if raw.startswith("/") else ""
    if ref.isdigit():
        if not strtab:
            fail(f"long-name reference {raw!r} precedes the archive's string table")
        # The entry terminator differs by producer - GNU writes "name/\n", MSVC
        # writes "name\0" - and MSVC's table as a whole also ends in a newline.
        # Taking whichever terminator comes FIRST handles both: preferring "\n"
        # would return the rest of the table glued together for MSVC (which is
        # what silently produced member names containing NULs and every
        # following path).
        start = int(ref)
        candidates = [
            end for end in (
                strtab.find(b"/\n", start),
                strtab.find(b"\n", start),
                strtab.find(b"\0", start),
            ) if end != -1
        ]
        end = min(candidates) if candidates else len(strtab)
        return strtab[start:end].decode("utf-8", "replace").rstrip("\0"), 0
    return (raw[:-1] if raw.endswith("/") else raw), 0


def scan(path: Path) -> tuple[list[Entry], bool]:
    """Walk `path` and return (object members, is_thin).

    Streaming: only the long-name table and one header are ever resident, which
    matters because the published archives run to several GB.
    """
    entries: list[Entry] = []
    with path.open("rb") as fh:
        magic = fh.read(8)
        if magic not in (ARCHIVE_MAGIC, THIN_MAGIC):
            fail(f"{path} is not an ar archive (magic {magic!r})")
        thin = magic == THIN_MAGIC

        offset = 8
        strtab = b""
        while True:
            fh.seek(offset)
            header = fh.read(HEADER_SIZE)
            if len(header) < HEADER_SIZE:
                break
            if header[58:60] != b"`\n":
                fail(f"{path}: malformed archive header at offset {offset}")
            raw_name = header[0:16].decode("ascii", "replace").strip()
            try:
                size = int(header[48:58].decode("ascii").strip() or "0")
            except ValueError:
                fail(f"{path}: malformed size field at offset {offset}: {header[48:58]!r}")

            payload_at = offset + HEADER_SIZE
            if thin:
                # A thin archive stores no bytes for a regular member: the size
                # field carries the length of the file the member REFERENCES.
                # Advancing by `size` therefore walks into the middle of the
                # archive and silently drops every later member (observed on
                # linux as "thin member ... is missing"). Only the special
                # members - the symbol index and the long-name table - actually
                # carry a payload.
                if raw_name in ("//", "/", "/SYM64/", "/<ECSYMBOLS>/"):
                    if raw_name == "//":
                        strtab = fh.read(size)
                    name, skip, stored = None, 0, size
                else:
                    # Nothing to read past the header, so resolve against the
                    # string table alone.
                    name, skip = _resolve_name(raw_name, b"", strtab)
                    stored = 0
            elif raw_name == "//":
                strtab = fh.read(size)
                name, skip, stored = None, 0, size
            else:
                # The name may be embedded in the payload (BSD '#1/'), so read
                # at most a bounded prefix to resolve it.
                head = fh.read(min(size, 256))
                name, skip = _resolve_name(raw_name, head, strtab)
                stored = size

            if name is not None:
                entries.append(Entry(name, payload_at + skip, size - skip))

            offset = payload_at + stored
            if offset % 2:
                offset += 1

    return entries, thin


def total_members(path: Path) -> int:
    entries, _thin = scan(path)
    return len(entries)


# --------------------------------------------------------------------------
# Link-set derivation ("which archives does node itself link?")
# --------------------------------------------------------------------------


def _archive_paths_from_mk(mk: Path, build_out: Path) -> list[Path]:
    """Archive paths node links, as recorded in one gyp makefile.

    gyp spells a static library's output differently per flavor, and the make
    generator writes whichever form into the *referencing* target's LD_INPUTS:

      linux/android/ohos  $(obj).target/libX.a   ($(obj).target = <build_out>/obj.target)
      macos/ios           $(builddir)/libX.a     ($(builddir) = <build_out> = .../Release)

    Node's own ``node`` target is what carries LD_INPUTS, so its makefile is the
    one to read. Handling only the $(obj).target form made the whole macos leg
    die at "could not derive the archive link set" even though node.target.mk was
    found - the referenced paths were simply spelled the other way.
    """
    text = mk.read_text(encoding="utf-8", errors="replace")

    # Each variable resolves to a directory; the match keeps whichever prefix it
    # was written with so the relative remainder can be re-joined.
    roots = {
        "$(obj).target": Path(f"{build_out / 'obj'}.target"),
        "$(obj).target/": Path(f"{build_out / 'obj'}.target"),
        "$(builddir)": build_out,
        "$(builddir)/": build_out,
    }
    found: list[Path] = []
    for variable, root in roots.items():
        for match in re.finditer(
            re.escape(variable) + r"/?([\w./-]+\.(?:a|lib))", text
        ):
            lib = root / match.group(1)
            if lib not in found:
                found.append(lib)
    return found


def _mk_candidates(build_out: Path) -> list[Path]:
    """Every makefile gyp might have emitted, nearest-first.

    The layout differs by generator output: node's configure passes
    --generator-output <node>/out, and `make -C out` runs the root Makefile, so
    node.target.mk normally sits in build_out.parent. Older/other layouts place
    it beside or above the build dir, so search outward, bounded - an unbounded
    walk would be slow on a build tree and could pick up unrelated paths.
    """
    roots = [build_out.parent, build_out, build_out.parent.parent]
    names = ("node.target.mk", "libnode.target.mk", "node_base.target.mk")
    candidates: list[Path] = []
    for root in roots:
        for name in names:
            candidate = root / name
            if candidate.is_file():
                candidates.append(candidate)
        # Deeper layouts (e.g. out/<toolset>/Release/) hold the mk files too.
        if root.is_dir():
            for depth in range(1, 3):
                pattern = "/".join(["*"] * depth) + "/{node,libnode,node_base}.target.mk"
                try:
                    candidates.extend(sorted(root.glob(pattern)))
                except OSError:
                    pass
    return candidates


def from_vcxproj(build_out: Path) -> list[Path]:
    """Read the archive list from Node's MSBuild projects.

    node.vcxproj references every static library it links, alongside
    header-only and executable projects that ConfigurationType filters out;
    each project emits <OutDir>lib/<project>.lib.
    """
    root = build_out.parent.parent  # out/Release -> node root
    vcxproj = root / "node.vcxproj"
    if not vcxproj.is_file():
        return []
    text = vcxproj.read_text(encoding="utf-8", errors="replace")
    libdir = build_out / "lib"
    found: list[Path] = []
    for ref in re.findall(r'<ProjectReference Include="([^"]+)"', text):
        project = root / ref.replace("\\", "/")
        if not project.is_file():
            continue
        if "<ConfigurationType>StaticLibrary<" not in project.read_text(
            encoding="utf-8", errors="replace"
        ):
            continue
        stem = Path(ref.replace("\\", "/")).name[: -len(".vcxproj")]
        lib = libdir / f"{stem}.lib"
        if lib.is_file() and lib not in found:
            found.append(lib)
    self_lib = libdir / "libnode.lib"  # the project's own output
    if self_lib.is_file() and self_lib not in found:
        found.append(self_lib)
    return found


def _symbols(archive: Path) -> tuple[set[str], set[str]]:
    """(defined, undefined) global symbol names in `archive`.

    COFF is parsed directly (fast, no toolchain needed). ELF/Mach-O goes through
    `nm`, which is present wherever these archives are built - and this path only
    runs when the link set looks incomplete, so it costs nothing normally.
    """
    defined: set[str] = set()
    undefined: set[str] = set()

    if archive.suffix != ".lib":
        nm = shutil.which("nm")
        if nm is None:
            return defined, undefined
        result = subprocess.run([nm, "-g", str(archive)], capture_output=True, text=True, errors="replace")
        if result.returncode != 0:
            return defined, undefined
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) < 2:
                continue
            kind, name = parts[-2], parts[-1]
            if kind == "U":
                undefined.add(name)
            elif kind.isupper() or kind in ("W", "V"):
                defined.add(name)
        return defined, undefined

    entries, _thin = scan(archive)
    for entry in entries:
        with archive.open("rb") as fh:
            fh.seek(entry.payload_at)
            data = fh.read(entry.payload_len)
        if len(data) < 20 or struct.unpack_from("<H", data, 0)[0] not in (0x8664, 0xAA64, 0x14C):
            continue
        ptr, nsyms = struct.unpack_from("<II", data, 8)
        if not ptr or not nsyms:
            continue
        strtab_at = ptr + nsyms * 18
        strtab = b""
        if strtab_at + 4 <= len(data):
            size = struct.unpack_from("<I", data, strtab_at)[0]
            strtab = data[strtab_at + 4: strtab_at + 4 + size]
        i = 0
        while i < nsyms:
            off = ptr + i * 18
            if off + 18 > len(data):
                break
            raw = data[off:off + 8]
            section = struct.unpack_from("<h", data, off + 12)[0]
            storage = data[off + 16]
            i += 1 + data[off + 17]
            if storage not in (2, 105):
                continue
            if raw[:4] == b"\0\0\0\0":
                idx = struct.unpack_from("<I", raw, 4)[0] - 4
                if not 0 <= idx < len(strtab):
                    continue
                end = strtab.find(b"\0", idx)
                name = strtab[idx:end if end != -1 else len(strtab)].decode("utf-8", "replace")
            else:
                name = raw.split(b"\0", 1)[0].decode("utf-8", "replace")
            if not name:
                continue
            (defined if section > 0 else undefined).add(name)
    return defined, undefined


# node's own output archives: either the project archive (libnode.lib/a) or a
# previously merged artifact. Never merge these back into themselves.
_OWN_OUTPUT = ("libnode.a", "libnode.lib", "libnode_self.lib", "libnode_merged.lib", "libnode_novcvars.lib")


def _candidate_archives(build_out: Path) -> list[Path]:
    """Every archive the build produced, excluding node's own output archives."""
    roots = [build_out / "lib", Path(f"{build_out / 'obj'}.target"), build_out / "obj.target", build_out]
    found: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        for lib in sorted(root.rglob("*")):
            if lib.suffix not in (".a", ".lib") or not lib.is_file():
                continue
            if lib.name in _OWN_OUTPUT:
                continue
            if lib not in found:
                found.append(lib)
    return found


def merge_set(build_out: Path) -> list[Path]:
    """Archives node itself links, from makefile metadata or the MSBuild tree."""
    libs: list[Path] = []
    for mk in _mk_candidates(build_out):
        found = [lib for lib in _archive_paths_from_mk(mk, build_out) if lib.is_file()]
        if found:
            print(f"link set source: {mk} ({len(found)} archives)")
            libs = found
            break
    if not libs:
        libs = from_vcxproj(build_out)
        if libs:
            print(f"link set source: node.vcxproj ({len(libs)} static library projects)")

    if libs:
        added = complete_link_set(build_out, libs)
        if added:
            print(f"link set completed with {len(added)} archive(s) the metadata omitted:")
            for lib in added:
                print(f"  + {lib}")
        return libs + added

    # Self-diagnosing failure. Losing an hour of CI to "could not derive the
    # link set" is far worse than a vague error, and the next person needs to
    # know whether the metadata was absent or merely somewhere unexpected.
    mks: list[str] = []
    for root in (build_out.parent, build_out):
        if root.is_dir():
            mks.extend(sorted(str(p) for p in root.rglob("*.target.mk"))[:20])
    archives = [str(p) for p in _candidate_archives(build_out)][:40]
    fail(
        "could not derive the archive link set.\n"
        f"  build_out      : {build_out}\n"
        f"  exists         : {build_out.is_dir()}\n"
        f"  mk files found : {mks or 'NONE'}\n"
        f"  candidates     : {[str(p) for p in _mk_candidates(build_out)] or 'NONE'}\n"
        f"  archives found : {archives or 'NONE'}\n"
        f"  vcxproj        : {build_out.parent.parent / 'node.vcxproj'}\n"
        "Refusing to fall back to a directory scan: merging every archive found "
        "pulls in host-only build tools and a second isolate setup implementation."
    )


def complete_link_set(build_out: Path, linked: list[Path]) -> list[Path]:
    """Add archives that satisfy symbols the linked set leaves undefined.

    Safety net for incomplete link metadata. The rule is deliberately narrow: an
    archive is added only if it defines something the library still needs, so
    test archives (gtest), host build tools (icutools) and the second isolate
    setup (v8_init) stay out - none of them answer an undefined symbol of the
    embedded library - while a genuinely omitted archive is pulled back in.
    """
    provided: set[str] = set()
    needed: set[str] = set()
    for lib in linked:
        d, u = _symbols(lib)
        provided |= d
        needed |= u
    needed -= provided

    added: list[Path] = []
    for candidate in _candidate_archives(build_out):
        if candidate in linked:
            continue
        d, u = _symbols(candidate)
        if not d:
            continue
        if d & needed:
            added.append(candidate)
            provided |= d
            needed |= u
            needed -= provided
    return added


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------


def _copy_range(src: Path, start: int, length: int, dst: BinaryIO) -> None:
    with src.open("rb") as fh:
        fh.seek(start)
        remaining = length
        while remaining:
            chunk = fh.read(min(remaining, 1 << 22))
            if not chunk:
                fail(f"{src}: unexpected end of file while extracting a member")
            dst.write(chunk)
            remaining -= len(chunk)


def find_msvc_tool(name: str) -> str | None:
    """Locate an MSVC tool (lib.exe / link.exe / cl.exe) without vcvars.

    build-windows.ps1 runs vcvars via vcbuild.bat, but callers must not depend
    on that: prefer PATH, then ask vswhere for the VS install and take the
    native x64 host toolset (the x86-hosted ones still work but warn and
    restart themselves as the 64-bit tool).
    """
    found = shutil.which(name)
    if found:
        return found
    vswhere = Path(
        r"C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe"
    )
    roots: list[Path] = []
    if vswhere.is_file():
        try:
            out = subprocess.run(
                [str(vswhere), "-latest", "-products", "*",
                 "-requires", "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
                 "-property", "installationPath"],
                capture_output=True, text=True,
            ).stdout.strip()
            if out:
                roots.append(Path(out))
        except OSError:
            pass
    for root in roots:
        tools = root / "VC" / "Tools" / "MSVC"
        if not tools.is_dir():
            continue
        for host in ("Hostx64", "Hostx86"):
            for arch in ("x64", "arm64", "x86"):
                candidates = sorted(tools.glob(f"*/bin/{host}/{arch}/{name}"))
                if candidates:
                    return str(candidates[-1])
    return None


def write_archive(output: Path, inputs: list[Path]) -> None:
    """Create `output` from `inputs` with the platform's own archiver."""
    response = output.with_name(output.name + ".members")
    response.write_text("\n".join(str(i) for i in inputs), encoding="utf-8")
    if output.exists():
        output.unlink()
    env = None
    try:
        if output.suffix == ".lib":
            # MSVC: lib.exe merges archives losslessly and keeps duplicated
            # member names (unlike a name-keyed rebuild). Verified locally:
            # merging two archives that each hold "same.obj" yields both.
            lib_exe = find_msvc_tool("lib.exe")
            if lib_exe is None:
                fail("could not locate lib.exe (not on PATH and vswhere found no VS install)")
            cmd = [lib_exe, "/nologo", f"/OUT:{output}", f"@{response}"]
            # lib.exe needs its own directory on PATH to find its DLLs; it does
            # NOT need a full vcvars environment (verified locally).
            env = dict(os.environ)
            env["PATH"] = str(Path(lib_exe).parent) + os.pathsep + env.get("PATH", "")
        elif sys.platform == "darwin":
            cmd = ["libtool", "-static", "-o", str(output), "-filelist", str(response)]
        else:
            cmd = ["ar", "rcs", str(output), f"@{response}"]
        result = subprocess.run(cmd, env=env)
        if result.returncode != 0:
            fail(f"{Path(cmd[0]).name} failed with exit code {result.returncode}")
    finally:
        response.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("build_out", type=Path, help="node/out/Release")
    parser.add_argument("output", type=Path, help="merged archive to write")
    parser.add_argument(
        "--list-only",
        action="store_true",
        help="print the derived merge set and exit (nothing is written)",
    )
    args = parser.parse_args()

    if not args.build_out.is_dir():
        fail(f"build output dir not found: {args.build_out}")

    libs = merge_set(args.build_out)
    if args.list_only:
        for lib in libs:
            print(lib)
        print(f"{len(libs)} archives in the merge set")
        return 0

    print(f"merging the {len(libs)} libraries that node itself links into {args.output}")

    temp_dir = Path(tempfile.mkdtemp(prefix="merge_libnode_"))
    members: list[Path] = []
    names: list[str] = []
    try:
        for index, lib in enumerate(libs):
            entries, thin = scan(lib)
            if not entries:
                fail(f"{lib} contains no object members")
            for member_index, entry in enumerate(entries):
                # One directory per member: the file keeps the member's ORIGINAL
                # basename (so the output archive is name-faithful, exactly like
                # moluopro's) while the directory keeps it unique on disk. That
                # is what lets two members both named "sweeper.o" survive
                # instead of one being overwritten.
                slot = temp_dir / f"{index:03d}_{member_index:05d}"
                slot.mkdir()
                dest = slot / os.path.basename(entry.name.replace("\\", "/"))
                with dest.open("wb") as out:
                    if thin:
                        source = Path(entry.name)
                        if not source.is_absolute():
                            source = lib.parent / source
                        if not source.is_file():
                            fail(f"{lib}: thin member {entry.name} is missing")
                        _copy_range(source, 0, source.stat().st_size, out)
                    else:
                        _copy_range(lib, entry.payload_at, entry.payload_len, out)
                if dest.stat().st_size == 0:
                    fail(f"{lib}: member {entry.name} is empty")
                members.append(dest)
                names.append(os.path.basename(entry.name.replace("\\", "/")))

        if not members:
            fail("no object members extracted from any library")

        wrote = Path(tempfile.mkdtemp(prefix="merge_libnode_out_"))
        try:
            staged = wrote / args.output.name
            write_archive(staged, members)
            size = staged.stat().st_size
            if size < 1024 * 1024:
                fail(f"merged archive suspiciously small ({size} bytes); refusing to stage garbage")

            # Fail closed: the merge must be a faithful concatenation. Member
            # count catches dropped members (the macOS `sweeper.o` collision
            # silently removed 8 of them); the name multiset additionally
            # catches renames that would still leave the count intact.
            written_entries, _ = scan(staged)
            if len(written_entries) != len(members):
                fail(
                    f"member count changed during the merge: {len(members)} members "
                    f"from {len(libs)} archives became {len(written_entries)} in the output"
                )
            # Compare on basenames: archivers store the basename, so a rounded
            # trip through e.g. BSD ar legitimately shortens a long path name.
            if sorted(os.path.basename(e.name.replace("\\", "/")) for e in written_entries) != sorted(names):
                fail("member names changed during the merge; the output is not a faithful copy")

            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.unlink(missing_ok=True)
            shutil.move(str(staged), str(args.output))
            print(
                f"merged archive written: {args.output} "
                f"({size} bytes, {len(members)} members from {len(libs)} archives)"
            )
        finally:
            shutil.rmtree(wrote, ignore_errors=True)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
