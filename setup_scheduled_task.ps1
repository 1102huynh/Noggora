# One-time setup: registers a Windows Scheduled Task that runs run_auto.ps1
# once a day, which in turn runs `python main.py auto` (auto-picks the next
# unused topic from data/topic_bank.csv, produces final.mp4).
#
# Usage:   powershell -ExecutionPolicy Bypass -File setup_scheduled_task.ps1 [-Time "08:00"]
# Remove:  Unregister-ScheduledTask -TaskName "Noggora Daily Video" -Confirm:$false

param(
    [string]$Time = "08:00"
)

$taskName = "Noggora Daily Video"
$scriptPath = Join-Path $PSScriptRoot "run_auto.ps1"

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`""
$trigger = New-ScheduledTaskTrigger -Daily -At $Time
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 1)

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings `
    -Description "Auto-generates 1 Noggora video/day from data/topic_bank.csv (see run_auto.ps1)." `
    -Force | Out-Null

Write-Host "Scheduled task '$taskName' registered: runs daily at $Time (only while you're logged in)."
Write-Host "Check it any time:  Get-ScheduledTask -TaskName '$taskName' | Get-ScheduledTaskInfo"
Write-Host "Run it right now:   Start-ScheduledTask -TaskName '$taskName'"
Write-Host "Remove it:          Unregister-ScheduledTask -TaskName '$taskName' -Confirm:`$false"
