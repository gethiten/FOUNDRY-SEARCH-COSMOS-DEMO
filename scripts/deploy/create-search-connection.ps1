# Creates a Foundry project *connection* to the Azure AI Search service so a
# Foundry-hosted agent can use the Azure AI Search tool. Prints the connection
# id to paste into AZURE_SEARCH_CONNECTION_ID (used by scripts/setup_kb_agent.py).
#
# Run AFTER deploy-infra.ps1 (and after the Foundry project exists).
param(
    [Parameter(Mandatory = $true)]
    [string]$SearchServiceName,

    [Parameter(Mandatory = $true)]
    [string]$SearchResourceGroup,

    [string]$FoundryAccountName = 'rag-ai-demo-resource',

    [string]$FoundryProjectName = 'rag-ai-demo',

    [Parameter(Mandatory = $true)]
    [string]$FoundryResourceGroup,

    [string]$ConnectionName = 'insurance-search-conn',

    [string]$ApiVersion = '2025-04-01-preview'
)

$ErrorActionPreference = 'Stop'

$subId = az account show --query id -o tsv
$searchEndpoint = "https://$SearchServiceName.search.windows.net"
$searchResourceId = "/subscriptions/$subId/resourceGroups/$SearchResourceGroup/providers/Microsoft.Search/searchServices/$SearchServiceName"

$connId = "/subscriptions/$subId/resourceGroups/$FoundryResourceGroup/providers/Microsoft.CognitiveServices/accounts/$FoundryAccountName/projects/$FoundryProjectName/connections/$ConnectionName"

# Entra ID (AAD) auth — the Foundry project's managed identity must have
# "Search Index Data Reader" on the Search service (grant separately if needed).
$body = @{
    properties = @{
        category      = 'CognitiveSearch'
        target        = $searchEndpoint
        authType      = 'AAD'
        isSharedToAll = $true
        metadata      = @{
            ApiType    = 'Azure'
            ResourceId = $searchResourceId
        }
    }
} | ConvertTo-Json -Depth 6

$uri = "https://management.azure.com$connId" + "?api-version=$ApiVersion"

# Pass the JSON body via a temp file. Inline --body strings get quote-mangled by
# PowerShell, which makes the service reject the request with 415 Unsupported
# Media Type.
$bodyFile = New-TemporaryFile
Set-Content -Path $bodyFile -Value $body -Encoding utf8

Write-Host "Creating Foundry project connection '$ConnectionName' -> $searchEndpoint ..."
az rest --method put --uri $uri --body "@$bodyFile" --headers "Content-Type=application/json" | Out-Null
Remove-Item $bodyFile -Force

# --- Grant the Foundry project (or account) managed identity read access to the
#     Search index, so the Azure AI Search tool can query it via Entra ID. ---
$projectUri = "https://management.azure.com/subscriptions/$subId/resourceGroups/$FoundryResourceGroup/providers/Microsoft.CognitiveServices/accounts/$FoundryAccountName/projects/$FoundryProjectName?api-version=$ApiVersion"
$principalId = az rest --method get --uri $projectUri --query "identity.principalId" -o tsv 2>$null

if ([string]::IsNullOrWhiteSpace($principalId) -or $principalId -eq 'null') {
    Write-Host "Project has no system-assigned identity; falling back to the account identity ..."
    $principalId = az cognitiveservices account show --name $FoundryAccountName --resource-group $FoundryResourceGroup --query "identity.principalId" -o tsv 2>$null
}

if ([string]::IsNullOrWhiteSpace($principalId) -or $principalId -eq 'null') {
    Write-Warning "Could not resolve a managed-identity principalId for the Foundry project/account."
    Write-Warning "Enable a system-assigned identity on the Foundry resource, then grant 'Search Index Data Reader' manually."
} else {
    Write-Host "Granting 'Search Index Data Reader' to Foundry identity $principalId on the Search service ..."
    az role assignment create `
        --assignee-object-id $principalId `
        --assignee-principal-type ServicePrincipal `
        --role "Search Index Data Reader" `
        --scope $searchResourceId | Out-Null
}

Write-Host ''
Write-Host 'Connection created. Set this in your .env:'
Write-Host "AZURE_SEARCH_CONNECTION_ID=$connId"
