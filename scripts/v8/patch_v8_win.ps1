# Patches applied to the v8 checkout before generating the windows build.
#
# These used to be inline `powershell -Command` one-liners in the build action.
# They are extracted here for two reasons:
#   1. They must be IDEMPOTENT. The workflow caches the whole v8/ tree, so a
#      re-run restores an already-patched tree; the old one-liners asserted the
#      *unpatched* text and aborted, which broke every cached run.
#   2. A script can be tested locally against a synthetic tree, which the inline
#      form could not (that cost several 40-minute CI rounds).
#
# Each patch asserts a state - "already patched" / "patch it" / "layout changed,
# abort" - rather than asserting the pre-patch text.
#
# Usage: patch_v8.ps1 -V8Root <path to v8/v8> [-NinjaReroute [-Target <out dir name>]]
#
# Called twice, because the two kinds of patch must straddle `gn gen`:
#   1. before gn gen: the source patches (BUILD.gn, snapshot.cc) feed generation
#   2. after  gn gen: the ninja launcher reroute needs the generated out dir
# The switch keeps each call explicit instead of guessing from the filesystem.
param(
  [Parameter(Mandatory = $true)][string]$V8Root,
  [string]$Target = "",
  [switch]$NinjaReroute
)
$ErrorActionPreference = "Stop"
$script:Failed = $false

function Fail([string]$message) {
  Write-Host "##[error]v8 patch error: $message"
  $script:Failed = $true
}

function Patch-File {
  # Patch-File <path> <marker that means "already patched"> <old text> <new text> <description>
  param([string]$Path, [string]$DoneMarker, [string]$Old, [string]$New, [string]$What)
  if (-not (Test-Path $Path)) { Fail "$What`: file not found: $Path"; return }
  $text = [IO.File]::ReadAllText($Path)
  if ($text.Contains($DoneMarker)) {
    Write-Host "$What`: already applied"
    return
  }
  if (-not $text.Contains($Old)) {
    Fail "$What`: neither the original nor the patched text is present in $Path - upstream layout changed"
    return
  }
  [IO.File]::WriteAllText($Path, $text.Replace($Old, $New))
  Write-Host "$What`: applied"
}

# --- 1. Enlarge the mksnapshot stack -------------------------------------------------
# V8's BUILD.gn sets /STACK:2097152 (2MB) for x64 targets and mksnapshot overflows
# it during Turbofan scheduling (0xC00000FD). Reserve 256MB instead.
Patch-File `
  -Path (Join-Path $V8Root "BUILD.gn") `
  -DoneMarker "/STACK:268435456" `
  -Old "/STACK:2097152" `
  -New "/STACK:268435456" `
  -What "mksnapshot stack reserve"

# --- 2. Skip ReadOnlyPromotion on windows --------------------------------------------
# cdb proved an infinite Promote->IterateBody->VisitObject recursion over a
# self-referential Code graph (5-frame repeating stack to overflow), independent of
# stack size. The RO-space seal that follows must still run or serialization asserts.
Patch-File `
  -Path (Join-Path $V8Root "src/snapshot/snapshot.cc") `
  -DoneMarker "recurses infinitely" `
  -Old "ReadOnlyPromotion::Promote(isolate_, safepoint_scope, no_gc_from_here_on);" `
  -New "// win-x64: ReadOnlyPromotion recurses infinitely on self-ref Code graphs; objects serialize from mutable heap." `
  -What "ReadOnlyPromotion call"

# --- 3. Route the mksnapshot action through the PE-stack wrapper ---------------------
# Swaps the exact launcher substring in every generated ninja file so mksnapshot runs
# through run_mksnapshot_win.py (which widens the PE stack and retries transient
# NTSTATUS failures). Also idempotent: a cached out dir is already rerouted.
if ($NinjaReroute) {
  if ($Target -eq "") { Fail "mksnapshot reroute: -Target is required with -NinjaReroute"; }
  else {
    $ninjaDir = Join-Path $V8Root ("out.gn/" + $Target)
    if (-not (Test-Path $ninjaDir)) {
      Fail "mksnapshot reroute: generated out dir not found: $ninjaDir"
    } else {
      $old = "../../tools/run.py ./mksnapshot "
      $new = "../../tools/run_mksnapshot_win.py ./mksnapshot "
      $patched = 0
      $already = 0
      Get-ChildItem $ninjaDir -Recurse -Filter "*.ninja" | ForEach-Object {
        $t = [IO.File]::ReadAllText($_.FullName)
        if ($t.Contains($old)) {
          [IO.File]::WriteAllText($_.FullName, $t.Replace($old, $new))
          $patched++
        } elseif ($t.Contains($new.Trim())) {
          $already++
        }
      }
      Write-Host "mksnapshot reroute: patched=$patched already=$already"
      if ($patched -lt 1 -and $already -lt 1) {
        Fail "mksnapshot reroute: no ninja file references the mksnapshot launch line"
      }
    }
  }
}

if ($script:Failed) { exit 1 }
Write-Host "v8 patches complete ($(if ($NinjaReroute) { 'ninja reroute' } else { 'source' }) phase)"
