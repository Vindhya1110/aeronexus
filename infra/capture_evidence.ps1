<#
.SYNOPSIS  Screenshots of the running dashboard (PNG) for the Round 1 slides / Round 2 report. Needs Edge or Chrome, nothing else.
.EXAMPLE   .\infra\capture_evidence.ps1                          # all screens -> data\demo\evidence\<timestamp>\
.EXAMPLE   .\infra\capture_evidence.ps1 -Shots "Events,Source insight" -Zone HITEC
.NOTES     Best taken while the HITEC smoke event is open (live mode: ~3-8 min after start; replay mode: any time).
           For a screen RECORDING instead: Win+Alt+R (Xbox Game Bar) on the browser window, or OBS.
#>
param([string]$Shots = "Overview,Live map,Zone detail,Source insight,Forecast,Events,Node health", [string]$Zone = "HITEC", [ValidateSet("admin", "user")][string]$As = "admin")
$Root = Split-Path -Parent $PSScriptRoot
& (Join-Path $Root "venv\Scripts\python.exe") (Join-Path $Root "tools\capture_screens.py") --shots $Shots --zone $Zone --as $As
exit $LASTEXITCODE
