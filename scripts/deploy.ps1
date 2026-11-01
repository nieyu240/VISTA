param([string]$Distribution = 'Ubuntu-24.04')
$ErrorActionPreference = 'Stop'
$benchmarkRoot = (Split-Path -Parent $PSScriptRoot).Replace('\','/')
if ($benchmarkRoot -notmatch '^([A-Za-z]):/(.*)$') { throw 'Use a local drive path.' }
$benchmarkLinuxRoot = '/mnt/' + $Matches[1].ToLowerInvariant() + '/' + $Matches[2]
& wsl -d $Distribution -u root -- bash "$benchmarkLinuxRoot/scripts/with_wsl_proxy.sh" bash -c 'apt-get update -qq && apt-get install -y --no-install-recommends git ca-certificates python3.12-venv libnss3 libnspr4 libasound2t64 libatk1.0-0t64 libatk-bridge2.0-0t64 libcups2t64 libdrm2 libdbus-1-3 libxkbcommon0 libx11-6 libxcomposite1 libxdamage1 libxext6 libxfixes3 libxrandr2 libgbm1 libpango-1.0-0 libcairo2 fonts-liberation'
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& wsl -d $Distribution -- bash "$benchmarkLinuxRoot/scripts/with_wsl_proxy.sh" bash "$benchmarkLinuxRoot/scripts/deploy_local.sh"
exit $LASTEXITCODE
