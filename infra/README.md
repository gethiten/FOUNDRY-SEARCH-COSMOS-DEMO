# Infrastructure (Bicep)

[main.bicep](main.bicep) provisions everything for the agentic-RAG demo.

## Resources

| Resource | Type | Notes |
|----------|------|-------|
| Azure AI Search | `Microsoft.Search/searchServices` | SKU `standard`, semantic search `standard`, system-assigned identity |
| Cosmos DB (NoSQL) | `Microsoft.DocumentDB/databaseAccounts` | Serverless; database `insurance` with `policies`, `claims`, `customers` containers |
| App Service Plan | `Microsoft.Web/serverfarms` | Linux B1 |
| Web App (agent API) | `Microsoft.Web/sites` | Python 3.13, system-assigned identity, app settings pre-wired |
| Application Insights | `Microsoft.Insights/components` | Tracing for the agent API |

## Role assignments

| Principal | Role | Scope |
|-----------|------|-------|
| Web App identity | Search Index Data Reader | Search service |
| Web App identity | Cosmos DB Built-in Data Contributor | Cosmos account (data-plane) |
| Web App identity | Cognitive Services OpenAI User | Foundry account |
| Search identity | Cognitive Services OpenAI User | Foundry account (integrated vectorization) |

> `foundryAccountName` must be the Cognitive Services / Azure OpenAI account that hosts
> `gpt-5-mini` and `text-embedding-3-large`. Leave it empty to skip the Foundry role
> assignments (e.g. if the Foundry account is in a different subscription).

## Parameters

Edit [main.bicepparam](main.bicepparam). Key values:

```bicep
param foundryProjectEndpoint = 'https://<foundry-account>.services.ai.azure.com/api/projects/<project>'
param foundryChatModel       = 'gpt-5-mini'
param embeddingModelName     = 'text-embedding-3-large'
param azureOpenAiEndpoint    = 'https://<foundry-account>.cognitiveservices.azure.com'
param foundryAccountName     = '<foundry-account>'
```

## Deploy

```powershell
az group create -n rg-insurance-rag -l eastus
az deployment group create -g rg-insurance-rag -f infra/main.bicep -p infra/main.bicepparam
```

## Outputs

`apiAppName`, `apiBaseUrl`, `searchEndpoint`, `searchServiceName`, `cosmosAccountName`,
`cosmosEndpoint`, `appPrincipalId` — used by the data-load and app-deploy scripts.
