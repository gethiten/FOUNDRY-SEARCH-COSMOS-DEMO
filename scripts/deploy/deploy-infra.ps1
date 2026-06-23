param(
    [Parameter(Mandatory = $true)]
    [string]$ResourceGroup,

    [string]$Location = 'uswest3',

    [string]$TemplateFile = 'infra/main.bicep',

    [string]$ParametersFile = 'infra/main.bicepparam'
)

$ErrorActionPreference = 'Stop'

Write-Host "Ensuring resource group $ResourceGroup exists in $Location ..."
az group create --name $ResourceGroup --location $Location | Out-Null

Write-Host "Deploying infrastructure (Search + Cosmos + App Service) ..."
az deployment group create `
    --resource-group $ResourceGroup `
    --template-file $TemplateFile `
    --parameters $ParametersFile `
    --query "properties.outputs" -o json

Write-Host 'Infrastructure deployment completed.'
Write-Host 'Next: run scripts/load_cosmos_data.py and scripts/setup_search_index.py, then deploy-app.ps1.'
