# Build NetNeighbor Windows folder distribution with PyInstaller (entry: main.py).
# Prerequisites: Python 3.10+, pip install -r requirements-qt.txt pyinstaller
#
#   .\packaging\windows\build.ps1
#   .\packaging\windows\build.ps1 -Version 2.0.0

param(
    [string]$Version = ""
)

$ErrorActionPreference = "Stop"
$WindowsDir = $PSScriptRoot
$Root = (Resolve-Path (Join-Path $WindowsDir "..\..")).Path
$DistDir    = Join-Path $Root "dist"
$BuildDir   = Join-Path $Root "build\pyinstaller-windows"
$PyDistDir  = Join-Path $Root "build\windows"

if (-not $Version) {
    $vf = Join-Path $Root "VERSION"
    if (Test-Path $vf) {
        $Version = (Get-Content $vf -TotalCount 1).Trim()
        if ($Version -match '#') { $Version = $Version.Split('#')[0].Trim() }
    }
}
if (-not $Version) {
    throw "Set VERSION file or pass -Version"
}

Write-Host "== NetNeighbor Windows PyInstaller =="
Write-Host "version=$Version root=$Root"

$py = $env:NETNEIGHBOR_PYTHON
if (-not $py) {
    $venvPy = Join-Path $Root ".venv\Scripts\python.exe"
    if (Test-Path $venvPy) { $py = $venvPy } else { $py = "python" }
}

Write-Host "== Update file headers =="
& $py (Join-Path $Root "tools\add_headers.py") --apply

& $py -m pip install -q -r (Join-Path $Root "requirements.txt") pyinstaller

New-Item -ItemType Directory -Force -Path $DistDir   | Out-Null
New-Item -ItemType Directory -Force -Path $PyDistDir | Out-Null
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $BuildDir

Push-Location $Root
try {
    & $py -m PyInstaller `
        --noconfirm `
        --distpath $PyDistDir `
        --workpath $BuildDir `
        (Join-Path $WindowsDir "netneighbor.spec")
}
finally {
    Pop-Location
}

$outFolder = Join-Path $PyDistDir "NetNeighbor"
if (-not (Test-Path $outFolder)) {
    throw "PyInstaller output not found: $outFolder"
}

# Trim netneighbor icon set to only the resolutions needed by the Qt UI (saves ~143 MB).
# Source tree keeps all resolutions for archival; packages ship only 16/32/48/96/256.
$bfd = Join-Path $outFolder "_internal\assets\icons\netneighbor"
if (Test-Path $bfd) {
    $keep = @("16", "32", "48", "96", "256")
    Get-ChildItem $bfd -Directory | Where-Object { $_.Name -notin $keep } | Remove-Item -Recurse -Force
    Write-Host "Trimmed netneighbor icons to: $($keep -join ', ')"
}

$zipName = "NetNeighbor-$Version-win64.zip"
$zipPath = Join-Path $DistDir $zipName
if (Test-Path $zipPath) { Remove-Item -Force $zipPath }
Compress-Archive -Path $outFolder -DestinationPath $zipPath

Write-Host "Built:"
Write-Host "  $outFolder"
Write-Host "  $zipPath"
Write-Host "Next: .\packaging\windows\build_installer.ps1 -Version $Version"
