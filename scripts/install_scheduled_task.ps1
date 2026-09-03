param(
    [ValidateSet("Install", "Remove", "Query")]
    [string]$Operation = "Install",
    [string]$TaskName = "VOC AI Tagger",
    [string]$ConfigPath,
    [string]$ProfileName = "默认配置",
    [string]$ProfileId = "business",
    [ValidateSet("Daily", "Minutes")]
    [string]$Mode,
    [string]$DailyTime,
    [int]$EveryMinutes
)

$ErrorActionPreference = "Stop"
$OutputEncoding = [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$packageDir = Split-Path -Parent $PSScriptRoot
$pythonExe = Join-Path $packageDir ".runtime\python\python.exe"
$runnerScript = Join-Path $PSScriptRoot "voc_run_once.py"

function Test-SamePath {
    param([string]$Left, [string]$Right)
    if (-not $Left -or -not $Right) {
        return $false
    }
    try {
        $leftPath = [System.IO.Path]::GetFullPath($Left)
        $rightPath = [System.IO.Path]::GetFullPath($Right)
        return [string]::Equals(
            $leftPath,
            $rightPath,
            [System.StringComparison]::OrdinalIgnoreCase
        )
    } catch {
        return $false
    }
}

if (-not $TaskName.Trim()) {
    throw "TaskName cannot be empty."
}

$existingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue

if ($Operation -eq "Query") {
    if ($existingTask) {
        $isExpectedProfile = $true
        if ($ConfigPath) {
            $expectedConfigPath = [System.IO.Path]::GetFullPath($ConfigPath)
            $expectedArguments = "`"$runnerScript`" --config `"$expectedConfigPath`" --profile-name `"$ProfileName`" --profile-id `"$ProfileId`""
            $actions = @($existingTask.Actions)
            $isExpectedProfile = $actions.Count -eq 1 `
                -and (Test-SamePath $actions[0].Execute $pythonExe) `
                -and (Test-SamePath $actions[0].WorkingDirectory $packageDir) `
                -and [string]::Equals(
                    $actions[0].Arguments,
                    $expectedArguments,
                    [System.StringComparison]::Ordinal
                )
        }
        if (-not $isExpectedProfile) {
            Write-Output "VOC_TASK_STATUS:LEGACY:$($existingTask.State)"
        } elseif ($existingTask.Settings.Enabled -eq $false) {
            Write-Output "VOC_TASK_STATUS:DISABLED"
        } else {
            Write-Output "VOC_TASK_STATUS:INSTALLED:$($existingTask.State)"
        }
    } else {
        Write-Output "VOC_TASK_STATUS:NOT_FOUND"
    }
    exit 0
}

if ($Operation -eq "Remove") {
    if (-not $existingTask) {
        Write-Output "VOC_TASK_STATUS:NOT_FOUND"
        exit 0
    }
    if ($existingTask.State -eq "Running") {
        Disable-ScheduledTask -TaskName $TaskName | Out-Null
        Write-Output "VOC_TASK_STATUS:DISABLED"
        exit 0
    }
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Output "VOC_TASK_STATUS:REMOVED"
    exit 0
}

if ($existingTask -and $existingTask.State -eq "Running") {
    throw "The scheduled task is running. Wait for the current batch to finish, then apply the schedule again."
}

if (-not $ConfigPath) {
    $ConfigPath = Join-Path $PSScriptRoot "voc_tagger_config.json"
}
$resolvedConfigPath = (Resolve-Path -LiteralPath $ConfigPath).Path

if (-not (Test-Path -LiteralPath $pythonExe)) {
    throw "Missing portable Python runtime: $pythonExe"
}
if (-not (Test-Path -LiteralPath $runnerScript)) {
    throw "Missing runner: $runnerScript"
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
    $scheduleTrigger = New-ScheduledTaskTrigger -Daily -At $at
    $scheduleText = "every day at $DailyTime"
} else {
    if (-not $EveryMinutes) {
        $text = Read-Host "Run every N minutes [30]"
        $EveryMinutes = if ($text) { [int]$text } else { 30 }
    }
    if ($EveryMinutes -lt 5) {
        throw "The interval must be at least 5 minutes."
    }
    $scheduleTrigger = New-ScheduledTaskTrigger `
        -Once `
        -At (Get-Date).AddMinutes(1) `
        -RepetitionInterval (New-TimeSpan -Minutes $EveryMinutes)
    $scheduleText = "every $EveryMinutes minutes"
}

$runnerArguments = "`"$runnerScript`" --config `"$resolvedConfigPath`" --profile-name `"$ProfileName`" --profile-id `"$ProfileId`""
$action = New-ScheduledTaskAction `
    -Execute $pythonExe `
    -Argument $runnerArguments `
    -WorkingDirectory $packageDir

$currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$logonTrigger = New-ScheduledTaskTrigger -AtLogOn -User $currentUser
$triggers = @($scheduleTrigger, $logonTrigger)
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
    -Trigger $triggers `
    -Principal $principal `
    -Settings $settings `
    -Description "Run the $ProfileName VOC AI tagging profile."

Register-ScheduledTask -TaskName $TaskName -InputObject $task -Force | Out-Null
Write-Output "VOC_TASK_STATUS:INSTALLED"
Write-Host "Scheduled task created: $TaskName ($scheduleText)"
Write-Host "Windows user: $currentUser"
Write-Host "The task also runs once whenever this Windows user logs on."
Write-Host "The task runs only while this user is logged on, so DPAPI secrets can be decrypted."
