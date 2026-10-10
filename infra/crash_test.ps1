<#
.SYNOPSIS  Kill the backend mid-stream, restart it, prove the simulator's buffered packets arrive: no loss, no duplicates.
.EXAMPLE   .\infra\run_demo.ps1 ; .\infra\crash_test.ps1              # 20 s outage
.EXAMPLE   .\infra\crash_test.ps1 -OutageS 45                          # longer outage
.EXAMPLE   .\infra\crash_test.ps1 -Auto                                # no manual restart: the supervisor brings the backend back
.NOTES     Needs the LIVE demo running (simulator streaming). Safe to run live in front of a judge: the dashboard just
           shows stale data for the outage and catches up. What it shows: the gateway (simulator) buffers while the backend
           is down (it logs "backend unreachable; buffering"), resends oldest-first on recovery, and the backend's unique
           (device_id, sequence) key makes any resend idempotent.
#>
param([int]$OutageS = 20, [int]$Port = 8000, [switch]$Auto)
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_demo_lib.ps1")
$Url = "http://127.0.0.1:$Port"
Initialize-Auth $Url
function Say([string]$m) { Write-Host ("[{0:HH:mm:ss}] {1}" -f (Get-Date), $m) }
$pids = Get-Content $PidFile -Raw | ConvertFrom-Json
if (-not $pids.simulator -or -not (Get-Process -Id $pids.simulator -ErrorAction SilentlyContinue)) {
    throw "simulator is not running - start the demo in live mode first (.\infra\run_demo.ps1)"
}
$simLog = Join-Path $LogDir "simulator.err.log"
function Buffered { $l = Select-String -Path $simLog -Pattern "buffered=(\d+)" | Select-Object -Last 1; if ($l) { [int]$l.Matches[0].Groups[1].Value } else { 0 } }

Say "1/5 baseline integrity"
Invoke-Py @((Join-Path $Root "tools\check_integrity.py"), "--db", $DbPath, "--snapshot", (Join-Path $DemoDir "integrity_before.json")) "integrity_before" | Select-Object -Last 1
$unreachBefore = @(Select-String -Path $simLog -Pattern "unreachable").Count
$bufStat = Select-String -Path $simLog -Pattern "'buffered': (\d+)" | Select-Object -Last 1
$bufferedBefore = if ($bufStat) { [int]$bufStat.Matches[0].Groups[1].Value } else { 0 }   # simulator's cumulative "ever buffered" counter

$pauseFile = Join-Path $DemoDir "supervisor.pause"
try {
if (-not $Auto) { "paused by crash_test" | Set-Content $pauseFile }   # we restart the backend ourselves, on cue
Say "2/5 KILLING the backend (pid $($pids.backend)) - simulated crash / network cut for $OutageS s"
& taskkill.exe /PID $pids.backend /T /F | Out-Null
Start-Sleep -Seconds 2
try { Get-Json "$Url/healthz" | Out-Null; throw "backend still answering" } catch { if ($_.Exception.Message -eq "backend still answering") { throw } }
Say "    backend is DOWN (healthz refused). Simulator keeps generating and buffering..."
Start-Sleep -Seconds ($OutageS - 2)
$unreachNow = @(Select-String -Path $simLog -Pattern "unreachable").Count
Say "    simulator logged $($unreachNow - $unreachBefore) 'backend unreachable; buffering' warnings"

if ($Auto) {
    Say "3/5 NOT restarting by hand: waiting for the supervisor to notice and restart the backend"
    if (-not (Wait-Until { (Get-Json "$Url/healthz").status -eq "ok" } 60 "supervisor restarts backend")) { throw "supervisor did not restart the backend" }
} else {
    Say "3/5 RESTARTING the backend on the same database"
    $env:AIRDNA_DATABASE_URL = "sqlite:///" + $DbPath.Replace('\', '/')
    $env:AIRDNA_API_KEY = "dev-key"
    $env:AIRDNA_OFFLINE_AFTER_S = "300"
    $env:AIRDNA_REQUIRE_AUTH = "1"
    $env:AIRDNA_AUTH_SECRET = (Get-Content (Join-Path $DemoDir "credentials.json") -Raw | ConvertFrom-Json).auth_secret
    Start-Backend $Port
    Remove-Item $pauseFile -ErrorAction SilentlyContinue
    if (-not (Wait-Until { (Get-Json "$Url/healthz").status -eq "ok" } 30 "backend back up")) { throw "backend did not restart" }
}
Say "    backend is UP again"

Say "4/5 waiting for the simulator buffer to drain (needs a NEW simulator status line reporting buffered=0)"
$linesAtRestart = @(Get-Content $simLog).Count
$statusLine = $null
$ok = Wait-Until {
    $new = @(Get-Content $simLog | Select-Object -Skip $linesAtRestart | Where-Object { $_ -match "stats=.*buffered=0$" })
    if ($new.Count) { $script:statusLine = $new[-1]; $true } else { $false }
} 60 "fresh simulator status line with buffered=0"
if ($ok) { Say "    simulator says: $($statusLine.Substring($statusLine.IndexOf('stats=')))" }
$fresh = (Get-Json "$Url/v1/devices").devices | Where-Object { $_.last_age_s -lt 30 }
Say "    devices streaming again: $(@($fresh).Count)/$(@((Get-Json "$Url/v1/devices").devices).Count)"

Say "5/5 integrity after recovery"
& $Py (Join-Path $Root "tools\check_integrity.py") --db $DbPath --compare (Join-Path $DemoDir "integrity_before.json")
$integrityRc = $LASTEXITCODE
# end-to-end count: DB rows must cover (history preloaded) + (everything the simulator says it delivered)
$history = @(Get-Content (Join-Path $DemoDir "demo_history.jsonl")).Count
$delivered = [int]([regex]::Match($statusLine, "'delivered': (\d+)").Groups[1].Value)
$buffered = [int]([regex]::Match($statusLine, "'buffered': (\d+)").Groups[1].Value) - $bufferedBefore
$rows = [int](& $Py -c "import sqlite3;print(sqlite3.connect(r'$DbPath').execute('select count(*) from raw_measurements').fetchone()[0])")
Say "    packets buffered during outage (simulator counter): $buffered; delivered by simulator: $delivered; DB rows: $rows (preloaded history $history)"
if ($integrityRc -ne 0 -or -not $ok -or $buffered -lt 1 -or $rows -lt ($history + $delivered)) { Write-Host "CRASH TEST: FAIL" -ForegroundColor Red; exit 1 }
Write-Host "CRASH TEST: PASS - outage of $OutageS s, nothing lost, nothing duplicated" -ForegroundColor Green
} finally {
    Remove-Item $pauseFile -ErrorAction SilentlyContinue   # never leave the supervisor paused, even if the test aborts
}
