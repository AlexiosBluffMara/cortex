# install_watchdog_task.ps1 - register the Seratonin fleet watchdog as a Windows
# scheduled task that fires at user logon.
#
# Run from an elevated PowerShell:
#   pwsh -File D:\cortex\fleet\install_watchdog_task.ps1
#
# Companion task: also (re)launches the full Seratonin stack via up-seratonin.ps1
# at logon, so a reboot brings the fleet back automatically.

$ErrorActionPreference = "Stop"
$User    = "$env:USERDOMAIN\$env:USERNAME"
$Repo    = "D:\cortex"
$Pwsh    = (Get-Command pwsh -ErrorAction SilentlyContinue)?.Source
if (-not $Pwsh) { $Pwsh = "powershell.exe" }
$VenvPy  = "C:\Users\soumi\cortex\.venv\Scripts\python.exe"

function Register-Task($Name, $Action, $TriggerKind = "Logon", $Description = "") {
    Write-Host "[task] registering $Name"
    if (Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $Name -Confirm:$false
    }
    $trigger = if ($TriggerKind -eq "Logon") {
        New-ScheduledTaskTrigger -AtLogOn -User $User
    } else {
        New-ScheduledTaskTrigger -AtStartup
    }
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -ExecutionTimeLimit (New-TimeSpan -Days 365) `
        -RestartCount 5 `
        -RestartInterval (New-TimeSpan -Minutes 1)
    $principal = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $Name `
        -Action $Action `
        -Trigger $trigger `
        -Settings $settings `
        -Principal $principal `
        -Description $Description | Out-Null
}

# 1. Watchdog - keeps everything alive once it's up.
$watchdogAction = New-ScheduledTaskAction `
    -Execute $VenvPy `
    -Argument "$Repo\fleet\watchdog.py" `
    -WorkingDirectory $Repo
Register-Task "Cortex_FleetWatchdog" $watchdogAction "Logon" `
    "Pings router/backend/vite/ollama every 20s and restarts dead services on Seratonin."

# 2. Stack launcher - brings everything up once at logon, then watchdog takes over.
$upAction = New-ScheduledTaskAction `
    -Execute $Pwsh `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$Repo\fleet\up-seratonin.ps1`"" `
    -WorkingDirectory $Repo
Register-Task "Cortex_StackLaunch" $upAction "Logon" `
    "One-shot launcher that brings up the full Seratonin stack at user logon."

Write-Host ""
Write-Host "Tasks registered. Start them now with:"
Write-Host "  Start-ScheduledTask -TaskName Cortex_StackLaunch"
Write-Host "  Start-ScheduledTask -TaskName Cortex_FleetWatchdog"
Write-Host ""
Write-Host "Watchdog log: C:\Temp\logs\fleet_watchdog.log"
Write-Host "Watchdog HTTP: http://localhost:8780/status"
