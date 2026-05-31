# Capital Surabaya — Register Windows Task Scheduler jobs
# Run once as Administrator: .\schedule\register_tasks.ps1

$ProjectDir = "C:\Users\kanna\OneDrive - The Siam Cement Public Company Limited\Desktop\Capital Surabaya - Claude Code"

# Post-Close: 04:00 ICT daily = 21:00 UTC = 04:00 local if machine is set to ICT
$PostCloseAction = New-ScheduledTaskAction `
    -Execute "cmd.exe" `
    -Argument "/c `"$ProjectDir\schedule\post_close.bat`""

$PostCloseTrigger = New-ScheduledTaskTrigger -Daily -At "04:00"

Register-ScheduledTask `
    -TaskName "CapitalSurabaya_PostClose" `
    -Action $PostCloseAction `
    -Trigger $PostCloseTrigger `
    -RunLevel Highest `
    -Description "Capital Surabaya post-close pipeline (04:00 ICT)" `
    -Force

# Pre-Market: 15:00 ICT daily = 08:00 UTC
$PreMarketAction = New-ScheduledTaskAction `
    -Execute "cmd.exe" `
    -Argument "/c `"$ProjectDir\schedule\pre_market.bat`""

$PreMarketTrigger = New-ScheduledTaskTrigger -Daily -At "15:00"

Register-ScheduledTask `
    -TaskName "CapitalSurabaya_PreMarket" `
    -Action $PreMarketAction `
    -Trigger $PreMarketTrigger `
    -RunLevel Highest `
    -Description "Capital Surabaya pre-market pipeline (15:00 ICT)" `
    -Force

Write-Host "Tasks registered:"
Get-ScheduledTask -TaskName "CapitalSurabaya_*" | Select-Object TaskName, State
