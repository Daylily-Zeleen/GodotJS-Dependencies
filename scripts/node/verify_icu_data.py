#!/usr/bin/env python3
"""Validate the small-ICU data archive produced by a Node build."""
from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path


SELECTED = (
    "root", "en", "en_GB", "en_US", "es", "es_ES", "es_MX", "fr", "fr_CA",
    "fr_FR", "ru", "ru_RU", "zh", "zh_Hans", "zh_Hans_CN", "zh_Hans_HK",
    "zh_Hant", "zh_Hant_HK", "zh_Hant_TW",
)

# icutrim rebuilds res_index.res for every trimmed resource tree (lang/region/
# curr/...); it is a per-tree index, not a locale resource.
NON_LOCALE_RES = {"res_index"}


def fail(message: str) -> "NoReturn":
    raise SystemExit(f"ICU data error: {message}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="Node output directory, usually out")
    args = parser.parse_args()

    all_dat_files = sorted(args.root.rglob("*.dat"))
    # icutrim writes the trimmed archive as icutmp/icudt<ver><endian>.dat; the
    # item names inside keep that prefix, so icupkg can list it directly.
    tmp_files = [
        path for path in all_dat_files
        if path.parent.name == "icutmp" and path.name.startswith("icudt")
    ]
    # The genccode step additionally copies the trimmed archive to a
    # icusmdt<ver>.dat name (POSIX only) purely to name the embedded C/asm
    # entry; the copy keeps the original icudt<ver><endian>/ item prefix, so
    # icupkg refuses to list it. It must be byte-identical to the archive.
    final_files = [
        path for path in all_dat_files if path.name.startswith("icusmdt")
    ]

    if not tmp_files:
        fail(f"no small-ICU trimmed archive found in icutmp: {all_dat_files}")
    if len(tmp_files) > 1:
        fail(f"multiple small-ICU trimmed archives: {tmp_files}")
    archive = tmp_files[0]

    allowed = set(tmp_files + final_files)
    unexpected_dat = sorted(set(all_dat_files) - allowed)
    if unexpected_dat:
        fail(f"unexpected or stale ICU .dat files: {unexpected_dat}")

    for final_path in final_files:
        if final_path.read_bytes() != archive.read_bytes():
            fail(f"final archive diverges from trimmed archive: {final_path}")

    tools = sorted(
        path for path in args.root.rglob("icupkg*")
        if path.is_file() and path.suffix.lower() in ("", ".exe")
    )
    if not tools:
        fail(f"no host icupkg tool found below {args.root}")
    tool = tools[0]
    result = subprocess.run(
        [str(tool), "-l", str(archive)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        fail(f"icupkg could not list {archive}: {result.stderr.strip()}")
    listing = result.stdout
    for locale in SELECTED:
        if not re.search(rf"(?:^|[/\\])lang[/\\]{re.escape(locale)}\.res(?:$|\s)", listing, re.MULTILINE):
            fail(f"selected locale data is missing: lang/{locale}.res")
    locale_entries = set(re.findall(r"(?:^|[/\\])lang[/\\]([^/\\\s]+)\.res(?:$|\s)", listing, re.MULTILINE))
    unexpected = sorted(locale_entries - set(SELECTED) - NON_LOCALE_RES)
    if unexpected:
        fail(f"unselected locale data is present: {unexpected}")
    print(f"ICU data passed: {archive}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
