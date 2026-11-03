param(
    [string]$ClusterName = 'argos-local',
    [switch]$SkipKindLoad
)

$ErrorActionPreference = 'Stop'

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$images = @(
    @{ Name = 'argos-main-api:local'; Dockerfile = 'apps/main_api/Dockerfile' },
    @{ Name = 'argos-mcp-data:local'; Dockerfile = 'apps/mcp_data/Dockerfile' },
    @{ Name = 'argos-mcp-prediction:local'; Dockerfile = 'apps/mcp_prediction/Dockerfile' },
    @{ Name = 'argos-mcp-email:local'; Dockerfile = 'apps/mcp_email/Dockerfile' },
    @{ Name = 'argos-a2a-agent:local'; Dockerfile = 'apps/a2a_agent/Dockerfile' },
    @{ Name = 'argos-postgres-init:local'; Dockerfile = 'infrastructure/docker/postgres/Dockerfile.local' }
)

foreach ($image in $images) {
    docker build --tag $image.Name --file (Join-Path $repoRoot $image.Dockerfile) $repoRoot
    if ($LASTEXITCODE -ne 0) { throw "Falha ao construir $($image.Name)" }
}

if (-not $SkipKindLoad) {
    foreach ($image in $images) {
        kind load docker-image --name $ClusterName $image.Name
        if ($LASTEXITCODE -ne 0) { throw "Falha ao carregar $($image.Name) no cluster Kind" }
    }
}
