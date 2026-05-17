# Build Inno Setup installer (requires ISCC.exe on PATH or via INNO_SETUP_DIR).
#
#   .\packaging\windows\build_installer.ps1
#   .\packaging\windows\build_installer.ps1 -Version 2.0.0

param(
    [string]$Version = ""
)

$ErrorActionPreference = "Stop"
$WindowsDir = $PSScriptRoot
$Root = (Resolve-Path (Join-Path $WindowsDir "..\..")).Path
$DistDir = Join-Path $Root "dist"
$PyOut = Join-Path $DistDir "NetNeighbor"

if (-not $Version) {
    $vf = Join-Path $Root "VERSION"
    if (Test-Path $vf) {
        $Version = (Get-Content $vf -TotalCount 1).Trim()
        if ($Version -match '#') { $Version = $Version.Split('#')[0].Trim() }
    }
}
if (-not $Version) { throw "Set VERSION file or pass -Version" }
if (-not (Test-Path $PyOut)) {
    throw "Missing $PyOut — run .\packaging\windows\build.ps1 first"
}

$iscc = $env:INNO_SETUP_ISCC
if (-not $iscc -and $env:INNO_SETUP_DIR) {
    $iscc = Join-Path $env:INNO_SETUP_DIR "ISCC.exe"
}
if (-not $iscc) {
    $iscc = "ISCC.exe"
}

$iss = Join-Path $WindowsDir "netneighbor.iss"
Write-Host "== Inno Setup =="
Write-Host "version=$Version iscc=$iscc"

& $iscc $iss `
    "/DMyAppVersion=$Version" `
    "/DPyInstallerDir=$PyOut" `
    "/DSourcePath=$WindowsDir"

$setup = Join-Path $DistDir "NetNeighbor-$Version-win64-setup.exe"
if (Test-Path $setup) {
    Write-Host "Built: $setup"
} else {
    Write-Warning "Expected installer not found at $setup (check ISCC output)"
}
