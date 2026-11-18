$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [Text.Encoding]::UTF8

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$System = Join-Path $Root "sistem"
$Venv = Join-Path $System ".venv"
$Py = Join-Path $Venv "Scripts\python.exe"

function Test-PythonCommand {
    param(
        [Parameter(Mandatory = $true)][string]$Exe,
        [string[]]$PrefixArgs = @()
    )
    try {
        & $Exe @PrefixArgs -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" *> $null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

function Find-Python {
    # IMPORTANT: return one object only. Never let probe-command stdout escape
    # from this function; otherwise PowerShell flattens it into the return value.
    $launchers = @()

    $cmd = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($cmd) { $launchers += $cmd.Source }

    $knownLauncher = Join-Path $env:LOCALAPPDATA "Programs\Python\Launcher\py.exe"
    if (Test-Path $knownLauncher) { $launchers += $knownLauncher }

    $launchers = @($launchers | Select-Object -Unique)
    foreach ($launcher in $launchers) {
        foreach ($selector in @("-3.12", "-3.11", "-3")) {
            if (Test-PythonCommand -Exe $launcher -PrefixArgs @($selector)) {
                return [pscustomobject]@{
                    Exe = $launcher
                    PrefixArgs = @($selector)
                }
            }
        }
    }

    $candidates = @()
    $pythonCmd = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($pythonCmd) { $candidates += $pythonCmd.Source }

    $candidates += @(
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\python.exe"),
        (Join-Path $env:ProgramFiles "Python312\python.exe"),
        (Join-Path $env:ProgramFiles "Python311\python.exe")
    )

    foreach ($candidate in @($candidates | Select-Object -Unique)) {
        if ((Test-Path $candidate) -and (Test-PythonCommand -Exe $candidate)) {
            return [pscustomobject]@{
                Exe = $candidate
                PrefixArgs = @()
            }
        }
    }

    return $null
}

Write-Host "RIPPERINIO V6.2.1 kurulumu" -ForegroundColor Cyan
$base = Find-Python

if (-not $base) {
    $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if (-not $winget) {
        throw "Python bulunamadi ve winget yok. Python 3.12 (64-bit) kurup tekrar calistir."
    }

    Write-Host "Python 3.12 kuruluyor..."
    & $winget.Source install --id Python.Python.3.12 -e --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) { throw "Python 3.12 kurulumu basarisiz." }

    # winget PATH changes are not guaranteed to reach the current PowerShell
    # process, so Find-Python also checks Python's standard per-user paths.
    Start-Sleep -Seconds 2
    $base = Find-Python
    if (-not $base) {
        throw "Python kuruldu ama bu terminalden bulunamadi. Pencereyi kapatip BASLAT.bat'i tekrar ac."
    }
}

if (-not (Test-Path $Py)) {
    if (Test-Path $Venv) {
        Write-Host "Yarim kalmis sanal ortam temizleniyor..." -ForegroundColor Yellow
        Remove-Item -LiteralPath $Venv -Recurse -Force
    }

    Write-Host "Sanal ortam olusturuluyor..."
    $exe = [string]$base.Exe
    $prefix = @($base.PrefixArgs)
    & $exe @prefix -m venv $Venv
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $Py)) {
        throw "venv olusturulamadi. Python: $exe"
    }
}

Write-Host "Paketler kuruluyor/guncelleniyor..."
& $Py -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "pip guncellenemedi." }

& $Py -m pip install -r (Join-Path $System "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "Python paket kurulumu basarisiz." }

Write-Host "Kaynak syntax kontrolu..."
& $Py -m compileall -q $System
if ($LASTEXITCODE -ne 0) { throw "Kaynak syntax kontrolu basarisiz." }

# V6.2.1 live screen uses a dedicated raw TCP stream on 8766. Ask for one
# UAC approval during clean install so Samsung/Android clients do not fall back
# to the slow buffered HTTP stream. Declining UAC is safe; HTTP/8765 remains.
try {
    $fwScript = Join-Path $Root "scripts\SCREEN_STREAM_FIREWALL.ps1"
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($id)
    $isAdmin = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if ($isAdmin) {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $fwScript
    } else {
        $fwArgs = '-NoProfile -ExecutionPolicy Bypass -File "' + $fwScript + '"'
        $fwProc = Start-Process powershell.exe -Verb RunAs -ArgumentList $fwArgs -Wait -PassThru
        if ($fwProc.ExitCode -ne 0) {
            Write-Host "TCP 8766 firewall izni verilmedi; HTTP fallback kullanilacak." -ForegroundColor Yellow
        }
    }
} catch {
    Write-Host "TCP 8766 firewall izni atlandi; HTTP fallback kullanilabilir." -ForegroundColor Yellow
}

try {
    & $Py (Join-Path $System "make_shortcut.py")
} catch {
    Write-Host "Masaustu kisayolu olusturulamadi; kurulum devam ediyor." -ForegroundColor Yellow
}

try {
    Write-Host "RIP + telefon otomatik baslangici ayarlaniyor..."
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Root "scripts\AUTOSTART.ps1")
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Otomatik baslangic ayarlanamadi; BASLAT.bat her acilista tekrar self-heal deneyecek." -ForegroundColor Yellow
    }
} catch {
    Write-Host "Otomatik baslangic ayarlanamadi; kurulum devam ediyor." -ForegroundColor Yellow
}

Write-Host "[OK] RIP V6.2.1 hazir." -ForegroundColor Green
