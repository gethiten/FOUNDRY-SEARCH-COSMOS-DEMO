using './main.bicep'

param namePrefix = 'insrag'
param foundryProjectEndpoint = 'https://rag-ai-demo-resource.services.ai.azure.com/api/projects/rag-ai-demo'
param foundryChatModel = 'gpt-5-mini'
param embeddingModelName = 'text-embedding-3-large'
param embeddingDeploymentName = 'text-embedding-3-large'
param azureOpenAiEndpoint = 'https://rag-ai-demo-resource.cognitiveservices.azure.com'

// Name of the existing Azure OpenAI / Foundry Cognitive Services account that
// hosts gpt-5-mini and text-embedding-3-large. Used for RBAC. Example below
// matches the project endpoint above; update if your account name differs.
param foundryAccountName = 'rag-ai-demo-resource'

param searchIndexName = 'insurance-kb'
param cosmosDatabaseName = 'insurance'
param kbBlobContainerName = 'kb-docs'

// Subscription has no App Service compute quota — skip the hosted API app and
// run the agent locally with uvicorn. Set to true once App Service quota is granted.
param deployApp = false

// East US is capacity-constrained for new serverless Cosmos accounts; deploy
// Cosmos to westus3 (Search/App Insights remain in the resource group's region).
param cosmosLocation = 'westus3'
