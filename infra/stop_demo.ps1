<#
.SYNOPSIS  Stop every Aeronex demo process and free ports 8000 / 8501. Safe to run when nothing is running.
.DESCRIPTION
  Kills (process trees) the PIDs recorded by run_demo.ps1, then sweeps for strays: anything listening on the demo
  ports, and any python/streamlit/uvicorn process of THIS repo running the simulator, worker, backend or dashboard.
  The sweep exists because `uvicorn --reload` leaves an orphaned server child if only the parent is killed.
#>
param([int[]]$Ports = @(8000, 8501))
$ErrorActionPreference = "SilentlyContinue"
$Root = Split-Path -Parent $PSScriptRoot
$PidFile = Join-Path $Root "data\demo\pids.json"
$killed = @()

function Stop-Tree([int]$ProcId, [string]$Why) {
    if ($ProcId -and (Get-Process -Id $ProcId -ErrorAction SilentlyContinue)) {
        & taskkill.exe /PID $ProcId /T /F | Out-Null
        $script:killed += "$ProcId ($Why)"
    }
}

if (Test-Path $PidFile) {
    # supervisor first, or it would resurrect the components we are about to stop
    $recorded = (Get-Content $PidFile -Raw | ConvertFrom-Json).PSObject.Properties | Sort-Object { $_.Name -ne "supervisor" }
    foreach ($e in $recorded) { Stop-Tree $e.Value $e.Name }
    Remove-Item $PidFile -Force
}
# sweep supervisors before anything else for the same reason
Get-CimInstance Win32_Process | Sort-Object { $_.CommandLine -notmatch 'supervisor\.ps1' } | Where-Object {
    $_.ProcessId -ne $PID -and $_.CommandLine -and $_.CommandLine.Contains($Root) -and
    $_.CommandLine -match '(-m simulator|-m worker|uvicorn|streamlit|dashboard\.serve|supervisor\.ps1)'
} | ForEach-Object { Stop-Tree $_.ProcessId "stray" }
foreach ($port in $Ports) {
    Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Tree $_.OwningProcess "port $port" }
}
Start-Sleep -Milliseconds 700
$busy = $Ports | Where-Object { Get-NetTCPConnection -LocalPort $_ -State Listen -ErrorAction SilentlyContinue }
if ($killed) { Write-Host "Stopped: $($killed -join ', ')" } else { Write-Host "Nothing was running." }
if ($busy) { Write-Host "WARNING: ports still busy: $($busy -join ', ')" -ForegroundColor Red; exit 1 }
Write-Host "Ports $($Ports -join ', ') are free."
