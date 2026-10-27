param(
    [string]$Python = ".venv\Scripts\python.exe"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $projectRoot
try {
    if (-not (Test-Path -LiteralPath $Python)) {
        throw "Python environment missing: $Python"
    }
    $templateDir = Join-Path $projectRoot "assets\tiles\classic"
    if (-not (Test-Path -LiteralPath (Join-Path $templateDir "manifest.json"))) {
        throw "Classic templates missing. Run scripts\extract_apk_tiles.py first."
    }
    & $Python -m PyInstaller --noconfirm --clean --onefile --windowed `
        --name SheepVisionSolver `
        --paths src `
        --add-data "${templateDir};assets\tiles\classic" `
        src\sheep_solver\gui\app.py
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE"
    }
    Write-Host "Built: $(Join-Path $projectRoot 'dist\SheepVisionSolver.exe')"
}
finally {
    Pop-Location
}
