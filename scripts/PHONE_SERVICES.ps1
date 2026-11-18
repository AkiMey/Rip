param([switch]$Remove, [switch]$Elevated, [switch]$Quiet)
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [Text.Encoding]::UTF8

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$System = Join-Path $Root "sistem"
$Pyw = Join-Path $System ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $Pyw)) { throw "Venv yok. Once BASLAT.bat calistir." }

$RunKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
$LegacyTaskNames = @(
    "Ripperinio Persistent Bridge",
    "Ripperinio Mobile Remote"
)

$services = @(
    @{
        TaskName = "Ripperinio V5 Persistent Bridge"
        RunName  = "RipperinioV5PersistentBridge"
        Script   = (Join-Path $System "ripperinio_web\persistent_bridge_server.py")
        Args     = ""
    },
    @{
        TaskName = "Ripperinio V5 Mobile Remote"
        RunName  = "RipperinioV5MobileRemote"
        Script   = (Join-Path $System "ripperinio_web\mobile_remote_runtime.py")
        Args     = "--daemon"
    }
)

function Say([string]$Text, [string]$Color = "Gray") {
    if (-not $Quiet) { Write-Host $Text -ForegroundColor $Color }
}

function Invoke-HiddenSchtasks([string[]]$Arguments) {
    try {
        & "$env:SystemRoot\System32\schtasks.exe" @Arguments *> $null
        return $LASTEXITCODE
    } catch { return 1 }
}

function Test-IsAdmin {
    try {
        $id = [Security.Principal.WindowsIdentity]::GetCurrent()
        $p = New-Object Security.Principal.WindowsPrincipal($id)
        return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    } catch { return $false }
}

function Test-TaskExists([string]$Name) {
    return ((Invoke-HiddenSchtasks @('/Query','/TN', $Name)) -eq 0)
}

function Remove-TaskSafe([string]$Name) {
    [void](Invoke-HiddenSchtasks @('/End','/TN', $Name))
    [void](Invoke-HiddenSchtasks @('/Delete','/TN', $Name,'/F'))
    try { Unregister-ScheduledTask -TaskName $Name -Confirm:$false -ErrorAction SilentlyContinue } catch {}
}

function Remove-RunFallback([string]$Name) {
    try {
        if (Test-Path $RunKey) {
            Remove-ItemProperty -Path $RunKey -Name $Name -ErrorAction SilentlyContinue
        }
    } catch {}
}

function Set-RunFallback($Service) {
    New-Item -Path $RunKey -Force | Out-Null
    $cmd = '"' + $Pyw + '" "' + $Service.Script + '"'
    if ($Service.Args) { $cmd += ' ' + $Service.Args }
    New-ItemProperty -Path $RunKey -Name $Service.RunName -Value $cmd -PropertyType String -Force | Out-Null
    Say "Baslangic fallback kaydi: $($Service.RunName)" "Yellow"
}

function Start-ServiceProcess($Service) {
    try {
        $argLine = '"' + $Service.Script + '"'
        if ($Service.Args) { $argLine += ' ' + $Service.Args }
        Start-Process -FilePath $Pyw -ArgumentList $argLine -WorkingDirectory $System -WindowStyle Hidden | Out-Null
    } catch {
        Say "Servis hemen baslatilamadi: $($Service.TaskName)" "Yellow"
    }
}

function Test-ServiceTaskMatches($Service) {
    try {
        $task = Get-ScheduledTask -TaskName $Service.TaskName -ErrorAction Stop
        $action = @($task.Actions)[0]
        if (-not $action) { return $false }
        $exeOk = ([string]$action.Execute).Trim('"') -ieq $Pyw
        $args = [string]$action.Arguments
        $scriptOk = $args.IndexOf([string]$Service.Script, [StringComparison]::OrdinalIgnoreCase) -ge 0
        $extraOk = $true
        if ($Service.Args) {
            $extraOk = $args.IndexOf([string]$Service.Args, [StringComparison]::OrdinalIgnoreCase) -ge 0
        }
        return ($exeOk -and $scriptOk -and $extraOk)
    } catch { return $false }
}

# Legacy SYSTEM tasks may require one UAC elevation to remove. Only elevate when
# those exact old task names exist; normal V6 startup remains prompt-free.
$legacyFound = $false
foreach ($name in $LegacyTaskNames) {
    if (Test-TaskExists $name) { $legacyFound = $true; break }
}
if ($legacyFound -and -not (Test-IsAdmin) -and -not $Elevated) {
    try {
        $removeArg = if ($Remove) { ' -Remove' } else { '' }
        $quietArg = if ($Quiet) { ' -Quiet' } else { '' }
        $argLine = '-NoProfile -ExecutionPolicy Bypass -File "' + $PSCommandPath + '" -Elevated' + $removeArg + $quietArg
        $child = Start-Process powershell.exe -Verb RunAs -ArgumentList $argLine -Wait -PassThru
        exit $child.ExitCode
    } catch {
        Say "Eski SYSTEM bridge gorevi kaldirilamadi; kullanici fallback'i ile devam ediliyor." "Yellow"
    }
}

foreach ($name in $LegacyTaskNames) { Remove-TaskSafe $name }

foreach ($s in $services) {
    if ($Remove) {
        Remove-TaskSafe $s.TaskName
        Remove-RunFallback $s.RunName
        Say "Silindi: $($s.TaskName) / $($s.RunName)"
        continue
    }

    $registered = Test-ServiceTaskMatches $s
    if (-not $registered) {
        # A task from another RIP source folder must not keep launching old code.
        Remove-TaskSafe $s.TaskName
        Remove-RunFallback $s.RunName
        try {
            $arg = '"' + $s.Script + '"'
            if ($s.Args) { $arg += ' ' + $s.Args }
            $action = New-ScheduledTaskAction -Execute $Pyw -Argument $arg -WorkingDirectory $System
            $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
            try { $trigger.Delay = "PT3S" } catch {}
            $settings = New-ScheduledTaskSettingsSet `
                -AllowStartIfOnBatteries `
                -DontStopIfGoingOnBatteries `
                -StartWhenAvailable `
                -MultipleInstances IgnoreNew

            Register-ScheduledTask -TaskName $s.TaskName -Action $action -Trigger $trigger -Settings $settings -Force -ErrorAction Stop | Out-Null
            $registered = $true
            Say "Gorev kuruldu/guncellendi: $($s.TaskName)" "Green"
        } catch {
            $registered = $false
            Say "Task Scheduler izin vermedi; kullanici baslangici kullanilacak." "Yellow"
        }
    }

    if ($registered) {
        Remove-RunFallback $s.RunName
        # IgnoreNew prevents a duplicate if it is already running.
        try { Start-ScheduledTask -TaskName $s.TaskName -ErrorAction SilentlyContinue } catch {}
    } else {
        Set-RunFallback $s
        Start-ServiceProcess $s
    }
}

Say "[OK] Telefon servis otomatik baslangici hazir." "Green"
exit 0
