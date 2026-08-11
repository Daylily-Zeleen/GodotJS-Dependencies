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


def fail(message: str) -> "NoReturn":
    raise SystemExit(f"ICU data error: {message}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="Node output directory, usually out")
    args = parser.parse_args()

    all_dat_files = sorted(args.root.rglob("*.dat"))
    final_files = [path for path in all_dat_files if path.name.startswith("icusmdt")]
    tmp_files = [
        path for path in all_dat_files
        if path.parent.name == "icutmp" and path.name.startswith("icudt")
    ]
    # Non-Windows GYP normally leaves both the trimmed input
    # (icutmp/icudt*.dat) and the renamed final archive (icutmp/icusmdt*.dat)
    # when --delete-tmp is disabled. Windows may leave only the former.
    if len(final_files) == 1:
        archive = final_files[0]
        allowed = set(final_files + tmp_files)
        if not tmp_files or any(path.parent != archive.parent for path in tmp_files):
            fail(f"small-ICU temporary/final archives are ambiguous: {all_dat_files}")
    elif not final_files and len(tmp_files) == 1:
        archive = tmp_files[0]
        allowed = set(tmp_files)
    else:
        fail(f"expected one small-ICU final archive, found {all_dat_files}")
    unexpected_dat = sorted(set(all_dat_files) - allowed)
    if unexpected_dat:
        fail(f"unexpected or stale ICU .dat files: {unexpected_dat}")
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
        if not re.search(rf"(?:^|[/\\\\])lang[/\\\\]{re.escape(locale)}\.res(?:$|\s)", listing, re.MULTILINE):
            fail(f"selected locale data is missing: lang/{locale}.res")
    locale_entries = set(re.findall(r"(?:^|[/\\\\])lang[/\\\\]([^/\\\\\s]+)\.res(?:$|\s)", listing, re.MULTILINE))
    unexpected = sorted(locale_entries - set(SELECTED))
    if unexpected:
        fail(f"unselected locale data is present: {unexpected}")
    print(f"ICU data passed: {archive}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
