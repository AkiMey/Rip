$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$System = Join-Path $Root "sistem"
$Py = Join-Path $System ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) {
    & (Join-Path $PSScriptRoot "INSTALL.ps1")
    exit $LASTEXITCODE
}
Write-Host "RIP V6.2.1 paketleri ve otomatik baslangic onariliyor..." -ForegroundColor Cyan
& $Py -m pip install --upgrade -r (Join-Path $System "requirements.txt")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $Py -m compileall -q $System
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
try {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Root "scripts\AUTOSTART.ps1")
} catch {
    Write-Host "Otomatik baslangic onarimi atlandi: $($_.Exception.Message)" -ForegroundColor Yellow
}
Write-Host "[OK] Onarim tamamlandi." -ForegroundColor Green
