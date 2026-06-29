targetScope = 'resourceGroup'

@description('Azure region for all resources.')
param location string = resourceGroup().location

@description('Azure region for the Cosmos DB account. Defaults to location; override when a region is capacity-constrained.')
param cosmosLocation string = location

@description('Name prefix used for all deployed resources (lowercase letters/numbers).')
param namePrefix string = 'insrag'

@description('Linux App Service Python runtime for the agent API.')
param linuxFxVersion string = 'PYTHON|3.13'

@description('Foundry project endpoint used by the agent API app settings.')
param foundryProjectEndpoint string = ''

@description('Chat model deployment name used by the agent.')
param foundryChatModel string = 'gpt-5-mini'

@description('Embedding model name used for integrated vectorization.')
param embeddingModelName string = 'text-embedding-3-large'

@description('Embedding deployment name on the Azure OpenAI / Foundry account.')
param embeddingDeploymentName string = 'text-embedding-3-large'

@description('Azure OpenAI (Foundry) endpoint used by the Search vectorizer for integrated vectorization.')
param azureOpenAiEndpoint string = ''

@description('Existing Azure OpenAI / Foundry Cognitive Services account name (for RBAC). Leave empty to skip role assignments.')
param foundryAccountName string = ''

@description('Search index name.')
param searchIndexName string = 'insurance-kb'

@description('Blob container that holds the knowledge-base source documents the Search indexer reads from.')
param kbBlobContainerName string = 'kb-docs'

@description('Blob container that holds the governed ontology metadata (ontology.json) the API hot-reloads for deterministic routing.')
param ontologyBlobContainerName string = 'ontology'

@description('How often (seconds) the API polls the governed ontology blob for changes. 0 disables hot-reload.')
param ontologyReloadSeconds int = 30

@description('Cosmos DB database name.')
param cosmosDatabaseName string = 'insurance'

@description('Deploy the App Service plan + API app + app-scoped RBAC. Set false when the subscription has no App Service compute quota (run the agent locally instead).')
param deployApp bool = true

@description('Tags applied to all resources.')
param tags object = {
  solution: 'insurance-agentic-rag'
  workload: 'foundry-search-cosmos'
}

var searchServiceName = '${namePrefix}-search-${uniqueString(resourceGroup().id)}'
var cosmosAccountName = '${namePrefix}-cosmos-${uniqueString(resourceGroup().id)}'
var storageAccountName = '${namePrefix}st${uniqueString(resourceGroup().id)}'
var planName = '${namePrefix}-plan'
var apiAppName = '${namePrefix}-api-${uniqueString(resourceGroup().id)}'
var appInsightsName = '${namePrefix}-appi'

// Built-in role definition ids
var searchIndexDataReaderRoleId = '1407120a-92aa-4202-b7e9-c0e197c71c8f'
var cognitiveServicesOpenAiUserRoleId = '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'
var cosmosDataContributorRoleId = '00000000-0000-0000-0000-000000000002'
var storageBlobDataReaderRoleId = '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1'

resource search 'Microsoft.Search/searchServices@2024-06-01-preview' = {
  name: searchServiceName
  location: location
  tags: tags
  sku: {
    name: 'standard'
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    replicaCount: 1
    partitionCount: 1
    hostingMode: 'default'
    semanticSearch: 'standard'
    publicNetworkAccess: 'enabled'
    authOptions: {
      aadOrApiKey: {
        aadAuthFailureMode: 'http401WithBearerChallenge'
      }
    }
  }
}

resource cosmos 'Microsoft.DocumentDB/databaseAccounts@2024-11-15' = {
  name: cosmosAccountName
  location: cosmosLocation
  tags: tags
  kind: 'GlobalDocumentDB'
  properties: {
    databaseAccountOfferType: 'Standard'
    enableAutomaticFailover: false
    disableLocalAuth: false
    capabilities: [
      {
        name: 'EnableServerless'
      }
    ]
    consistencyPolicy: {
      defaultConsistencyLevel: 'Session'
    }
    locations: [
      {
        locationName: cosmosLocation
        failoverPriority: 0
        isZoneRedundant: false
      }
    ]
  }
}

resource cosmosDb 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2024-11-15' = {
  parent: cosmos
  name: cosmosDatabaseName
  properties: {
    resource: {
      id: cosmosDatabaseName
    }
  }
}

resource policiesContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-11-15' = {
  parent: cosmosDb
  name: 'policies'
  properties: {
    resource: {
      id: 'policies'
      partitionKey: {
        paths: [
          '/customerId'
        ]
        kind: 'Hash'
      }
    }
  }
}

resource claimsContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-11-15' = {
  parent: cosmosDb
  name: 'claims'
  properties: {
    resource: {
      id: 'claims'
      partitionKey: {
        paths: [
          '/policyId'
        ]
        kind: 'Hash'
      }
    }
  }
}

resource customersContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-11-15' = {
  parent: cosmosDb
  name: 'customers'
  properties: {
    resource: {
      id: 'customers'
      partitionKey: {
        paths: [
          '/customerId'
        ]
        kind: 'Hash'
      }
    }
  }
}

// Storage account + blob container that hold the knowledge-base source
// documents. The Search indexer pulls these blobs, chunks them, generates
// embeddings (integrated vectorization), and projects chunks into the index.
resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageAccountName
  location: location
  tags: tags
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    accessTier: 'Hot'
    allowBlobPublicAccess: false
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
}

resource kbContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: kbBlobContainerName
  properties: {
    publicAccess: 'None'
  }
}

// Governed ontology metadata container. The API reads ontology.json from here
// (keyless, via managed identity) and hot-reloads it without a restart.
resource ontologyContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: ontologyBlobContainerName
  properties: {
    publicAccess: 'None'
  }
}

// Search service managed identity -> read knowledge-base blobs (keyless data source)
resource searchStorageReaderRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, search.id, storageBlobDataReaderRoleId)
  scope: storage
  properties: {
    roleDefinitionId: resourceId('Microsoft.Authorization/roleDefinitions', storageBlobDataReaderRoleId)
    principalId: search.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: appInsightsName
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    IngestionMode: 'ApplicationInsights'
  }
}

resource plan 'Microsoft.Web/serverfarms@2023-12-01' = if (deployApp) {
  name: planName
  location: location
  tags: tags
  sku: {
    name: 'F1'
    tier: 'Free'
  }
  kind: 'linux'
  properties: {
    reserved: true
  }
}

resource apiApp 'Microsoft.Web/sites@2023-12-01' = if (deployApp) {
  name: apiAppName
  location: location
  tags: tags
  kind: 'app,linux'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: linuxFxVersion
      appCommandLine: 'python -m uvicorn insurance_rag_agent.main:app --app-dir src --host 0.0.0.0 --port 8000'
      appSettings: [
        {
          name: 'WEBSITES_PORT'
          value: '8000'
        }
        {
          name: 'SCM_DO_BUILD_DURING_DEPLOYMENT'
          value: 'true'
        }
        {
          name: 'PYTHONPATH'
          value: '/home/site/wwwroot/src'
        }
        {
          name: 'APP_ENV'
          value: 'prod'
        }
        {
          name: 'FOUNDRY_PROJECT_ENDPOINT'
          value: foundryProjectEndpoint
        }
        {
          name: 'FOUNDRY_CHAT_MODEL'
          value: foundryChatModel
        }
        {
          name: 'FOUNDRY_EMBEDDING_MODEL'
          value: embeddingModelName
        }
        {
          name: 'AZURE_SEARCH_ENDPOINT'
          value: 'https://${search.name}.search.windows.net'
        }
        {
          name: 'AZURE_SEARCH_INDEX'
          value: searchIndexName
        }
        {
          name: 'AZURE_OPENAI_ENDPOINT'
          value: azureOpenAiEndpoint
        }
        {
          name: 'AZURE_OPENAI_EMBEDDING_DEPLOYMENT'
          value: embeddingDeploymentName
        }
        {
          name: 'COSMOS_ENDPOINT'
          value: cosmos.properties.documentEndpoint
        }
        {
          name: 'COSMOS_DATABASE'
          value: cosmosDatabaseName
        }
        {
          name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
          value: appInsights.properties.ConnectionString
        }
        {
          name: 'OTEL_SERVICE_NAME'
          value: 'insurance-rag-api'
        }
        {
          name: 'ONTOLOGY_BLOB_URL'
          value: '${storage.properties.primaryEndpoints.blob}${ontologyBlobContainerName}/ontology.json'
        }
        {
          name: 'ONTOLOGY_RELOAD_SECONDS'
          value: string(ontologyReloadSeconds)
        }
      ]
    }
  }
}

resource foundryAccount 'Microsoft.CognitiveServices/accounts@2024-10-01' existing = if (!empty(foundryAccountName)) {
  name: foundryAccountName
}

// App -> read documents from the Search index
resource appSearchReaderRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (deployApp) {
  name: guid(search.id, apiApp!.id, searchIndexDataReaderRoleId)
  scope: search
  properties: {
    roleDefinitionId: resourceId('Microsoft.Authorization/roleDefinitions', searchIndexDataReaderRoleId)
    principalId: apiApp!.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// App -> read the governed ontology blob (hot-reloaded by the API, keyless)
resource appStorageReaderRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (deployApp) {
  name: guid(storage.id, apiApp!.id, storageBlobDataReaderRoleId)
  scope: storage
  properties: {
    roleDefinitionId: resourceId('Microsoft.Authorization/roleDefinitions', storageBlobDataReaderRoleId)
    principalId: apiApp!.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// App -> invoke Foundry models / agents
resource appFoundryUserRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(foundryAccountName) && deployApp) {
  name: guid(foundryAccount.id, apiApp!.id, cognitiveServicesOpenAiUserRoleId)
  scope: foundryAccount
  properties: {
    roleDefinitionId: resourceId('Microsoft.Authorization/roleDefinitions', cognitiveServicesOpenAiUserRoleId)
    principalId: apiApp!.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// Search service -> call Azure OpenAI embeddings for integrated vectorization
resource searchFoundryUserRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(foundryAccountName)) {
  name: guid(foundryAccount.id, search.id, cognitiveServicesOpenAiUserRoleId)
  scope: foundryAccount
  properties: {
    roleDefinitionId: resourceId('Microsoft.Authorization/roleDefinitions', cognitiveServicesOpenAiUserRoleId)
    principalId: search.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// App -> Cosmos DB data-plane read/write (data containers)
resource appCosmosDataRole 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-11-15' = if (deployApp) {
  parent: cosmos
  name: guid(cosmos.id, apiApp!.id, cosmosDataContributorRoleId)
  properties: {
    roleDefinitionId: '${cosmos.id}/sqlRoleDefinitions/${cosmosDataContributorRoleId}'
    principalId: apiApp!.identity.principalId
    scope: cosmos.id
  }
}

output apiAppName string = deployApp ? apiApp!.name : ''
output apiBaseUrl string = deployApp ? 'https://${apiApp!.properties.defaultHostName}' : ''
output searchEndpoint string = 'https://${search.name}.search.windows.net'
output searchServiceName string = search.name
output cosmosAccountName string = cosmos.name
output cosmosEndpoint string = cosmos.properties.documentEndpoint
output appPrincipalId string = deployApp ? apiApp!.identity.principalId : ''
output storageAccountName string = storage.name
output storageBlobEndpoint string = storage.properties.primaryEndpoints.blob
output storageResourceId string = storage.id
output kbBlobContainerName string = kbContainer.name
