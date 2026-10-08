# Shared helpers for the Aeronex demo scripts (dot-sourced by run_demo.ps1 / crash_test.ps1 / capture_evidence.ps1).
$Root = Split-Path -Parent $PSScriptRoot
$Py = Join-Path $Root "venv\Scripts\python.exe"
$DemoDir = Join-Path $Root "data\demo"
$LogDir = Join-Path $DemoDir "logs"
$PidFile = Join-Path $DemoDir "pids.json"
$DbPath = Join-Path $DemoDir "demo.db"
New-Item -ItemType Directory -Force $LogDir | Out-Null

function Save-Pid([string]$Name, [int]$ProcId) {
    $m = @{}
    if (Test-Path $PidFile) { (Get-Content $PidFile -Raw | ConvertFrom-Json).PSObject.Properties | ForEach-Object { $m[$_.Name] = $_.Value } }
    $m[$Name] = $ProcId
    ($m | ConvertTo-Json) | Set-Content $PidFile -Encoding ascii
}

function Start-Component([string]$Name, [string[]]$ArgList) {
    $p = Start-Process -FilePath $Py -ArgumentList $ArgList -WorkingDirectory $Root -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $LogDir "$Name.out.log") -RedirectStandardError (Join-Path $LogDir "$Name.err.log")
    Save-Pid $Name $p.Id
    return $p
}

function Wait-Until([scriptblock]$Test, [int]$TimeoutS, [string]$What) {
    $t0 = Get-Date
    while (((Get-Date) - $t0).TotalSeconds -lt $TimeoutS) {
        try { if (& $Test) { return $true } } catch { }
        Start-Sleep -Milliseconds 500
    }
    Write-Host "TIMEOUT waiting for: $What" -ForegroundColor Red
    return $false
}

$script:AuthHeaders = @{}
function Get-Json([string]$Url) { Invoke-RestMethod -Uri $Url -TimeoutSec 5 -Headers $script:AuthHeaders }

# The demo backend runs with AIRDNA_REQUIRE_AUTH=1, so the scripts sign in as the demo admin like any other client.
function Initialize-Auth([string]$BaseUrl) {
    $c = Get-Content (Join-Path $DemoDir "credentials.json") -Raw | ConvertFrom-Json
    $body = @{ username = "official.demo"; password = $c.accounts."official.demo".password } | ConvertTo-Json
    $r = Invoke-RestMethod -Uri "$BaseUrl/v1/auth/login" -Method Post -Body $body -ContentType "application/json" -TimeoutSec 10
    $script:AuthHeaders = @{ Authorization = "Bearer $($r.token)" }
}

function Start-Backend([int]$Port) {
    # NO --reload: the reloader keeps an orphan child alive and re-spawns on data/ writes under load.
    Start-Component "backend" @("-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1", "--port", "$Port", "--log-level", "warning") | Out-Null
}

function Start-Simulator([string]$Url, [double]$Speedup, [string]$Start) {
    Start-Component "simulator" @("-m", "simulator", "live", "--scenario", "demo", "--no-faults", "--offline-routes",
        "--url", $Url, "--start", $Start, "--speedup", "$Speedup") | Out-Null
}

# Run a python script to completion with stdout/stderr captured to logs (PowerShell 5.1 turns native stderr into
# terminating errors under $ErrorActionPreference='Stop', and the worker/simulator log to stderr). Returns the output lines.
function Invoke-Py([string[]]$ArgList, [string]$LogName) {
    $out = Join-Path $LogDir "$LogName.out.log"; $err = Join-Path $LogDir "$LogName.err.log"
    $p = Start-Process -FilePath $Py -ArgumentList $ArgList -WorkingDirectory $Root -NoNewWindow -Wait -PassThru `
        -RedirectStandardOutput $out -RedirectStandardError $err
    if ($p.ExitCode -ne 0) { throw "$LogName failed (exit $($p.ExitCode)); see $err" }
    return (Get-Content $out)
}
