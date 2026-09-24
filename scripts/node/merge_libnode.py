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


def _resolve_name(raw: str, head: bytes, strtab: bytes) -> tuple[str | None, int]:
    """Resolve an ar member name. Returns (name, bytes_to_skip_in_payload)."""
    if raw == "//":
        return None, 0  # GNU/MSVC long-name string table
    if raw in ("/", "/SYM64/", "/<ECSYMBOLS>/"):
        return None, 0  # archive symbol table / linker members
    if raw.startswith("#1/"):
        # BSD style: the real name lives at the front of the payload.
        nlen = int(raw[3:])
        return head[:nlen].decode("utf-8", "replace").rstrip("\0"), nlen
    if raw.startswith("/") and raw[1:].isdigit():
        # GNU style: offset into the '//' table. The entry terminator differs by
        # producer - GNU writes "name/\n", MSVC writes "name\0" - and MSVC's
        # table as a whole also ends in a newline. Taking whichever terminator
        # comes FIRST handles both: preferring "\n" would return the rest of the
        # table glued together for MSVC (which is what silently produced member
        # names containing NULs and every following path).
        start = int(raw[1:])
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
            if raw_name == "//":
                strtab = fh.read(size)
                name = None
                skip = 0
            else:
                # The name may be embedded in the payload (BSD '#1/'), so read
                # at most a bounded prefix to resolve it.
                head = fh.read(min(size, 256))
                name, skip = _resolve_name(raw_name, head, strtab)

            if name is not None:
                entries.append(Entry(name, payload_at + skip, size - skip))

            offset = payload_at + size
            if offset % 2:
                offset += 1

    return entries, thin


def total_members(path: Path) -> int:
    entries, _thin = scan(path)
    return len(entries)


# --------------------------------------------------------------------------
# Link-set derivation ("which archives does node itself link?")
# --------------------------------------------------------------------------


def from_makefile(build_out: Path) -> list[Path]:
    """Read the archive list from gyp's generated makefiles.

    gyp writes the exact link inputs into LD_INPUTS in <target>.target.mk, with
    archive paths spelled $(obj).target/... where $(obj) is <build_out>/obj
    (out/Makefile sets obj := $(builddir)/obj and builddir ends in BUILDTYPE).
    The .mk files themselves land next to the toplevel Makefile, which node's
    configure.py points at out/ via gyp's --generator-output, so they are
    searched upward from the build directory.

    Order is preserved: static archive order decides symbol resolution.
    """
    # $(obj).target is the make variable $(obj) followed by the literal
    # ".target" toolset suffix, i.e. <build_out>/obj.target/...
    obj = build_out / "obj"
    for directory in (build_out.parent, build_out, build_out.parent.parent):
        for name in ("node.target.mk", "libnode.target.mk"):
            mk = directory / name
            if not mk.is_file():
                continue
            text = mk.read_text(encoding="utf-8", errors="replace")
            found: list[Path] = []
            for match in re.finditer(r"\$\(obj\)\.target/([\w./-]+\.a)", text):
                lib = Path(f"{obj}.target") / match.group(1)
                if lib not in found:
                    found.append(lib)
            if found:
                return found
    return []


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


def merge_set(build_out: Path) -> list[Path]:
    libs = from_makefile(build_out) or from_vcxproj(build_out)
    if not libs:
        fail(
            f"could not derive the archive link set from {build_out}: no "
            "node.target.mk LD_INPUTS and no node.vcxproj StaticLibrary "
            "references. Refusing to fall back to a directory scan - merging "
            "every archive found pulls in host-only build tools and a second "
            "isolate setup implementation."
        )
    missing = [str(lib) for lib in libs if not lib.is_file()]
    if missing:
        fail(f"link set references archives that do not exist: {missing}")
    return libs


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
