#!/usr/bin/env python3
"""Validate staged dependency artifacts before upload or release packaging.

The checks deliberately validate the release tree shape used by the public
GodotJS-Dependencies bundles and the libnode package, rather than comparing
build-specific hashes.  Binary hashes are checked after the final zip is made
by build_all.yml; source builds naturally produce different hashes per run.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


LWS_PLATFORMS = {
    "linux": {"x86_64", "arm64"},
    "macos": {"x86_64", "arm64"},
    "windows": {"x86_64", "arm64"},
    "android": {"arm64", "arm32", "x86_64"},
    "ios": {"arm64"},
}
V8_PLATFORMS = LWS_PLATFORMS
NODE_PLATFORMS = {
    "linux": {"x86_64"},
    "macos": {"arm64"},
    "windows": {"x86_64"},
    "android": {"arm64"},
    "ios": {"arm64"},
    "ohos": {"arm64"},
}


def fail(message: str) -> "NoReturn":
    print(f"artifact validation error: {message}", file=sys.stderr)
    raise SystemExit(1)


def regular_nonempty(path: Path, label: str) -> None:
    if not path.is_file() or path.is_symlink():
        fail(f"missing or non-regular {label}: {path}")
    if path.stat().st_size == 0:
        fail(f"empty {label}: {path}")


def require_files(root: Path, relative_paths: list[str]) -> None:
    for relative in relative_paths:
        regular_nonempty(root / relative, relative)


def child_names(root: Path) -> set[str]:
    if not root.is_dir():
        fail(f"missing artifact directory: {root}")
    return {child.name for child in root.iterdir()}


def reject_unexpected(root: Path, expected: set[str], label: str) -> None:
    unexpected = sorted(child_names(root) - expected)
    if unexpected:
        fail(f"unexpected {label} in {root}: {', '.join(unexpected)}")


def count_regular_files(root: Path) -> int:
    return sum(1 for path in root.rglob("*") if path.is_file() and not path.is_symlink())


def lws_dir(platform: str, arch: str) -> str:
    return f"{platform}_{arch}_release"


def v8_dir(platform: str, arch: str) -> str:
    if platform == "windows":
        return f"windows_{arch}_release"
    return f"{platform}.{arch}.release"


def node_dir(platform: str, arch: str) -> str:
    return f"{platform}/{'x64' if platform == 'windows' else arch}"


def parse_platforms(component: str, value: str, root: Path | None = None, infer: bool = False) -> list[tuple[str, str]]:
    supported = {
        "lws": LWS_PLATFORMS,
        "v8": V8_PLATFORMS,
        "node": NODE_PLATFORMS,
    }[component]
    value = value.strip()
    if infer:
        if root is None:
            fail("--infer requires --root")
        selected = []
        for platform, arches in supported.items():
            for arch in sorted(arches):
                directory = {
                    "lws": lws_dir(platform, arch),
                    "v8": v8_dir(platform, arch),
                    "node": node_dir(platform, arch),
                }[component]
                if (root / directory).is_dir():
                    selected.append((platform, arch))
        if not selected:
            fail(f"could not infer any {component} platform directories from {root}")
        return selected
    if not value or value == "all":
        return [(platform, arch) for platform, arches in supported.items() for arch in sorted(arches)]

    wanted: set[str] = set()
    wanted_arch: dict[str, set[str]] = {}
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        if "-" in item:
            platform, arch = item.split("-", 1)
            # The V8 matrix uses GN's x64 spelling for Windows, while the
            # published directory uses the portable x86_64 spelling.
            if component == "v8" and platform == "windows" and arch == "x64":
                arch = "x86_64"
            wanted.add(platform)
            wanted_arch.setdefault(platform, set()).add(arch)
        else:
            wanted.add(item)

    unknown = sorted(wanted - set(supported))
    if unknown:
        fail(f"unsupported {component} platform(s): {', '.join(unknown)}")
    selected: list[tuple[str, str]] = []
    for platform in supported:
        if platform not in wanted:
            continue
        arches = wanted_arch.get(platform, supported[platform])
        unknown_arches = sorted(arches - supported[platform])
        if unknown_arches:
            fail(f"unsupported {component} arch for {platform}: {', '.join(unknown_arches)}")
        selected.extend((platform, arch) for arch in sorted(arches))
    return selected


def validate_lws_platform(root: Path, platform: str, arch: str) -> None:
    library = "websockets_static.lib" if platform == "windows" else "libwebsockets.a"
    reject_unexpected(root, {"include", library}, "LWS platform entries")
    require_files(root, [
        "include/lws_config.h",
        "include/libwebsockets.h",
        "include/libwebsockets/lws-callbacks.h",
        library,
    ])
    include_files = count_regular_files(root / "include")
    # v4.3 release bundles contain the complete generated public include tree,
    # not only the two headers above. This catches partial/corrupt copies while
    # allowing harmless upstream header additions.
    if include_files < 80:
        fail(f"incomplete LWS include tree for {platform}/{arch}: {include_files} files")


def validate_lws(root: Path, selected: list[tuple[str, str]], full: bool) -> None:
    if full:
        expected = {lws_dir(platform, arch) for platform, arch in selected}
        reject_unexpected(root, expected, "LWS platform directories")
        for platform, arch in selected:
            validate_lws_platform(root / lws_dir(platform, arch), platform, arch)
    else:
        if len(selected) != 1:
            fail("component validation requires exactly one --platform/--arch pair")
        platform, arch = selected[0]
        expected = {lws_dir(platform, arch)}
        reject_unexpected(root, expected, "LWS staging entries")
        validate_lws_platform(root / lws_dir(platform, arch), platform, arch)


def validate_v8_platform(root: Path, platform: str, arch: str) -> None:
    library = "v8_monolith.lib" if platform == "windows" else "libv8_monolith.a"
    reject_unexpected(root, {"DEPS", library}, "V8 platform entries")
    require_files(root, ["DEPS", library])


def validate_v8(root: Path, selected: list[tuple[str, str]], full: bool) -> None:
    if full:
        expected = {v8_dir(platform, arch) for platform, arch in selected}
        reject_unexpected(root, expected | {"include"}, "V8 entries")
        include = root / "include"
        require_files(include, [
            "v8.h",
            "v8config.h",
            "libplatform/libplatform.h",
            "cppgc/heap.h",
        ])
        if count_regular_files(include) < 100:
            fail(f"incomplete V8 include tree: {count_regular_files(include)} files")
        for platform, arch in selected:
            validate_v8_platform(root / v8_dir(platform, arch), platform, arch)
    else:
        if len(selected) != 1:
            fail("component validation requires exactly one --platform/--arch pair")
        platform, arch = selected[0]
        expected = {v8_dir(platform, arch), "include"}
        reject_unexpected(root, expected, "V8 staging entries")
        require_files(root / "include", ["v8.h", "v8config.h"])
        validate_v8_platform(root / v8_dir(platform, arch), platform, arch)


def validate_node_platform(root: Path, platform: str, arch: str, require_commit: bool = True) -> None:
    library = "libnode.lib" if platform == "windows" else "libnode.a"
    expected = {library}
    if require_commit:
        expected.add("node-commit.txt")
    if platform == "windows":
        expected |= {"libnode.props", "libnode.cmake"}
    reject_unexpected(root, expected, "Node platform entries")
    require_files(root, [library])
    if require_commit:
        require_files(root, ["node-commit.txt"])
    if platform == "windows":
        require_files(root, ["libnode.props", "libnode.cmake"])


def validate_node(root: Path, selected: list[tuple[str, str]], full: bool, release: bool = False) -> None:
    if full:
        expected = {node_dir(platform, arch).split("/", 1)[0] for platform, _ in selected}
        # The root contains one directory per platform, shared headers, and the
        # two metadata files published by moluopro/libnode.
        reject_unexpected(
            root,
            expected | {"include", "BUILD-METADATA.json", "ICU-PROFILE.json"},
            "Node entries",
        )
        require_files(root / "include", ["node_api.h", "config.gypi"])
        require_files(root, ["BUILD-METADATA.json", "ICU-PROFILE.json"])
        commits: set[str] = set()
        for platform, arch in selected:
            platform_root = root / node_dir(platform, arch)
            validate_node_platform(platform_root, platform, arch, require_commit=not release)
            marker = platform_root / "node-commit.txt"
            if marker.exists():
                commit = marker.read_text(encoding="utf-8").strip()
                if not commit:
                    fail(f"empty Node commit marker: {marker}")
                commits.add(commit)
        if not release and len(commits) != 1:
            fail(f"Node platform artifacts were built from different commits: {sorted(commits)}")

        try:
            metadata = json.loads((root / "BUILD-METADATA.json").read_text(encoding="utf-8"))
            profile = json.loads((root / "ICU-PROFILE.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            fail(f"invalid Node metadata JSON: {exc}")
        required_metadata = {
            "source_repository", "source_ref", "source_commit", "node_ref",
            "node_commit", "build_type", "debug_info", "icu_profile",
            "platforms", "debug_source", "workflow_repository", "workflow_commit",
        }
        if not required_metadata <= metadata.keys():
            fail(f"BUILD-METADATA.json is missing fields: {sorted(required_metadata - metadata.keys())}")
        if commits and (metadata["node_commit"] != next(iter(commits)) or metadata["source_commit"] != next(iter(commits))):
            fail("BUILD-METADATA.json commit does not match platform markers")
        if metadata["icu_profile"] != "selected-locales-full-break-v1":
            fail("Node metadata has an unexpected ICU profile")
        expected_profile = {
            "copyright": "Copyright (c) 2014 IBM Corporation and Others. All Rights Reserved.",
            "comment": "LibNode ICU profile: trim locale presentation data while preserving shared capability data.",
            "variables": {"locales": {"only": [
                "root", "en", "en_GB", "en_US", "es", "es_ES", "es_MX", "fr", "fr_CA",
                "fr_FR", "ru", "ru_RU", "zh", "zh_Hans", "zh_Hans_CN", "zh_Hans_HK",
                "zh_Hant", "zh_Hant_HK", "zh_Hant_TW",
            ]}},
            "trees": {
                "ROOT": "locales", "coll": "locales", "curr": "locales", "lang": "locales",
                "rbnf": "locales", "region": "locales", "unit": "locales", "zone": "locales",
            },
            "remove": [],
            "keep": ["pool.res", "supplementalData.res", "zoneinfo64.res", "likelySubtags.res"],
        }
        if profile != expected_profile:
            fail("ICU-PROFILE.json does not exactly match selected-locales-full-break-v1")
        expected_platforms = sorted({platform for platform, _ in selected})
        if metadata["platforms"] != expected_platforms:
            fail(f"BUILD-METADATA.json platform list does not match the merged package: {metadata['platforms']}")
    else:
        if len(selected) != 1:
            fail("component validation requires exactly one --platform/--arch pair")
        platform, arch = selected[0]
        expected = {platform, "include"}
        reject_unexpected(root, expected, "Node staging entries")
        require_files(root / "include", ["node_api.h", "config.gypi"])
        validate_node_platform(root / node_dir(platform, arch), platform, arch, require_commit=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("component", choices=("lws", "v8", "node"))
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--platform", help="single platform for a matrix job")
    parser.add_argument("--arch", help="single architecture for a matrix job")
    parser.add_argument("--platforms", default="", help="comma-separated platforms or platform-arch pairs")
    parser.add_argument("--full", action="store_true", help="validate the complete merged component tree")
    parser.add_argument("--infer", action="store_true", help="infer the selected platform set from directories under --root")
    parser.add_argument("--release", action="store_true", help="validate a final Node release tree without CI-only commit marker files")
    args = parser.parse_args()

    if args.full:
        selected = parse_platforms(args.component, args.platforms, args.root, args.infer)
    else:
        if not args.platform or not args.arch:
            parser.error("--platform and --arch are required unless --full is used")
        selected = parse_platforms(args.component, f"{args.platform}-{args.arch}")

    if args.component == "lws":
        validate_lws(args.root, selected, args.full)
    elif args.component == "v8":
        validate_v8(args.root, selected, args.full)
    else:
        validate_node(args.root, selected, args.full, args.release)
    print(f"artifact validation passed: {args.component} ({len(selected)} platform/arch target(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
