# Infrastructure (Bicep)

[main.bicep](main.bicep) provisions everything for the agentic-RAG demo, including
the private networking required to reach Cosmos DB.

## Resources

| Resource | Type | Notes |
|----------|------|-------|
| Azure AI Search | `Microsoft.Search/searchServices` | SKU `standard`, semantic search `standard`, system-assigned identity |
| Cosmos DB (NoSQL) | `Microsoft.DocumentDB/databaseAccounts` | Serverless; database `insurance` with `policies`, `claims`, `customers` containers. **`publicNetworkAccess: Disabled` + `disableLocalAuth: true`** (see governance note) |
| Storage account | `Microsoft.Storage/storageAccounts` | Holds KB source docs (`kb-docs`) and the governed `ontology` blob |
| Application Insights | `Microsoft.Insights/components` | Tracing for the agent API |
| App Service Plan | `Microsoft.Web/serverfarms` | Linux **B1** (Basic) — Basic+ is required for regional VNet integration |
| Web App (agent API) | `Microsoft.Web/sites` | Python 3.13, system-assigned identity, **regional VNet integration**, app settings pre-wired |
| Virtual Network | `Microsoft.Network/virtualNetworks` | `snet-pe` (private endpoints) + `snet-app` (delegated to `Microsoft.Web/serverFarms`) |
| Cosmos private endpoint | `Microsoft.Network/privateEndpoints` | `Sql` group; gives the app a private path to Cosmos |
| Private DNS zone | `Microsoft.Network/privateDnsZones` | `privatelink.documents.azure.com`, linked to the VNet, with a DNS zone group on the endpoint |

Resources gated by `deployApp` (App Service plan/app, VNet, private endpoint, private
DNS, and app-scoped RBAC) are only deployed when `deployApp = true`.

## Governance note — Cosmos is private + keyless (enforced by policy)

This subscription enforces two Azure Policy **`modify`** effects on Cosmos DB accounts:

- `CosmosDB_PublicNetwork_Modify` → forces `publicNetworkAccess = Disabled`
- `CosmosDB_LocalAuth_Modify` → forces `disableLocalAuth = true` (Entra-only, no keys)

Because public access cannot be enabled, the App Service reaches Cosmos over a
**private endpoint** through **regional VNet integration**. The app sets
`WEBSITE_VNET_ROUTE_ALL=1` and `WEBSITE_DNS_SERVER=168.63.129.16` so
`privatelink.documents.azure.com` resolves to the private endpoint. The Bicep
declares the enforced Cosmos flags so the template stays aligned (no drift).

> **Local dev / data-load scripts cannot reach Cosmos directly** — public access is
> policy-disabled. Run them from inside the VNet (VM/jumpbox), over a VPN/Bastion into
> the VNet, or through a private endpoint reachable from your machine.

## Regions

Cosmos, the App Service plan/app, the VNet, and the private endpoint deploy to
`appLocation` (defaults to `cosmosLocation`, e.g. `westus3`). Search, Storage, and
Application Insights use `location` (the resource group region). `westus3` is used
for Cosmos/compute because East US is capacity-constrained for new serverless Cosmos
accounts and had no App Service quota.

## Role assignments

| Principal | Role | Scope |
|-----------|------|-------|
| Web App identity | Search Index Data Reader | Search service |
| Web App identity | Cosmos DB Built-in Data Contributor | Cosmos account (data-plane) |
| Web App identity | Storage Blob Data Reader | Storage account (ontology blob) |
| Web App identity | Cognitive Services OpenAI User | Foundry account (invoke models/agents) |
| Web App identity | Cognitive Services Speech User | Foundry account (keyless Speech tokens) |
| Web App identity | Cognitive Services User | Foundry account (Voice Live) |
| Search identity | Storage Blob Data Reader | Storage account (keyless indexer data source) |
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
param cosmosLocation         = 'westus3'   // appLocation follows this by default
param deployApp              = true
```

Networking address space is configurable via `vnetAddressPrefix`,
`privateEndpointSubnetPrefix`, and `appSubnetPrefix`.

## Deploy

```powershell
az group create -n rg-insurance-rag -l eastus
az deployment group create -g rg-insurance-rag -f infra/main.bicep -p infra/main.bicepparam
```

## Outputs

`apiAppName`, `apiBaseUrl`, `searchEndpoint`, `searchServiceName`, `cosmosAccountName`,
`cosmosEndpoint`, `appPrincipalId`, `storageAccountName`, `storageBlobEndpoint`,
`storageResourceId`, `kbBlobContainerName`, `vnetName`, `cosmosPrivateEndpointName` —
used by the data-load and app-deploy scripts.
