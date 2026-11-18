param([switch]$Remove, [switch]$Quiet)
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [Text.Encoding]::UTF8

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$System = Join-Path $Root "sistem"
$Pyw = Join-Path $System ".venv\Scripts\pythonw.exe"
$Launcher = Join-Path $Root "scripts\START_RIP_HIDDEN.py"
$PhoneScript = Join-Path $Root "scripts\PHONE_SERVICES.ps1"
$TaskName = "Ripperinio V6 Auto Start"
$RunName = "RipperinioV6AutoStart"
$RunKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
$StatusDir = Join-Path $env:LOCALAPPDATA "RIPPERINIO"
$StatusFile = Join-Path $StatusDir "autostart_status.txt"

function Say([string]$Text, [string]$Color = "Gray") {
    if (-not $Quiet) { Write-Host $Text -ForegroundColor $Color }
}

function Remove-TaskSafe([string]$Name) {
    try { Stop-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue } catch {}
    try { Unregister-ScheduledTask -TaskName $Name -Confirm:$false -ErrorAction SilentlyContinue } catch {}
}

function Remove-RunFallback {
    try {
        if (Test-Path $RunKey) {
            Remove-ItemProperty -Path $RunKey -Name $RunName -ErrorAction SilentlyContinue
        }
    } catch {}
}

function Set-RunFallback {
    New-Item -Path $RunKey -Force | Out-Null
    $cmd = '"' + $Pyw + '" "' + $Launcher + '" --autostart'
    New-ItemProperty -Path $RunKey -Name $RunName -Value $cmd -PropertyType String -Force | Out-Null
}

function Test-TaskMatches {
    try {
        $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
        $action = @($task.Actions)[0]
        if (-not $action) { return $false }
        $exeOk = ([string]$action.Execute).Trim('"') -ieq $Pyw
        $args = [string]$action.Arguments
        $argOk = $args.IndexOf($Launcher, [StringComparison]::OrdinalIgnoreCase) -ge 0
        return ($exeOk -and $argOk)
    } catch {
        return $false
    }
}

if ($Remove) {
    Remove-TaskSafe $TaskName
    Remove-RunFallback
    try { & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $PhoneScript -Remove -Quiet *> $null } catch {}
    Say "[OK] RIP otomatik baslangic kaldirildi." "Green"
    exit 0
}

if (-not (Test-Path $Pyw)) { throw "Venv yok. Once BASLAT.bat calistir." }
if (-not (Test-Path $Launcher)) { throw "START_RIP_HIDDEN.py bulunamadi." }

$registered = $false
if (Test-TaskMatches) {
    $registered = $true
    Say "RIP otomatik baslangic gorevi zaten guncel." "DarkGray"
} else {
    # Old path/version task: replace it with the source folder being run now.
    Remove-TaskSafe $TaskName
    try {
        $action = New-ScheduledTaskAction -Execute $Pyw -Argument ('"' + $Launcher + '" --autostart') -WorkingDirectory $Root
        $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
        try { $trigger.Delay = "PT8S" } catch {}
        $settings = New-ScheduledTaskSettingsSet `
            -AllowStartIfOnBatteries `
            -DontStopIfGoingOnBatteries `
            -StartWhenAvailable `
            -MultipleInstances IgnoreNew

        Register-ScheduledTask `
            -TaskName $TaskName `
            -Action $action `
            -Trigger $trigger `
            -Settings $settings `
            -Description "RIPPERINIO UI + phone services auto-start for the current user" `
            -Force -ErrorAction Stop | Out-Null

        $registered = $true
        Say "RIP otomatik baslangic gorevi kuruldu." "Green"
    } catch {
        Say "Task Scheduler kullanilamadi; HKCU Run fallback kuruluyor." "Yellow"
    }
}

if ($registered) {
    Remove-RunFallback
} else {
    Set-RunFallback
}

# Phone bridge/mobile tasks are also self-healed on every BASLAT. The script is
# idempotent: if actions already point at this source tree, it does not tear
# down a healthy running bridge.
try {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $PhoneScript -Quiet *> $null
    if ($LASTEXITCODE -ne 0) { throw "PHONE_SERVICES exit=$LASTEXITCODE" }
} catch {
    Say "Telefon servis gorevleri yenilenemedi; RIP acilinca dahili self-heal yine deneyecek." "Yellow"
}

try {
    New-Item -ItemType Directory -Force -Path $StatusDir | Out-Null
    $mode = if ($registered) { "TASK" } else { "HKCU_RUN" }
    @(
        "version=6.2.1",
        "mode=$mode",
        "root=$Root",
        "updated=$(Get-Date -Format o)"
    ) | Set-Content -Path $StatusFile -Encoding UTF8
} catch {}

Say "[OK] RIP + telefon servis otomatik baslangici hazir." "Green"
exit 0
