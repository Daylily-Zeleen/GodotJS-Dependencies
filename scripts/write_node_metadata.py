#!/usr/bin/env python3
"""Write metadata files required by the published libnode package."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


ICU_PROFILE_NAME = "selected-locales-full-break-v1"
PROFILE_PATH = Path(__file__).parent / "node" / "icu-selected-locales.json"


def fail(message: str) -> "NoReturn":
    raise SystemExit(f"node metadata error: {message}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--node-ref", required=True)
    parser.add_argument("--workflow-repository", required=True)
    parser.add_argument("--workflow-commit", required=True)
    args = parser.parse_args()

    root = args.root
    if not root.is_dir():
        fail(f"missing package root: {root}")
    if not PROFILE_PATH.is_file():
        fail(f"missing canonical ICU profile: {PROFILE_PATH}")

    platforms: set[str] = set()
    commits: set[str] = set()
    for platform_dir in sorted(root.iterdir()):
        if not platform_dir.is_dir() or platform_dir.name == "include":
            continue
        for arch_dir in sorted(platform_dir.iterdir()):
            if not arch_dir.is_dir():
                fail(f"unexpected non-directory in {platform_dir}: {arch_dir.name}")
            marker = arch_dir / "node-commit.txt"
            if not marker.is_file():
                fail(f"missing Node commit marker: {marker}")
            commit = marker.read_text(encoding="utf-8").strip()
            if not commit:
                fail(f"empty Node commit marker: {marker}")
            commits.add(commit)
        platforms.add(platform_dir.name)

    if not platforms:
        fail("no platform directories found")
    if len(commits) != 1:
        fail(f"platforms were built from different Node commits: {sorted(commits)}")
    node_commit = next(iter(commits))

    metadata = {
        "source_repository": "https://github.com/nodejs/node",
        "source_ref": args.node_ref,
        "source_commit": node_commit,
        "node_ref": args.node_ref,
        "node_commit": node_commit,
        "build_type": "release",
        "debug_info": "stripped",
        "icu_profile": ICU_PROFILE_NAME,
        "platforms": sorted(platforms),
        "debug_source": None,
        "workflow_repository": args.workflow_repository,
        "workflow_commit": args.workflow_commit,
    }

    profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    (root / "ICU-PROFILE.json").write_text(
        json.dumps(profile, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (root / "BUILD-METADATA.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"wrote Node metadata for {len(platforms)} platform(s) using {ICU_PROFILE_NAME}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
