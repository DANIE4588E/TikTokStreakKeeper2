# Registers the daily 00:01 run in Windows Task Scheduler. Run once with:
#   powershell -ExecutionPolicy Bypass -File register_task.ps1
#
# The task runs run_daily.cmd from this folder as YOUR user, only while you
# are logged on (the browser needs a desktop session). If the PC is asleep at
# 00:01, StartWhenAvailable fires it as soon as the session is back.

$ErrorActionPreference = "Stop"

$root     = Split-Path -Parent $MyInvocation.MyCommand.Path
$taskName = "TikTokStreakKeeper Daily"
$cmd      = Join-Path $root "run_daily.cmd"

if (-not (Test-Path $cmd)) { throw "Missing $cmd next to this script." }

$action  = New-ScheduledTaskAction -Execute "cmd.exe" `
           -Argument "/c `"$cmd`"" -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Daily -At 12:01AM
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
            -MultipleInstances IgnoreNew `
            -ExecutionTimeLimit ([TimeSpan]::FromHours(1))

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Settings $settings `
    -Description "Sends the TikTok message in message.txt to everyone in friends.json at 00:01 daily." |
    Out-Null

$info = Get-ScheduledTaskInfo -TaskName $taskName
Write-Host "Registered '$taskName'."
Write-Host "Next run: $($info.NextRunTime)"
Write-Host "Log file: $(Join-Path $root 'logs\cron.log')"
Write-Host "Remove it later with:"
Write-Host "  Unregister-ScheduledTask -TaskName '$taskName' -Confirm:`$false"
