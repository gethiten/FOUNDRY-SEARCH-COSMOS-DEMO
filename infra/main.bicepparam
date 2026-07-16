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

// Voice Live (real-time speech-to-speech) targets the orchestrator agent in
// this Foundry project. Leave the agent/version/voice defaults as-is.
param voiceLiveProjectName = 'rag-ai-demo'

param searchIndexName = 'insurance-kb'
param cosmosDatabaseName = 'insurance'
param kbBlobContainerName = 'kb-docs'

// App Service compute is deployed. The plan is Basic (B1) in the Cosmos region
// (westus3) so regional VNet integration + the Cosmos private endpoint are
// co-located. Set to false to skip App Service and run the API locally.
param deployApp = true

// East US is capacity-constrained for new serverless Cosmos accounts; deploy
// Cosmos to westus3 (Search/App Insights/Storage remain in the resource
// group's region). The App Service, VNet, and private endpoint follow Cosmos
// into westus3 via appLocation (defaults to cosmosLocation).
param cosmosLocation = 'westus3'
