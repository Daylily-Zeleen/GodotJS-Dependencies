# Build node (Node.js as an embeddable static library) for Windows x86_64.
# Uses the MSVC toolchain (Visual Studio) that comes with windows-2022 runners.
# Usage: build-windows.ps1 [-Branch <node_branch>] [-DestCpu <cpu>]
param(
  [string]$Branch = "v24.x",
  [string]$DestCpu = "x64"
)
$ErrorActionPreference = "Stop"
$Workspace = if ($env:GITHUB_WORKSPACE) { $env:GITHUB_WORKSPACE } else { (Get-Location).Path }

# --- Fetch Node.js source (skip if already fetched by the fetch action) ---
Set-Location $Workspace
if (-not (Test-Path "node/.git")) {
  git clone --depth 1 --branch $Branch https://github.com/nodejs/node.git
}
Set-Location node

# --- Configure & build with MSVC ---
# Node ships vcbuild.bat which wraps configure + msbuild for the VS toolchain.
# vcbuild.bat expects 'x64' (not 'x86_64'), so map the matrix arch first.
$VcCpu = if ($DestCpu -eq "x86_64") { "x64" } else { $DestCpu }
& ".\vcbuild.bat" $VcCpu release
if ($LASTEXITCODE -ne 0) { throw "vcbuild.bat failed with exit code $LASTEXITCODE" }

# --- Locate static library (path varies across versions) ---
$Lib = Get-ChildItem -Path "out" -Recurse -Filter "libnode*.lib" | Select-Object -First 1
if (-not $Lib) {
  $Lib = Get-ChildItem -Path "out" -Recurse -Filter "*.lib" | Where-Object { $_.FullName -match "Release" } | Select-Object -First 1
}
if (-not $Lib) {
  Write-Error "libnode*.lib not found in build output"
  Get-ChildItem -Path "out" -Recurse -Filter "*.lib" | Select-Object -First 20
  throw "libnode*.lib not found"
}
Write-Host "Found: $($Lib.FullName)"

# --- Stage artifacts ---
# Follow repo convention: x64 -> x86_64
$PlatformName = if ($VcCpu -eq "x64") { "x86_64" } else { $VcCpu }
$StageDir = Join-Path $Workspace "staging/node/windows_${PlatformName}_release"
New-Item -ItemType Directory -Force -Path $StageDir | Out-Null
# node v24 has no top-level include/; generate headers via tools/install.py
python tools/install.py install --headers-only --dest-dir $StageDir --prefix "/"
if (Test-Path "out/Release/config.gypi") {
  Copy-Item -Force "out/Release/config.gypi" $StageDir
}
Copy-Item -Force $Lib.FullName $StageDir

Write-Host "== staging layout =="
Get-ChildItem -Path (Join-Path $Workspace "staging") -Recurse -File | Select-Object -ExpandProperty FullName
