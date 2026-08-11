#!/usr/bin/env python3
"""Check the generated Node ICU configuration matches libnode's ICU profile."""
from __future__ import annotations

import argparse
import ast
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
    parser.add_argument("path", type=Path, help="generated Node config.gypi")
    args = parser.parse_args()
    if not args.path.is_file():
        fail(f"missing generated configuration: {args.path}")
    text = args.path.read_text(encoding="utf-8")
    start = text.find("{")
    if start < 0:
        fail("generated configuration is not a dictionary")
    try:
        expression = ast.parse(text[start:], mode="eval")
        config = ast.literal_eval(expression.body)
    except (SyntaxError, ValueError) as exc:
        fail(f"cannot parse generated GYP configuration: {exc}")
    if not isinstance(config, dict):
        fail("generated configuration is not a dictionary")
    variables = config.get("variables")
    if not isinstance(variables, dict):
        fail("generated configuration has no variables dictionary")
    if str(variables.get("icu_small", "")).lower() != "true":
        fail("icu_small is not enabled; the build is not small-icu")
    locales = variables.get("icu_locales")
    if not isinstance(locales, str):
        fail("icu_locales is missing")
    actual = tuple(item for item in locales.split(",") if item)
    # Node configure.py canonicalizes this set alphabetically and always adds
    # root, so validate exact membership rather than caller argument order.
    if set(actual) != set(LOCALES) or len(actual) != len(LOCALES):
        fail(f"icu_locales does not match {PROFILE_NAME}: {actual}")
    print(f"ICU configuration passed: {PROFILE_NAME}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
