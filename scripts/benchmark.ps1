param(
    [ValidateSet('doctor','list','run','compare','replay')][string]$Command = 'doctor',
    [ValidateSet('gameworld','arc3','aigamestore')][string]$Profile = 'gameworld',
    [string]$Game = '01_2048',
    [string]$Task = '01_01',
    [string]$Model = 'deepseek-flash',
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
$benchmarkLinuxHome = (& wsl -d Ubuntu-24.04 -- bash -lc 'printf %s "$HOME"').Trim()
if (-not $benchmarkLinuxHome) { throw 'Could not resolve the WSL home directory.' }
$benchmarkLinuxPython = "$benchmarkLinuxHome/.venvs/vista-benchmark/bin/python"
if ($env:VISTA_BENCH_PYTHON) { $benchmarkLinuxPython = $env:VISTA_BENCH_PYTHON }
if (-not $Output) {
    $Output = Join-Path $benchmarkRoot ('runs\' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + $Profile)
}
$benchmarkLinuxOutput = ConvertTo-BenchmarkWslPath $Output
$benchmarkArgs = @(
    $benchmarkLinuxPython, '-m', 'vista.localbench', $Command,
    '--profile', $Profile, '--game', $Game, '--task', $Task, '--model', $Model,
    '--seed', $Seed, '--max-steps', $MaxSteps, '--max-calls', $MaxCalls,
    '--max-tokens', $MaxTokens, '--task-timeout', $TaskTimeout, '--strategy', $Strategy, '--thinking', $Thinking,
    '--reliability', $Reliability, '--memory-mode', $MemoryMode, '--inspection', $Inspection,
    '--inspection-budget', $InspectionBudget, '--inspection-view-budget', $InspectionViewBudget,
    '--stall-threshold', $StallThreshold, '--recovery-limit', $RecoveryLimit,
    '--clock-mode', $ClockMode,
    '--output', $benchmarkLinuxOutput
)
foreach ($benchmarkEnvFile in $ModelEnvFile) {
    if (-not (Test-Path -LiteralPath $benchmarkEnvFile -PathType Leaf)) {
        throw "Model environment file not found: $benchmarkEnvFile"
    }
    $benchmarkArgs += @('--env-file', (ConvertTo-BenchmarkWslPath $benchmarkEnvFile))
}
if ($ProbeModel) { $benchmarkArgs += '--probe-model' }
& wsl -d Ubuntu-24.04 -- bash "$benchmarkLinuxRoot/scripts/with_wsl_proxy.sh" @benchmarkArgs
exit $LASTEXITCODE
