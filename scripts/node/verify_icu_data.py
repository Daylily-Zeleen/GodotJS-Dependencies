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

# ICU 78 merged these locale bundles into their parents (en_US -> en,
# es_ES -> es, ...); the .res files no longer exist in the source data and
# the locales resolve through fallback, so their absence is expected.
FALLBACK_OK = {"en_US", "es_ES", "fr_FR", "ja_JP", "ru_RU", "th_TH", "zh_Hans_HK"}

# Parent-anchor locales our targets depend on. Without them icupkg's
# dependency cascade deletes the dependent locales (en_GB -> en_001,
# es_MX -> es_419); they must stay in the trimmed archive.
REQUIRED = tuple(s for s in SELECTED if s not in FALLBACK_OK) + ("en_001", "es_419")

# Legacy/alias bundles that exist in the raw data but are absent from the
# res_index manifest, so icutrim never queues them for removal.
ALLOWED_EXTRA = {"zh_CN", "zh_HK", "zh_TW"}

# The full ICU data archive is ~33 MB; a trimmed archive above this bound
# means the trim step silently failed to run.
MAX_ARCHIVE_BYTES = 20 * 1024 * 1024

# icutrim rebuilds res_index.res for every trimmed resource tree (lang/region/
# curr/...); it is a per-tree index, not a locale resource. pool.res is the
# shared string pool the tree bundles reference.
NON_LOCALE_RES = {"res_index", "pool"}


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
    if archive.stat().st_size > MAX_ARCHIVE_BYTES:
        fail(
            f"trimmed archive is {archive.stat().st_size} bytes; above "
            f"{MAX_ARCHIVE_BYTES} suggests the ICU trim step did not run"
        )
    for locale in REQUIRED:
        if not re.search(rf"(?:^|[/\\])lang[/\\]{re.escape(locale)}\.res(?:$|\s)", listing, re.MULTILINE):
            fail(f"selected locale data is missing: lang/{locale}.res")
    locale_entries = set(re.findall(r"(?:^|[/\\])lang[/\\]([^/\\\s]+)\.res(?:$|\s)", listing, re.MULTILINE))
    unexpected = sorted(locale_entries - set(SELECTED) - set(REQUIRED) - ALLOWED_EXTRA - NON_LOCALE_RES)
    if unexpected:
        fail(f"unselected locale data is present: {unexpected}")
    print(f"ICU data passed: {archive}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
