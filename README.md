# GodotJS-Dependencies
Workflows to build all dependencies of GodotJS (v8 and libwebsockets).


- https://github.com/actions/runner-images
- https://github.com/actions/checkout
- https://github.com/actions/create-release


## Windows Toolset

Different Visual C++ Compilers are involved in different prebuilt GodotJS dependency releases:

| Package | Workflow Runners | Libraries | Toolset | Platforms/Architectures |
|---|---|---|---|---|
|[v8_r7](https://github.com/ialex32x/GodotJS-Dependencies/releases/download/v8_r7/v8_r7.zip)| windows-latest(2022)<br/>ubuntu-latest(22.04) |v8(12.4.254.20)| MSVC v143(14.40.33807)| Windows(x86_64)<br/>Linux(x86_64)<br/>MacOS(ARM64) |
|[v8_r9](https://github.com/ialex32x/GodotJS-Dependencies/releases/download/v8_r9/v8_r9.zip)| windows-latest(2022)<br/>ubuntu-latest(22.04) |v8(12.4.254.20)| VS2022(17.11.3)<br/> MSVC v143(14.41.34120)| Windows(x86_64)<br/>Linux(x86_64)<br/>MacOS(ARM64)<br/>Android(ARM64-v8a)<br/>iOS(ARM64) |
|[v8_r10](https://github.com/ialex32x/GodotJS-Dependencies/releases/download/v8_r10/v8_r10.zip)| windows-2019<br/>ubuntu-22.04 |v8(12.4.254.21)| MSVC v142(14.29.30133) | Windows(x86_64)<br/>Linux(x86_64)<br/>MacOS(ARM64)<br/>Android(ARM64-v8a)<br/>iOS(ARM64) |

> [!NOTE]
> Install specific VS 2022 MSVC toolset for different pacakge you used to avoid linkage errors.

-----

Why **windows_x86_64_release** does not follow the same naming rules?
> godot 4.3.1 generates `ActiveProjectItemList_` variable name with the path name (see methods.py)
> it'll fail if the path contains '.' or any other characters invalid as variable name
> detect and rename them for better compatibility


# Local Builds (with act)

This repository supports running our GitHub Actions locally with [act](https://github.com/nektos/act).

## Workflow layout

All build logic lives in reusable [composite actions](.github/actions/) and is driven by four entry workflows:

| Workflow | What it builds | Inputs |
|---|---|---|
| `build_v8.yml` | V8 monolith (11 platform/arch combos) | `v8_version`, `platforms` |
| `build_lws.yml` | libwebsockets static lib (10 combos) | `platforms` |
| `build_node.yml` | Node.js embeddable static lib (6 combos) | `node_version`, `platforms` |
| `build_all.yml` | All three + publish a Release with v8/lws/node bundles | `bundle_tag`, `v8_version`, `node_version` |

The `platforms` input is a comma-separated list (e.g. `linux,macos`); leave it empty to build **all** combos for that library.

## Local build examples

Important:
- Always pass: `--var HOST_MACOS=true` when running with act. It is required for macOS/iOS jobs and harmless for others.
- On Apple Silicon, add `--container-architecture linux/amd64` for Linux/Android jobs.
- Map macOS runners to your host: `-P macos-latest=-self-hosted`.
- Use `workflow_dispatch` on each entry workflow directly (do not use `build_all.yml` for local runs, as its publish job needs GitHub Releases).

Common arguments you can reuse:
- Docker images for Ubuntu:
  `-P ubuntu-latest=catthehacker/ubuntu:act-22.04 -P ubuntu-22.04=catthehacker/ubuntu:act-22.04`
- Artifact server path (optional): `--artifact-server-path ./artifacts`

### macOS host (run on your Mac)

- LWS macOS arm64
```sh
act workflow_dispatch \r
  -W .github/workflows/build_lws.yml \r
  -P macos-latest=-self-hosted \r
  --var HOST_MACOS=true \r
  --input platforms=macos \r
  -j build
```

- V8 macOS arm64 + iOS device (arm64)
```sh
act workflow_dispatch \r
  -W .github/workflows/build_v8.yml \r
  -P macos-latest=-self-hosted \r
  --var HOST_MACOS=true \r
  --input v8_version=13.5.119 \r
  --input platforms=macos,ios \r
  -j build
```

- Node macOS arm64
```sh
act workflow_dispatch \r
  -W .github/workflows/build_node.yml \r
  -P macos-latest=-self-hosted \r
  --var HOST_MACOS=true \r
  --input node_version=v24.x \r
  --input platforms=macos \r
  -j build
```

### Linux / Android (run in Docker)

Add these flags on Apple Silicon: `--container-architecture linux/amd64`.

- LWS Linux x86_64
```sh
act workflow_dispatch \r
  -W .github/workflows/build_lws.yml \r
  --artifact-server-path ./artifacts \r
  -P ubuntu-latest=catthehacker/ubuntu:act-22.04 \r
  -P ubuntu-22.04=catthehacker/ubuntu:act-22.04 \r
  --var HOST_MACOS=true \r
  --input platforms=linux \r
  -j build
```

- V8 Android arm64
```sh
act workflow_dispatch \r
  -W .github/workflows/build_v8.yml \r
  --artifact-server-path ./artifacts \r
  -P ubuntu-latest=catthehacker/ubuntu:act-22.04 \r
  -P ubuntu-22.04=catthehacker/ubuntu:act-22.04 \r
  --var HOST_MACOS=true \r
  --input v8_version=13.5.119 \r
  --input platforms=android \r
  -j build
```

- Node Linux x86_64 + Android arm64
```sh
act workflow_dispatch \r
  -W .github/workflows/build_node.yml \r
  --artifact-server-path ./artifacts \r
  -P ubuntu-latest=catthehacker/ubuntu:act-22.04 \r
  -P ubuntu-22.04=catthehacker/ubuntu:act-22.04 \r
  --var HOST_MACOS=true \r
  --input node_version=v24.x \r
  --input platforms=linux,android \r
  -j build
```
