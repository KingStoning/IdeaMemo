$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    python -m PyInstaller --noconfirm --clean --onefile --windowed --name IdeaMemo --distpath dist --workpath build main.py
    if ($LASTEXITCODE -ne 0) { throw 'Windows build failed' }
    Write-Host "Built: $PSScriptRoot\dist\IdeaMemo.exe"
} finally {
    Pop-Location
}
