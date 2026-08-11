#!/usr/bin/env python3
"""Check the generated Node ICU configuration matches libnode's ICU profile."""
from __future__ import annotations

import argparse
import re
from pathlib import Path


PROFILE_NAME = "selected-locales-full-break-v1"
LOCALES = (
    "root", "en", "en_GB", "en_US", "es", "es_ES", "es_MX", "fr", "fr_CA",
    "fr_FR", "ru", "ru_RU", "zh", "zh_Hans", "zh_Hans_CN", "zh_Hans_HK",
    "zh_Hant", "zh_Hant_HK", "zh_Hant_TW",
)


def fail(message: str) -> "NoReturn":
    raise SystemExit(f"ICU configuration error: {message}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, help="generated icu_config.gypi")
    args = parser.parse_args()
    if not args.path.is_file():
        fail(f"missing generated configuration: {args.path}")
    text = args.path.read_text(encoding="utf-8")
    if not re.search(r"['\"]icu_small['\"]\s*:\s*['\"]true['\"]", text):
        fail("icu_small is not enabled; the build is not small-icu")
    match = re.search(r"['\"]icu_locales['\"]\s*:\s*['\"]([^'\"]*)['\"]", text)
    if not match:
        fail("icu_locales is missing")
    actual = tuple(match.group(1).split(","))
    if actual != tuple(sorted(LOCALES)):
        fail(f"icu_locales does not match {PROFILE_NAME}: {actual}")
    print(f"ICU configuration passed: {PROFILE_NAME}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
