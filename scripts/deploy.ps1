$ErrorActionPreference = 'Stop'
$benchmarkRoot = (Split-Path -Parent $PSScriptRoot).Replace('\','/')
if ($benchmarkRoot -notmatch '^([A-Za-z]):/(.*)$') { throw 'Use a local drive path.' }
$benchmarkLinuxRoot = '/mnt/' + $Matches[1].ToLowerInvariant() + '/' + $Matches[2]
& wsl -d Ubuntu-24.04 -u root -- bash "$benchmarkLinuxRoot/scripts/with_wsl_proxy.sh" bash -c 'apt-get update -qq && apt-get install -y --no-install-recommends libnss3 libnspr4 libasound2t64'
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& wsl -d Ubuntu-24.04 -- bash "$benchmarkLinuxRoot/scripts/with_wsl_proxy.sh" bash "$benchmarkLinuxRoot/scripts/deploy_local.sh"
exit $LASTEXITCODE
