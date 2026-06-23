param(
    [Parameter(Mandatory = $true)]
    [string]$ResourceGroup,

    [Parameter(Mandatory = $true)]
    [string]$ApiAppName
)

$ErrorActionPreference = 'Stop'

$repoRoot = Resolve-Path "$PSScriptRoot\..\.."
$buildDir = Join-Path $repoRoot '.artifacts'
$staging = Join-Path $buildDir 'api-staging'
$zip = Join-Path $buildDir 'api.zip'

New-Item -ItemType Directory -Path $buildDir -Force | Out-Null
if (Test-Path $staging) { Remove-Item $staging -Recurse -Force }
if (Test-Path $zip) { Remove-Item $zip -Force }
New-Item -ItemType Directory -Path $staging | Out-Null

Copy-Item -Path (Join-Path $repoRoot 'src') -Destination $staging -Recurse
Copy-Item -Path (Join-Path $repoRoot 'requirements.txt') -Destination $staging

Compress-Archive -Path (Join-Path $staging '*') -DestinationPath $zip

Write-Host "Deploying API package to $ApiAppName ..."
az webapp deploy --resource-group $ResourceGroup --name $ApiAppName --src-path $zip --type zip

Write-Host 'Application deployment completed.'
Write-Host "Health check: https://$ApiAppName.azurewebsites.net/health"
