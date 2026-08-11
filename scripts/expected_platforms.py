#!/usr/bin/env python3
"""Print the canonical platform/arch pairs expected for a CI component."""
from __future__ import annotations

import argparse


MATRICES = {
    "lws": {
        "linux": ("x86_64", "arm64"),
        "macos": ("arm64", "x86_64"),
        "windows": ("x86_64", "arm64"),
        "android": ("arm64", "arm32", "x86_64"),
        "ios": ("arm64",),
    },
    "v8": {
        "linux": ("x86_64", "arm64"),
        "macos": ("arm64", "x86_64"),
        "windows": ("x86_64", "arm64"),
        "android": ("arm64", "arm32", "x86_64"),
        "ios": ("arm64",),
    },
    "node": {
        "linux": ("x86_64",),
        "macos": ("arm64",),
        "windows": ("x86_64",),
        "android": ("arm64",),
        "ios": ("arm64",),
        "ohos": ("arm64",),
    },
}


def fail(message: str) -> "NoReturn":
    raise SystemExit(f"expected platform error: {message}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("component", choices=sorted(MATRICES))
    parser.add_argument("--platforms", default="")
    parser.add_argument("--skip-apple", action="store_true")
    parser.add_argument("--skip-windows", action="store_true")
    parser.add_argument("--ohos-available", action="store_true")
    args = parser.parse_args()

    matrix = MATRICES[args.component]
    requested = args.platforms.strip()
    selected: dict[str, set[str]] | None = None
    if requested and requested != "all":
        selected = {}
        for item in requested.split(","):
            item = item.strip()
            if not item:
                continue
            if "-" in item:
                platform, arch = item.split("-", 1)
                if platform == "windows" and arch == "x64":
                    arch = "x86_64"
                if platform not in matrix:
                    fail(f"{args.component} does not support platform {platform}")
                if arch not in matrix[platform]:
                    fail(f"{args.component} does not support architecture {arch} on {platform}")
                selected.setdefault(platform, set()).add(arch)
            else:
                if item not in matrix:
                    fail(f"{args.component} does not support platform {item}")
                selected.setdefault(item, set())

    pairs: list[str] = []
    for platform, arches in matrix.items():
        explicitly_requested = selected is not None and platform in selected
        if platform == "ohos" and not args.ohos_available:
            if explicitly_requested:
                fail("OHOS was explicitly requested but no OHOS SDK is configured")
            continue
        if args.skip_apple and platform in ("macos", "ios"):
            if explicitly_requested:
                fail(f"{platform} was explicitly requested but SKIP_APPLE is enabled")
            continue
        if args.skip_windows and platform == "windows":
            if explicitly_requested:
                fail("windows was explicitly requested but SKIP_WINDOWS is enabled")
            continue
        if selected is not None and not explicitly_requested:
            continue
        wanted = selected.get(platform, set()) if selected is not None else set()
        for arch in arches:
            if wanted and arch not in wanted:
                continue
            pairs.append(f"{platform}-{arch}")

    if not pairs:
        raise SystemExit(f"no expected {args.component} platform targets")
    print(",".join(pairs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
