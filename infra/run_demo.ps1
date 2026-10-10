<#
.SYNOPSIS  One-command Aeronex demo: backend + worker (scheduled) + simulator + dashboard, health-checked.
.EXAMPLE   .\infra\run_demo.ps1                 # live mode: history to 09:30 IST preloaded, narrative plays on at x12
.EXAMPLE   .\infra\run_demo.ps1 -Mode replay    # fallback: whole narrative pre-loaded (anomaly in progress), no live feed
.EXAMPLE   .\infra\run_demo.ps1 -Mode replay -Rebuild   # also re-ingest from the JSONL instead of using the snapshot
.NOTES     Stop with .\infra\stop_demo.ps1. Logs: data\demo\logs\. DB: data\demo\demo.db (recreated every start).
#>
param(
    [ValidateSet("live", "replay")][string]$Mode = "live",
    [double]$Speedup = 12,
    [int]$Port = 8000,
    [int]$DashPort = 8501,
    [switch]$Rebuild,
    [switch]$NoBrowser,
    [string]$HardwarePort = "",                 # ESP32 node: serial port (COM5) or "sim" for a synthetic node
    [string]$HardwareId = "ZONE_HITEC_90"
)
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_demo_lib.ps1")
$t0 = Get-Date
function Step([string]$m) { Write-Host ("[{0,5:N1}s] {1}" -f ((Get-Date) - $t0).TotalSeconds, $m) }

Step "stopping anything left over"
& (Join-Path $PSScriptRoot "stop_demo.ps1") -Ports $Port, $DashPort | Out-Null

$Url = "http://127.0.0.1:$Port"
$env:AIRDNA_DATABASE_URL = "sqlite:///" + $DbPath.Replace('\', '/')
$env:AIRDNA_API_KEY = "dev-key"
$env:AIRDNA_BACKEND_URL = $Url
$env:AIRDNA_WORKER_INTERVAL_S = if ($Mode -eq "live") { "15" } else { "60" }
# replay data is not "received" now, so the server-clock liveness would flag everything offline after 5 min
$env:AIRDNA_OFFLINE_AFTER_S = if ($Mode -eq "live") { "300" } else { "604800" }
$env:AIRDNA_AUTOREFRESH_S = "10"
# sign-in is enforced on the demo API; the secret is persisted (data\demo\credentials.json) so restarts keep sessions valid
$env:AIRDNA_REQUIRE_AUTH = "1"
Remove-Item Env:AIRDNA_DASHBOARD_URL -ErrorAction SilentlyContinue   # an old session value (http://localhost:8501) must not leak into the new one-site setup
$env:AIRDNA_DASHBOARD_UPSTREAM = "http://127.0.0.1:$DashPort"   # the backend proxies /app/ to this (one website, one port)
$env:PYTHONUNBUFFERED = "1"

Step "preparing seeded dataset + fresh database ($Mode)"
Invoke-Py @((Join-Path $Root "tools\demo_data.py"), "prepare") "demo_prepare" | Out-Null
$loadArgs = @((Join-Path $Root "tools\demo_data.py"), "load", "--mode", $Mode)
if ($Rebuild) { $loadArgs += "--rebuild" }
Invoke-Py $loadArgs "demo_load" | Select-String "restored|built" | ForEach-Object { Step $_.Line }

$env:AIRDNA_AUTH_SECRET = (Get-Content (Join-Path $DemoDir "credentials.json") -Raw | ConvertFrom-Json).auth_secret

Step "starting backend"
Start-Backend $Port
if (-not (Wait-Until { (Get-Json "$Url/healthz").status -eq "ok" } 30 "backend /healthz")) { throw "backend did not come up: see data\demo\logs\backend.err.log" }

Initialize-Auth $Url

Step "starting worker (scheduled, every $($env:AIRDNA_WORKER_INTERVAL_S) s)"
Start-Component "worker" @("-m", "worker") | Out-Null
$allDerived = {
    $z = (Get-Json "$Url/v1/zones").zones
    ($z.Count -eq 6) -and (@($z | Where-Object { $_.aqi -and $_.coverage -and $_.source_hypothesis }).Count -eq 6)
}
if (-not (Wait-Until $allDerived 60 "worker output for all 6 zones")) { throw "worker produced no results: see data\demo\logs\worker.err.log" }

if ($Mode -eq "live") {
    Step "starting simulator (x$Speedup, scripted demo scenario, continues at 09:30 IST)"
    Start-Simulator $Url $Speedup "2026-12-10T04:00:00Z"
    $before = [datetime]::UtcNow
    $fresh = { ((Get-Json "$Url/v1/devices").devices | Where-Object { $_.last_age_s -lt 20 }).Count -ge 8 }
    if (-not (Wait-Until $fresh 45 "live packets from >= 8 devices")) { throw "simulator is not delivering: see data\demo\logs\simulator.*.log" }
}

if ($HardwarePort) {
    Step "starting ESP32 node bridge ($HardwarePort -> $HardwareId)"
    $bargs = @("-m", "gateway.serial_bridge", "--url", $Url, "--device-id", $HardwareId, "--fresh")
    if ($HardwarePort -eq "sim") { $bargs += "--simulate" } else { $bargs += @("--port", $HardwarePort) }
    Start-Component "bridge" $bargs | Out-Null
}

Step "starting dashboard"
Start-Component "dashboard" @("-m", "dashboard.serve", "--port", "$DashPort") | Out-Null
if (-not (Wait-Until { (Invoke-WebRequest "http://127.0.0.1:$DashPort/app/_stcore/health" -UseBasicParsing -TimeoutSec 3).Content -eq "ok" } 45 "dashboard health")) {
    throw "dashboard did not come up: see data\demo\logs\dashboard.err.log"
}

Step "starting supervisor (auto-restarts backend / worker / dashboard)"
Remove-Item (Join-Path $DemoDir "supervisor.pause") -ErrorAction SilentlyContinue
$sup = Start-Process -FilePath "powershell.exe" -WindowStyle Hidden -PassThru -WorkingDirectory $Root `
    -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $PSScriptRoot "supervisor.ps1"), "-Port", "$Port", "-DashPort", "$DashPort") `
    -RedirectStandardOutput (Join-Path $LogDir "supervisor.out.log") -RedirectStandardError (Join-Path $LogDir "supervisor.err.log")
Save-Pid "supervisor" $sup.Id

$z = (Get-Json "$Url/v1/zones").zones
$dev = (Get-Json "$Url/v1/devices")
Step "READY in $([math]::Round(((Get-Date) - $t0).TotalSeconds, 1)) s"
Write-Host ""
Write-Host "  Website   : http://localhost:$Port          (one site: home page, Login -> dashboard at /app/)"
Write-Host "  API docs  : $Url/docs"
Write-Host "  Mode      : $Mode   zones with AQI: $(@($z | Where-Object { $_.aqi }).Count)/6   devices: $($dev.count)"
if ($Mode -eq "live") {
    Write-Host "  Story     : sim clock 09:30 IST now; HITEC smoke episode starts ~10:00 IST (about $([math]::Round(30*60/$Speedup/60, 1)) min from now)"
} else {
    Write-Host "  Story     : data ends 10:50 IST, HITEC smoke anomaly in progress"
}
Write-Host "  Logins    : data\demo\credentials.txt   (official.demo = official, public.demo = public)"
Write-Host "  Stop      : .\infra\stop_demo.ps1"
if (-not $NoBrowser) { Start-Process "http://localhost:$Port" }
