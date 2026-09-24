#!/usr/bin/env python3
"""Fail closed when node's OpenSSL detection silently degraded the build.

Why this exists
---------------
node's configure.py derives `openssl_version` by asking the C compiler to
preprocess `openssl/opensslv.h`. When that probe fails it only WARNS and
continues with version 0:

    WARNING: Failed to extract OpenSSL macros from headers
    WARNING: Failed to determine OpenSSL version from header: ...
    INFO: configure completed successfully

gyp then treats OpenSSL as older than 3.0.15 and, in deps/ncrypto/ncrypto.gyp,
skips the `ncrypto_engine` target entirely - compiling engine.cc into the plain
`ncrypto` target without NCRYPTO_ENGINE_COMPAT / OPENSSL_API_COMPAT=30000 /
OPENSSL_SUPPRESS_DEPRECATED. The build then fails on undeclared ENGINE_* symbols
*much later*, and a build that happened not to reference them would publish a
silently degraded libnode.

So a configure-time warning is treated here as a hard error: the expected
OpenSSL version is asserted against the generated configuration, and on mismatch
the header probe is re-run with its compiler stderr surfaced.
"""
from __future__ import annotations

import argparse
import ast
import shlex
import subprocess
import sys
from pathlib import Path

# OpenSSL 3.0.15 == 0x3000000f: the threshold deps/ncrypto/ncrypto.gyp uses to
# decide whether the engine/legacy backend split applies.
MIN_OPENSSL_VERSION = 0x3000000F
OPENSSL_VERSION_LABEL = "0x3000000f"


def fail(message: str) -> "NoReturn":
    raise SystemExit(f"OpenSSL configuration error: {message}")


def read_variables(config_path: Path) -> dict:
    if not config_path.is_file():
        fail(f"missing generated configuration: {config_path}")
    text = config_path.read_text(encoding="utf-8", errors="replace")
    start = text.find("{")
    if start < 0:
        fail("generated configuration is not a dictionary")
    try:
        config = ast.literal_eval(ast.parse(text[start:], mode="eval").body)
    except (SyntaxError, ValueError) as exc:
        fail(f"cannot parse generated GYP configuration: {exc}")
    variables = config.get("variables") if isinstance(config, dict) else None
    if not isinstance(variables, dict):
        fail("generated configuration has no variables dictionary")
    return variables


def rerun_probe(node_dir: Path, shared_openssl_includes: str) -> None:
    """Re-run node's own header probe so a failure explains itself."""
    import os

    cc = os.environ.get("CC", "cc")
    args = ["-E", "-dM",
            "-include", "openssl/opensslv.h",
            "-include", "openssl/crypto.h",
            "-"]
    if shared_openssl_includes:
        args = ["-I", shared_openssl_includes] + args
    else:
        args = ["-I", "deps/openssl/openssl/include"] + args
    print(f"  probe compiler: {cc}")
    try:
        proc = subprocess.Popen(
            shlex.split(cc) + args,
            cwd=str(node_dir),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as exc:
        print(f"  probe could not start: {exc}")
        return
    with proc:
        proc.stdin.write(b"\n")
        out, err = proc.communicate()
    macros = [ln for ln in out.decode("utf-8", "replace").split("\n")
              if ln.startswith("#define OPENSSL_")]
    print(f"  probe returncode: {proc.returncode}")
    print(f"  OPENSSL_* macros: {len(macros)}")
    if err:
        text = err.decode("utf-8", "replace").strip()
        for line in text.splitlines()[:20]:
            print(f"  probe stderr: {line}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path, help="generated Node config.gypi")
    parser.add_argument("--node-dir", type=Path, default=Path("."),
                        help="node source directory (for the probe re-run)")
    args = parser.parse_args()

    variables = read_variables(args.config)
    boringssl = str(variables.get("openssl_is_boringssl", "")).lower() == "true"
    version = variables.get("openssl_version")
    if version is None:
        fail(f"openssl_version is missing from {args.config}")
    try:
        version = int(version)
    except (TypeError, ValueError):
        fail(f"openssl_version is not an integer: {version!r}")

    if boringssl:
        print(f"OpenSSL configuration passed: BoringSSL (version {version:#x})")
        return 0

    if version < MIN_OPENSSL_VERSION:
        # node's probe failed (or detected a genuinely old OpenSSL). Either way
        # the resulting gyp spec drops the engine backend, so refuse to build.
        print(f"  openssl_version = {version:#010x} (need >= {OPENSSL_VERSION_LABEL})")
        shared_includes = str(variables.get("shared_openssl_includes", "") or "")
        rerun_probe(args.node_dir, shared_includes)
        fail(
            f"OpenSSL {version:#010x} is below {OPENSSL_VERSION_LABEL}; node's header "
            "probe most likely failed, which silently drops deps/ncrypto's engine "
            "backend. Scroll up for the probe's stderr."
        )

    print(f"OpenSSL configuration passed: version {version:#010x} >= {OPENSSL_VERSION_LABEL}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
