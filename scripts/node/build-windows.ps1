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

# node builds OpenSSL with assembly which requires NASM; install it if missing.
if (-not (Get-Command nasm -ErrorAction SilentlyContinue)) {
  Write-Host "NASM not found, installing via choco..."
  choco install nasm -y --no-progress
  if ($LASTEXITCODE -ne 0) { Write-Warning "choco nasm failed ($LASTEXITCODE); falling back to openssl-no-asm" }
  # refresh PATH so nasm is visible to vcbuild.bat
  $env:Path = "C:\Program Files\NASM;" + $env:Path
}
if (-not (Get-Command nasm -ErrorAction SilentlyContinue)) {
  Write-Host "NASM still unavailable, building with openssl-no-asm"
  & ".\vcbuild.bat" $VcCpu release openssl-no-asm
} else {
  & ".\vcbuild.bat" $VcCpu release
}
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
# Layout mirrors moluopro/libnode releases: libnode/windows/x64/libnode.lib with
# one shared libnode/include/ (node header install flattened to include/).
$LibDir = Join-Path $Workspace "staging/libnode/windows/x64"
$Hdrs = Join-Path $Workspace "staging/libnode/include"
Remove-Item -Recurse -Force $Hdrs -ErrorAction SilentlyContinue
python tools/install.py install --headers-only --dest-dir $Hdrs --prefix "/"
# install.py lays headers out under include/node/; flatten to include/ so the
# package matches the upstream libnode release layout.
Get-ChildItem -Path (Join-Path $Hdrs "include\node") -Force | Move-Item -Destination $Hdrs
Remove-Item -Recurse -Force (Join-Path $Hdrs "include")
if (Test-Path "out/Release/config.gypi") {
  Copy-Item -Force "out/Release/config.gypi" $Hdrs
}
New-Item -ItemType Directory -Force -Path $LibDir | Out-Null
Copy-Item -Force $Lib.FullName (Join-Path $LibDir "libnode.lib")

Write-Host "== staging layout =="
Get-ChildItem -Path (Join-Path $Workspace "staging") -Recurse -File | Select-Object -ExpandProperty FullName
