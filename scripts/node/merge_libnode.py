#!/usr/bin/env python3
"""Merge every static library produced by a Node.js build into one
self-contained libnode.a, mirroring the upstream moluopro/libnode release
layout (a single archive the embedder links against).

Node's gyp/make build emits many independent archives (libnode.a,
libv8_base_without_compiler.a, libicu*.a, libopenssl.a, ...). The Delegate
RTTI symbols the downstream GodotJS embedder needs live in the v8 archives,
which are NOT referenced by node's own libnode.a. Merging them here makes the
staged libnode.a self-contained so a single --whole-archive link resolves
every symbol, exactly like the official libnode release.

Usage: merge_libnode.py <build_out_dir> <output_lib_path>
  build_out_dir : node/out/Release (searched recursively for *.a)
  output_lib_path: path written for the merged libnode.a
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile

# Test-only archives must never be merged into the shipped library.
EXCLUDE_RE = re.compile(r"(^|/)(cctest|gtest|gtest_main|.*_test)\.a$")


def fail(msg: str) -> "NoReturn":
    print(f"merge_libnode error: {msg}", file=sys.stderr)
    raise SystemExit(1)


def list_libs(build_out: str) -> list[str]:
    libs: list[str] = []
    for root, _dirs, files in os.walk(build_out):
        for name in files:
            if not name.endswith(".a"):
                continue
            if EXCLUDE_RE.search(os.path.join(root, name)):
                continue
            libs.append(os.path.join(root, name))
    if not libs:
        fail(f"no static libraries found under {build_out}")
    return sorted(libs)


def extract_members(lib: str, dest: str) -> list[str]:
    """Extract every member of `lib` into `dest` with collision-free names.

    Uses `ar p` so it works for both regular and thin archives (the referenced
    object bytes are emitted on stdout). Returns the list of extracted paths.
    """
    members = subprocess.run(
        ["ar", "t", lib], capture_output=True, text=True
    )
    if members.returncode != 0:
        fail(f"ar t failed on {lib}: {members.stderr.strip()}")
    extracted: list[str] = []
    base = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.relpath(lib, os.path.dirname(dest)))
    for member in members.stdout.splitlines():
        member = member.strip()
        if not member:
            continue
        safe = f"{base}__{re.sub(r'[^A-Za-z0-9._-]', '_', member)}"
        out_path = os.path.join(dest, safe)
        # `ar p` prints the raw member content regardless of thin/regular form.
        with open(out_path, "wb") as fh:
            proc = subprocess.run(["ar", "p", lib, member], capture_output=True)
            if proc.returncode != 0:
                fail(f"ar p failed on {lib}!{member}: {proc.stderr.decode('replace').strip()}")
            fh.write(proc.stdout)
        extracted.append(out_path)
    return extracted


def main() -> int:
    if len(sys.argv) != 3:
        fail("usage: merge_libnode.py <build_out_dir> <output_lib_path>")
    build_out = sys.argv[1]
    output = sys.argv[2]
    if not os.path.isdir(build_out):
        fail(f"build output dir not found: {build_out}")

    libs = list_libs(build_out)
    print(f"merging {len(libs)} static libraries into {output}")

    tmp = tempfile.mkdtemp(prefix="merge_libnode_")
    try:
        objects: list[str] = []
        for lib in libs:
            objects.extend(extract_members(lib, tmp))
        if not objects:
            fail("no object members extracted from any library")

        os.makedirs(os.path.dirname(os.path.abspath(output)), exist_ok=True)
        # Portable archive creation: `ar rcs` works on both GNU and BSD ar.
        result = subprocess.run(["ar", "rcs", output, *objects])
        if result.returncode != 0:
            fail(f"ar rcs failed ({result.returncode})")
        size = os.path.getsize(output)
        print(f"merged archive written: {output} ({size} bytes, {len(objects)} objects)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
