[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$demoRoot = Split-Path -Parent $PSScriptRoot
$demoEnv = Join-Path $demoRoot '.env.demo'
$demoCompose = Join-Path $demoRoot 'docker/docker-compose.Demo.yml'

docker info --format '{{.ServerVersion}}' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Start Docker Desktop with Linux containers first.' }

if (-not (Test-Path -LiteralPath $demoEnv)) {
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $jwtBytes = New-Object byte[] 32
        $dbBytes = New-Object byte[] 24
        $rng.GetBytes($jwtBytes)
        $rng.GetBytes($dbBytes)
        $jwt = [BitConverter]::ToString($jwtBytes).Replace('-', '').ToLowerInvariant()
        $password = [BitConverter]::ToString($dbBytes).Replace('-', '').ToLowerInvariant()
        [System.IO.File]::WriteAllText($demoEnv, "JWT_SECRET_KEY=$jwt`nPOSTGRES_PASSWORD=$password`n")
    } finally { $rng.Dispose() }
}

docker compose --env-file $demoEnv -f $demoCompose --profile tunnel config --quiet
if ($LASTEXITCODE -ne 0) { throw 'Invalid demo configuration.' }
docker compose --env-file $demoEnv -f $demoCompose --profile tunnel up -d --build
if ($LASTEXITCODE -ne 0) { throw 'Demo startup failed. Inspect docker compose logs.' }
Write-Host 'API: http://localhost:8000/health | Test email: http://localhost:8025'
Write-Host 'Find your public HTTPS URL below; set API_PROXY_URL in Vercel and redeploy:'
docker compose --env-file $demoEnv -f $demoCompose logs --tail 40 tunnel
if ($LASTEXITCODE -ne 0) { throw 'Could not read tunnel logs.' }
