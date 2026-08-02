[CmdletBinding()]
param(
    [string]$TaskName = "Freshdesk Metrics Daily Update",
    [string]$At = "07:00"
)
$ErrorActionPreference = "Stop"
$Runner = Join-Path $PSScriptRoot "update_freshdesk.ps1"
$Action = New-ScheduledTaskAction -Execute (Get-Command powershell.exe).Source -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$Runner`""
$Trigger = New-ScheduledTaskTrigger -Daily -At $At
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Description "Descarga actividades de Freshdesk y las publica en GitHub." -Force
Write-Host "Tarea '$TaskName' registrada diariamente a las $At."
