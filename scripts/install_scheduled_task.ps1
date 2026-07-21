param(
    [ValidateSet("Daily", "Minutes")]
    [string]$Mode,
    [string]$DailyTime,
    [int]$EveryMinutes
)

$ErrorActionPreference = "Stop"
$packageDir = Split-Path -Parent $PSScriptRoot
$runner = Join-Path $packageDir "run_once.bat"
$taskName = "VOC AI Tagger"

if (-not (Test-Path -LiteralPath $runner)) {
    throw "Missing runner: $runner"
}

if (-not $Mode) {
    Write-Host "Choose schedule mode:"
    Write-Host "  1. Run every day at a fixed time"
    Write-Host "  2. Run every N minutes"
    $choice = Read-Host "Enter 1 or 2 [1]"
    $Mode = if ($choice -eq "2") { "Minutes" } else { "Daily" }
}

if ($Mode -eq "Daily") {
    if (-not $DailyTime) {
        $DailyTime = Read-Host "Daily start time (HH:mm) [02:00]"
        if (-not $DailyTime) { $DailyTime = "02:00" }
    }
    try {
        $at = [datetime]::ParseExact($DailyTime, "HH:mm", $null)
    } catch {
        throw "Time must use HH:mm, for example 02:00."
    }
    $trigger = New-ScheduledTaskTrigger -Daily -At $at
    $scheduleText = "every day at $DailyTime"
} else {
    if (-not $EveryMinutes) {
        $text = Read-Host "Run every N minutes [30]"
        $EveryMinutes = if ($text) { [int]$text } else { 30 }
    }
    if ($EveryMinutes -lt 5) {
        throw "The interval must be at least 5 minutes."
    }
    $trigger = New-ScheduledTaskTrigger `
        -Once `
        -At (Get-Date).AddMinutes(1) `
        -RepetitionInterval (New-TimeSpan -Minutes $EveryMinutes)
    $scheduleText = "every $EveryMinutes minutes"
}

$action = New-ScheduledTaskAction `
    -Execute "cmd.exe" `
    -Argument "/d /c `"`"$runner`"`"" `
    -WorkingDirectory $packageDir

$currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal `
    -UserId $currentUser `
    -LogonType Interactive `
    -RunLevel Limited

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 5) `
    -ExecutionTimeLimit (New-TimeSpan -Hours 6)

$task = New-ScheduledTask `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Description "Run one VOC AI tagging batch with the portable package."

Register-ScheduledTask -TaskName $taskName -InputObject $task -Force | Out-Null
Write-Host "Scheduled task created: $taskName ($scheduleText)"
Write-Host "Windows user: $currentUser"
Write-Host "The task runs only while this user is logged on, so DPAPI secrets can be decrypted."
