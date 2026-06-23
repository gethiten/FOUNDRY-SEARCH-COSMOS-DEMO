# Grants the *current signed-in user* the data-plane roles needed to run the
# local data-load and indexing scripts (load_cosmos_data.py, setup_search_index.py).
#
# Run AFTER deploy-infra.ps1.
param(
    [Parameter(Mandatory = $true)]
    [string]$ResourceGroup,

    [Parameter(Mandatory = $true)]
    [string]$SearchServiceName,

    [Parameter(Mandatory = $true)]
    [string]$CosmosAccountName,

    [string]$StorageAccountName = '',

    [string]$FoundryAccountName = 'rag-ai-demo-resource',

    [string]$FoundryResourceGroup = ''
)

$ErrorActionPreference = 'Stop'

$me = az ad signed-in-user show --query id -o tsv
$subId = az account show --query id -o tsv
Write-Host "Granting data-plane roles to principal $me ..."

# --- Azure AI Search: Search Index Data Contributor (upload docs) ---
$searchScope = "/subscriptions/$subId/resourceGroups/$ResourceGroup/providers/Microsoft.Search/searchServices/$SearchServiceName"
az role assignment create --assignee $me --role "Search Index Data Contributor" --scope $searchScope | Out-Null
az role assignment create --assignee $me --role "Search Service Contributor" --scope $searchScope | Out-Null

# --- Cosmos DB: SQL built-in Data Contributor (data-plane) ---
$cosmosDataContribId = "/subscriptions/$subId/resourceGroups/$ResourceGroup/providers/Microsoft.DocumentDB/databaseAccounts/$CosmosAccountName/sqlRoleDefinitions/00000000-0000-0000-0000-000000000002"
$cosmosScope = "/subscriptions/$subId/resourceGroups/$ResourceGroup/providers/Microsoft.DocumentDB/databaseAccounts/$CosmosAccountName"
az cosmosdb sql role assignment create `
    --account-name $CosmosAccountName `
    --resource-group $ResourceGroup `
    --role-definition-id $cosmosDataContribId `
    --principal-id $me `
    --scope $cosmosScope | Out-Null

# --- Azure OpenAI / Foundry: Cognitive Services OpenAI User (embeddings) ---
if (-not [string]::IsNullOrWhiteSpace($FoundryAccountName)) {
    $frg = if ([string]::IsNullOrWhiteSpace($FoundryResourceGroup)) { $ResourceGroup } else { $FoundryResourceGroup }
    $foundryScope = "/subscriptions/$subId/resourceGroups/$frg/providers/Microsoft.CognitiveServices/accounts/$FoundryAccountName"
    az role assignment create --assignee $me --role "Cognitive Services OpenAI User" --scope $foundryScope | Out-Null
}

# --- Azure Storage: Storage Blob Data Contributor (upload KB docs to blob) ---
if (-not [string]::IsNullOrWhiteSpace($StorageAccountName)) {
    $storageScope = "/subscriptions/$subId/resourceGroups/$ResourceGroup/providers/Microsoft.Storage/storageAccounts/$StorageAccountName"
    az role assignment create --assignee $me --role "Storage Blob Data Contributor" --scope $storageScope | Out-Null
}

Write-Host 'Data-plane role grants completed (allow ~5 min for propagation).'
