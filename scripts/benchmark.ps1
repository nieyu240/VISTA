param(
    [ValidateSet('doctor','list','run','compare','replay','report')][string]$Command = 'doctor',
    [ValidateSet('gameworld','arc3','aigamestore')][string]$Profile = 'gameworld',
    [string]$Game = '01_2048',
    [string]$Task = '01_01',
    [string]$Model = '',
    [string]$Distribution = 'Ubuntu-24.04',
    [string]$PythonPath = '',
    [int]$Seed = 42,
    [int]$MaxSteps = 100,
    [int]$MaxCalls = 150,
    [int]$MaxTokens = 2048,
    [int]$TaskTimeout = 1200,
    [ValidateSet('baseline','vista')][string]$Strategy = 'vista',
    [ValidateSet('enabled','disabled')][string]$Thinking = 'disabled',
    [ValidateSet('legacy','ledger','verified','reliable')][string]$Reliability = 'reliable',
    [ValidateSet('legacy','current','summary','visual')][string]$MemoryMode = 'visual',
    [ValidateSet('off','fixed','agent')][string]$Inspection = 'agent',
    [int]$InspectionBudget = 8,
    [int]$InspectionViewBudget = 24,
    [int]$StallThreshold = 3,
    [int]$RecoveryLimit = 6,
    [ValidateSet('paused','wall','token')][string]$ClockMode = 'paused',
    [string[]]$ModelEnvFile = @(),
    [string]$Output,
    [switch]$ProbeModel
)
$ErrorActionPreference = 'Stop'
function ConvertTo-BenchmarkWslPath([string]$Path) {
    $benchmarkFullPath = [IO.Path]::GetFullPath($Path).Replace('\','/')
    if ($benchmarkFullPath -notmatch '^([A-Za-z]):/(.*)$') { throw 'Use a local drive path.' }
    return '/mnt/' + $Matches[1].ToLowerInvariant() + '/' + $Matches[2]
}
$benchmarkRoot = Split-Path -Parent $PSScriptRoot
$benchmarkLinuxRoot = ConvertTo-BenchmarkWslPath $benchmarkRoot
$benchmarkLinuxVenv = (& wsl -d $Distribution -- bash -lc 'printf %s "${VISTA_BENCH_VENV:-$HOME/.venvs/vista-benchmark}"').Trim()
if ($LASTEXITCODE -ne 0 -or -not $benchmarkLinuxVenv) { throw 'Could not resolve the WSL Python environment. Run scripts/deploy.ps1 first.' }
$benchmarkLinuxPython = "$benchmarkLinuxVenv/bin/python"
if ($env:VISTA_BENCH_PYTHON) { $benchmarkLinuxPython = $env:VISTA_BENCH_PYTHON }
if ($PythonPath) { $benchmarkLinuxPython = $PythonPath }
if ($Command -eq 'report') {
    & wsl -d $Distribution -- $benchmarkLinuxPython "$benchmarkLinuxRoot/scripts/report_results.py"
    exit $LASTEXITCODE
}
if (-not $Output) {
    $Output = Join-Path $benchmarkRoot ('runs\' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + $Profile)
}
$benchmarkLinuxOutput = ConvertTo-BenchmarkWslPath $Output
$benchmarkArgs = @(
    $benchmarkLinuxPython, '-m', 'vista.localbench', $Command,
    '--profile', $Profile, '--game', $Game, '--task', $Task,
    '--seed', $Seed, '--max-steps', $MaxSteps, '--max-calls', $MaxCalls,
    '--max-tokens', $MaxTokens, '--task-timeout', $TaskTimeout, '--strategy', $Strategy, '--thinking', $Thinking,
    '--reliability', $Reliability, '--memory-mode', $MemoryMode, '--inspection', $Inspection,
    '--inspection-budget', $InspectionBudget, '--inspection-view-budget', $InspectionViewBudget,
    '--stall-threshold', $StallThreshold, '--recovery-limit', $RecoveryLimit,
    '--clock-mode', $ClockMode,
    '--output', $benchmarkLinuxOutput
)
if ($Model) { $benchmarkArgs += @('--model', $Model) }
foreach ($benchmarkEnvFile in $ModelEnvFile) {
    if (-not (Test-Path -LiteralPath $benchmarkEnvFile -PathType Leaf)) {
        throw "Model environment file not found: $benchmarkEnvFile"
    }
    $benchmarkArgs += @('--env-file', (ConvertTo-BenchmarkWslPath $benchmarkEnvFile))
}
if ($ProbeModel) { $benchmarkArgs += '--probe-model' }
& wsl -d $Distribution -- bash "$benchmarkLinuxRoot/scripts/with_wsl_proxy.sh" @benchmarkArgs
exit $LASTEXITCODE
