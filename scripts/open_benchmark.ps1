param(
    [ValidateRange(1,65535)][int]$Port = 8765,
    [string]$Distribution = 'Ubuntu-24.04'
)
$ErrorActionPreference = 'Stop'
$benchmarkRoot = Split-Path -Parent $PSScriptRoot
$benchmarkFullPath = $benchmarkRoot.Replace('\','/')
if ($benchmarkFullPath -notmatch '^([A-Za-z]):/(.*)$') { throw 'Use a local drive path.' }
$benchmarkLinuxRoot = '/mnt/' + $Matches[1].ToLowerInvariant() + '/' + $Matches[2]
$benchmarkUrl = "http://localhost:$Port"
$benchmarkRunning = $false
try { $benchmarkRunning = (Invoke-WebRequest -UseBasicParsing "$benchmarkUrl/api/runs" -TimeoutSec 2).StatusCode -eq 200 } catch {}
if (-not $benchmarkRunning) {
    New-Item -ItemType Directory -Path (Join-Path $benchmarkRoot 'reports\deployment') -Force | Out-Null
    $benchmarkLog = Join-Path $benchmarkRoot 'reports\deployment\viewer.log'
    $benchmarkErrorLog = Join-Path $benchmarkRoot 'reports\deployment\viewer.stderr.log'
    Start-Process -FilePath 'wsl.exe' -ArgumentList @('-d',$Distribution,'--','python3',('"' + "$benchmarkLinuxRoot/scripts/serve_benchmark.py" + '"'),'--port',$Port) -WindowStyle Hidden -RedirectStandardOutput $benchmarkLog -RedirectStandardError $benchmarkErrorLog
    for ($benchmarkAttempt = 0; $benchmarkAttempt -lt 20; $benchmarkAttempt++) {
        Start-Sleep -Milliseconds 500
        try { $benchmarkRunning = (Invoke-WebRequest -UseBasicParsing "$benchmarkUrl/api/runs" -TimeoutSec 2).StatusCode -eq 200 } catch {}
        if ($benchmarkRunning) { break }
    }
    if (-not $benchmarkRunning) { throw "Benchmark viewer did not start. See $benchmarkErrorLog" }
}
Start-Process $benchmarkUrl
