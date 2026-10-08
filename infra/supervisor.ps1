<#
.SYNOPSIS  Tiny watchdog started by run_demo.ps1: restarts the backend / worker / dashboard if they die or stop answering.
.NOTES     Checks every 3 s. Pause it by creating data\demo\supervisor.pause (crash_test.ps1 does this for the manual
           kill-and-restart variant). The simulator is deliberately NOT restarted: a restarted simulator would lose
           its in-memory retry buffer and could re-use sequence numbers, which would muddy the no-data-loss story.
           Log: data\demo\logs\supervisor.out.log
#>
param([int]$Port = 8000, [int]$DashPort = 8501, [int]$EverySeconds = 3)
$ErrorActionPreference = "Continue"
. (Join-Path $PSScriptRoot "_demo_lib.ps1")
$Pause = Join-Path $DemoDir "supervisor.pause"
$fails = @{ backend = 0; dashboard = 0 }

function Log([string]$m) { Write-Host ("{0:HH:mm:ss} {1}" -f (Get-Date), $m) }
function Pid-Of([string]$n) { try { (Get-Content $PidFile -Raw | ConvertFrom-Json).$n } catch { $null } }
function Alive([string]$n) { $p = Pid-Of $n; [bool]($p -and (Get-Process -Id $p -ErrorAction SilentlyContinue)) }
function Healthy([string]$url, [string]$expect) {
    try { (Invoke-WebRequest $url -UseBasicParsing -TimeoutSec 3).Content -match $expect } catch { $false }
}
function Restart-Comp([string]$n, [scriptblock]$start) {
    Log "RESTARTING $n"
    $p = Pid-Of $n
    if ($p) { & taskkill.exe /PID $p /T /F 2>&1 | Out-Null }
    & $start
}

# Keep the machine awake while the demo runs (per-process request; released automatically when this process ends).
# A laptop that sleeps mid-demo freezes everything, and the simulator then "catches up" hours of data in one burst.
try {
    Add-Type -Namespace Win -Name Power -MemberDefinition '[DllImport("kernel32.dll")] public static extern uint SetThreadExecutionState(uint f);'
    [void][Win.Power]::SetThreadExecutionState([uint32]::Parse('80000003', 'HexNumber'))   # ES_CONTINUOUS | ES_DISPLAY_REQUIRED | ES_SYSTEM_REQUIRED
    Log "keep-awake requested (no sleep / screen-off while the demo is up)"
} catch { Log "keep-awake unavailable: $($_.Exception.Message)" }

Log "supervisor up (backend :$Port, dashboard :$DashPort)"
while ($true) {
    Start-Sleep -Seconds $EverySeconds
    # a pause file older than 3 min is stale (an aborted test): ignore and remove it
    if (Test-Path $Pause) {
        if (((Get-Date) - (Get-Item $Pause).LastWriteTime).TotalMinutes -lt 3) { continue }
        Remove-Item $Pause -ErrorAction SilentlyContinue; Log "removed stale supervisor.pause"
    }
    # two consecutive failures before acting, so a slow request is not mistaken for a crash
    if (-not (Healthy "http://127.0.0.1:$Port/healthz" '"ok"')) { $fails.backend++ } else { $fails.backend = 0 }
    if ($fails.backend -ge 2) { Restart-Comp "backend" { Start-Backend $Port }; $fails.backend = 0 }
    if (-not (Healthy "http://127.0.0.1:$DashPort/app/_stcore/health" '^ok$')) { $fails.dashboard++ } else { $fails.dashboard = 0 }
    if ($fails.dashboard -ge 3) { Restart-Comp "dashboard" { Start-Component "dashboard" @("-m", "dashboard.serve", "--port", "$DashPort") | Out-Null }; $fails.dashboard = 0 }
    if (-not (Alive "worker")) { Restart-Comp "worker" { Start-Component "worker" @("-m", "worker") | Out-Null } }
}
