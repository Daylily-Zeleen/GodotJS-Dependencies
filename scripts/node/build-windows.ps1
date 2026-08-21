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
$IcuProfile = Join-Path $Workspace "Scripts/scripts/node/icu-selected-locales.json"
if (-not (Test-Path $IcuProfile)) { throw "Missing canonical ICU profile: $IcuProfile" }
$IcuTarget = Join-Path (Get-Location) "tools/icu/icu_small.json"
Copy-Item -Force $IcuProfile $IcuTarget
if (-not ((Get-FileHash $IcuProfile).Hash -eq (Get-FileHash $IcuTarget).Hash)) {
  throw "Canonical ICU profile was not copied exactly to $IcuTarget"
}
$IcuGyp = Join-Path (Get-Location) "tools/icu/icu-generic.gyp"
if (-not (Test-Path $IcuGyp)) { throw "Missing ICU gyp definition: $IcuGyp" }
$gypText = Get-Content -Raw $IcuGyp
$deleteTmpMatches = [regex]::Matches($gypText, "'--delete-tmp',")
if ($deleteTmpMatches.Count -ne 2) { throw "Expected exactly two ICU --delete-tmp actions, found $($deleteTmpMatches.Count)" }
$gypText = $gypText -replace "'--delete-tmp',", " "
# Node >= 24 forces the ClangCL toolchain (vcbuild.bat), and an ICU genccode
# built by clang refuses to emit a Windows .obj without an explicit CPU
# architecture (-c). Wire '-c <(target_arch)' into every Windows genccode
# action that lacks it (upstream only set it on one of the three actions).
# ICU < 77 does not know this option, so gate on the node version.
if ($Branch -notmatch '^v(\d+)') { throw "Cannot parse Node major version from branch '$Branch'" }
$NodeMajor = [int]$Matches[1]
if ($NodeMajor -ge 24) {
  $gypLines = $gypText -split "`n", -1
  $patchedLines = New-Object System.Collections.Generic.List[string]
  $inserted = 0
  for ($idx = 0; $idx -lt $gypLines.Count; $idx++) {
    $line = $gypLines[$idx]
    $patchedLines.Add($line)
    if ($line -match "^([ \t]*)'<@\(icu_asm_opts\)', # -o\s*$") {
      $indent = $Matches[1]
      $next = if ($idx + 1 -lt $gypLines.Count) { $gypLines[$idx + 1] } else { "" }
      if ($next -notmatch "^[ \t]*'-c',") {
        $patchedLines.Add("$indent'-c', '<(target_arch)',")
        $inserted++
      }
    }
  }
  if ($inserted -ne 2) { throw "Expected to add genccode -c to exactly two Windows ICU actions, found $inserted" }
  $gypText = $patchedLines -join "`n"
}
[IO.File]::WriteAllText($IcuGyp, $gypText, [Text.UTF8Encoding]::new($false))
if ((Get-Content -Raw $IcuGyp) -match '--delete-tmp') { throw "Failed to disable ICU temporary-data deletion" }
if ($NodeMajor -ge 24) {
  $cpuArchEntries = [regex]::Matches((Get-Content -Raw $IcuGyp), [regex]::Escape("'-c', '<(target_arch)',")).Count
  if ($cpuArchEntries -lt 3) { throw "genccode -c patch incomplete: found $cpuArchEntries entries, expected at least 3" }
}
$IcuTrim = Join-Path (Get-Location) "tools/icu/icutrim.py"
if (-not (Test-Path $IcuTrim)) { throw "Missing ICU trim tool: $IcuTrim" }
$trimText = Get-Content -Raw $IcuTrim
# Normalize line endings before the exact source patch so Windows CRLF and
# upstream LF files are handled identically; write the patched Python as LF.
$trimText = $trimText -replace "`r`n", "`n"
$oldTrimGuard = @'
if not (os.path.isdir(options.tmpdir)):
    os.mkdir(options.tmpdir)
else:
    print("Please delete tmpdir %s before beginning." % options.tmpdir)
    sys.exit(1)
'@
$oldTrimGuard = $oldTrimGuard -replace "`r`n", "`n"
$newTrimGuard = @'
if os.path.isdir(options.tmpdir):
    if os.listdir(options.tmpdir):
        print("Please delete tmpdir %s before beginning." % options.tmpdir)
        sys.exit(1)
else:
    os.mkdir(options.tmpdir)
'@
$newTrimGuard = $newTrimGuard -replace "`r`n", "`n"
if (($trimText -split [regex]::Escape($oldTrimGuard)).Count -ne 2) { throw "Expected exactly one ICU tmpdir guard" }
$trimText = $trimText.Replace($oldTrimGuard, $newTrimGuard)
[IO.File]::WriteAllText($IcuTrim, $trimText, [Text.UTF8Encoding]::new($false))
$patchedTrimText = Get-Content -Raw $IcuTrim
if (($patchedTrimText -split [regex]::Escape($oldTrimGuard)).Count -ne 1) { throw "Old ICU tmpdir guard remains after patch" }
if ($patchedTrimText -notmatch 'if os\.listdir\(options\.tmpdir\):') { throw "Failed to patch empty ICU tmpdir handling" }
python "$Workspace\Scripts\scripts\node\patch_rtti.py" (Get-Location) windows

# --- Configure & build with MSVC ---
# Node ships vcbuild.bat which wraps configure + msbuild for the VS toolchain.
# vcbuild.bat expects 'x64' (not 'x86_64'), so map the matrix arch first.
$VcCpu = if ($DestCpu -eq "x86_64") { "x64" } else { $DestCpu }

# Replicate moluopro/libnode's selected-locales-full-break-v1 ICU build. vcbuild
# maps small-icu to --with-intl=small-icu and forwards config_flags to configure.
$env:config_flags = "--with-icu-locales=root,en,en_GB,en_US,es,es_ES,es_MX,fr,fr_CA,fr_FR,ru,ru_RU,zh,zh_Hans,zh_Hans_CN,zh_Hans_HK,zh_Hant,zh_Hant_HK,zh_Hant_TW"
Remove-Item -Force -ErrorAction SilentlyContinue .gyp_configure_stamp, .tmp_gyp_configure_stamp, node.sln


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
  & ".\vcbuild.bat" $VcCpu release small-icu openssl-no-asm
} else {
  & ".\vcbuild.bat" $VcCpu release small-icu
}
if ($LASTEXITCODE -ne 0) { throw "vcbuild.bat failed with exit code $LASTEXITCODE" }
python "$Workspace\Scripts\scripts\node\verify_icu_config.py" "config.gypi"
python "$Workspace\Scripts\scripts\node\verify_icu_data.py" "out"

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
# Publish the same Windows integration templates as moluopro/libnode. They
# are part of the platform package, not source-only build helpers.
$TemplateRoot = Join-Path $PSScriptRoot ""
Copy-Item -Force (Join-Path $TemplateRoot "libnode.props") $LibDir
Copy-Item -Force (Join-Path $TemplateRoot "libnode.cmake") $LibDir

Write-Host "== staging layout =="
Get-ChildItem -Path (Join-Path $Workspace "staging") -Recurse -File | Select-Object -ExpandProperty FullName
