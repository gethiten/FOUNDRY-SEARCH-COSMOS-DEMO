# Setup Guide — Insurance Agentic-RAG

Step-by-step instructions to provision, load, deploy, and verify the demo.

For the technical design see [ARCHITECTURE.md](ARCHITECTURE.md). For a quick
overview see [README.md](README.md).

- [Prerequisites](#prerequisites)
- [Step 1 — Local environment](#step-1--local-environment)
- [Step 2 — Provision Azure infrastructure](#step-2--provision-azure-infrastructure)
- [Step 3 — Configure `.env`](#step-3--configure-env)
- [Step 4 — Grant your user data access](#step-4--grant-your-user-data-access)
- [Step 5 — Load the data](#step-5--load-the-data)
- [Step 6 — Smoke test the data plane](#step-6--smoke-test-the-data-plane)
- [Step 7 — Run / deploy the FastAPI app](#step-7--run--deploy-the-fastapi-app)
- [Step 8 — Register the Foundry prompt agents](#step-8--register-the-foundry-prompt-agents)
- [Step 9 — Deploy the two hosted agents](#step-9--deploy-the-two-hosted-agents)
- [Step 10 — End-to-end verification](#step-10--end-to-end-verification)
- [Troubleshooting](#troubleshooting)
- [Cleanup](#cleanup)

---

## Prerequisites

| Tool | Version | Notes |
|------|---------|-------|
| Python | 3.13 | Matches the App Service + hosted-agent runtime |
| Azure CLI (`az`) | latest | `az login` |
| Azure Developer CLI (`azd`) | ≥ 1.25 | For the hosted agents (earlier versions have a broken extension resolver) |
| azd extensions | — | `azd extension install azure.ai.agents` |
| An Azure subscription | — | With quota for Azure AI Search, Cosmos DB, and App Service |
| A Foundry project | — | `gpt-5-mini`, `gpt-4.1-mini`, and `text-embedding-3-large` deployed |

Reference environment used by this repo (substitute your own values):

- Subscription: `<your-subscription>`
- Resource group: `<your-resource-group>`
- Foundry project endpoint:
  `https://<foundry-account>.services.ai.azure.com/api/projects/<project>`

---

## Step 1 — Local environment

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env     # fill in after Step 2
$env:PYTHONPATH = "src"
```

Verify the install:

```powershell
pip check
python -c "from agent_framework import Agent; from agent_framework_foundry import FoundryChatClient; print('ok')"
```

> Run all Python scripts with `$env:PYTHONPATH = "src"` set so the
> `insurance_rag_agent` package is importable.

---

## Step 2 — Provision Azure infrastructure

Provisions Azure AI Search, Cosmos DB, Blob Storage, App Service, the VNet +
Cosmos private endpoint, and the required RBAC role assignments
([infra/main.bicep](infra/main.bicep)).

```powershell
az login
.\scripts\deploy\deploy-infra.ps1 -ResourceGroup rg-ai-search-demo -Location eastus
```

Note the outputs: `searchEndpoint`, `cosmosEndpoint`, `searchServiceName`,
`cosmosAccountName`, `storageAccountName`, `apiAppName`.

> **Region notes:** Search deploys to `eastus`; Cosmos to `westus3` (eastus is
> capacity-constrained for new serverless Cosmos). App Service plan B1 runs in
> `westus3`. See [infra/README.md](infra/README.md).

---

## Step 3 — Configure `.env`

Fill `.env` with the Step 2 outputs. Minimum keys:

```ini
FOUNDRY_PROJECT_ENDPOINT=https://<foundry-account>.services.ai.azure.com/api/projects/<project>
AZURE_SEARCH_ENDPOINT=https://<searchServiceName>.search.windows.net
AZURE_OPENAI_ENDPOINT=https://<foundry-account>.openai.azure.com
COSMOS_ENDPOINT=https://<cosmosAccountName>.documents.azure.com:443/
AZURE_STORAGE_BLOB_ENDPOINT=https://<storageAccountName>.blob.core.windows.net
AZURE_STORAGE_RESOURCE_ID=/subscriptions/.../storageAccounts/<storageAccountName>
POLICY_API_BASE_URL=https://<apiAppName>.azurewebsites.net
```

Agent names and models default to the values in
[config.py](src/insurance_rag_agent/config.py); override only if you renamed
anything.

---

## Step 4 — Grant your user data access

So you can load data from your workstation (keyless / Entra ID):

```powershell
.\scripts\deploy\grant-dev-access.ps1 `
    -ResourceGroup rg-ai-search-demo `
    -SearchServiceName <searchServiceName> `
    -CosmosAccountName <cosmosAccountName> `
    -StorageAccountName <storageAccountName>
```

Grants your user: Search Index Data Contributor, Cosmos Built-in Data
Contributor, Storage Blob Data Contributor, and the Foundry data roles. RBAC
data-plane changes can take a few minutes to propagate.

---

## Step 5 — Load the data

```powershell
python scripts\load_cosmos_data.py      # policies/claims/customers -> Cosmos DB
python scripts\upload_kb_to_blob.py     # KB docs -> Blob Storage (kb-docs)
python scripts\setup_search_index.py    # Blob -> Search index (chunk + vectorize + index)
```

`setup_search_index.py` creates the index, blob data source, skillset
(split + embed), and indexer, then runs the indexer. Expected result:
~30 policies / 20 claims / 100 customers in Cosmos and ~9 chunks in
`insurance-kb`.

> **Private Cosmos:** if the subscription enforces private Cosmos DB (public
> access disabled by Azure Policy), `load_cosmos_data.py` cannot reach Cosmos
> from your workstation. Run it from inside the VNet (VM/jumpbox) or a network
> with a private endpoint to the Cosmos account. The Search + Blob steps are
> unaffected. See
> [infra/README.md](infra/README.md#governance-note--cosmos-is-private--keyless-enforced-by-policy).

---

## Step 6 — Smoke test the data plane

```powershell
python scripts\smoke_test.py
```

Confirms both Cosmos and Search return data before involving any agent.

---

## Step 7 — Run / deploy the FastAPI app

**Locally:**

```powershell
uvicorn insurance_rag_agent.main:app --app-dir src --reload --port 8000
# Health:  GET  http://localhost:8000/health
# Query:   POST http://localhost:8000/query  {"question":"..."}
```

**To App Service:**

```powershell
.\scripts\deploy\deploy-app.ps1 -ResourceGroup rg-ai-search-demo -ApiAppName <apiAppName>
```

The deploy script zips `src` + `requirements.txt` and runs
`az webapp deploy --type zip` (Oryx remote build). The startup command is
`uvicorn insurance_rag_agent.main:app --host 0.0.0.0 --port 8000 --app-dir src`
and `WEBSITES_PORT=8000`.

Test:

```powershell
$body = @{ question = "What is my deductible on POL-001, and what does a deductible mean?" } | ConvertTo-Json
Invoke-RestMethod -Uri https://<apiAppName>.azurewebsites.net/query -Method Post -Body $body -ContentType application/json
```

---

## Step 8 — Register the Foundry prompt agents

These are the portal-visible prompt agents (no-code demo surface).

```powershell
# Knowledge-base agent (native Azure AI Search tool)
.\scripts\deploy\create-search-connection.ps1 `
    -SearchServiceName <searchServiceName> -SearchResourceGroup rg-ai-search-demo `
    -FoundryResourceGroup rg-ai-search-demo
# copy the printed AZURE_SEARCH_CONNECTION_ID into .env, then:
python scripts\setup_kb_agent.py

# Policy agent (OpenAPI tool -> /api/*)
python scripts\setup_policy_agent.py

# Combined-tool router
python scripts\setup_orchestrator_agent.py

# Delegating orchestrator (optional, exposed at /api/orchestrator)
python scripts\setup_insurance_orchestrator_agent.py
```

> `POLICY_API_BASE_URL` must be a **public HTTPS** URL the Foundry service can
> reach (the App Service URL).

---

## Step 9 — Deploy the two hosted agents

The hosted (containerized custom-code) agents demoed through the chat UI live
under [hosted-agents/](hosted-agents). Each is its own `azd` project deployed
into the **existing** Foundry project.

```powershell
# one-time azd setup
azd auth login
azd config set auth.useAzCliAuth true
azd extension install azure.ai.agents

# KB hosted agent
cd hosted-agents\kb
azd deploy --no-prompt
cd ..\..

# Policy hosted agent
cd hosted-agents\policy
azd deploy --no-prompt
cd ..\..
```

Each `azd deploy` produces a new immutable agent version. Deploying identical
code reuses the active version and does **not** restart the container — make a
code change to force a fresh container.

**Hosted KB agent search auth:** the KB agent reads Search via a read-only
**query key**, but the key is **not** stored in `agent.yaml`. It lives in a
Foundry project connection (`kb-search-key`, `CustomKeys`), and
[agent.yaml](hosted-agents/kb/src/kb-hosted-agent/agent.yaml) references it with
a placeholder that the platform resolves into `AZURE_SEARCH_API_KEY` at container
start:

```yaml
- name: AZURE_SEARCH_API_KEY
  value: ${{connections.kb-search-key.credentials.AZURE_SEARCH_API_KEY}}
```

Create the connection in your own project **before** deploying the KB agent
(read the key into a variable so it is never echoed, send the request body via a
temp file, and delete it afterward):

```powershell
$key = az search query-key list --service-name <searchServiceName> `
    --resource-group <your-resource-group> --query "[0].key" -o tsv

$proj = "/subscriptions/<sub>/resourceGroups/<your-resource-group>/providers/Microsoft.CognitiveServices/accounts/<foundry-account>/projects/<project>"
$uri  = "https://management.azure.com$proj/connections/kb-search-key?api-version=2025-04-01-preview"
$body = @{ properties = @{
    authType = "CustomKeys"; category = "CustomKeys"
    target = "https://<searchServiceName>.search.windows.net"
    credentials = @{ keys = @{ AZURE_SEARCH_API_KEY = $key } }
    isSharedToAll = $true; metadata = @{}
} } | ConvertTo-Json -Depth 6
$tmp = New-TemporaryFile; Set-Content $tmp $body -Encoding utf8
try { az rest --method put --uri $uri --body "@$($tmp.FullName)" --headers "Content-Type=application/json" }
finally { Remove-Item $tmp -Force }
```

> The management API only ever returns the literal placeholder — the secret is
> never committed or echoed back. See
> [ARCHITECTURE.md §11](ARCHITECTURE.md#11-security-notes).

After deploying, point the backend at the hosted agents (defaults already do
this) and redeploy the app so `/api/agents/{kb,policy}` invoke them:

```powershell
.\scripts\deploy\deploy-app.ps1 -ResourceGroup <your-resource-group> -ApiAppName <apiAppName>
```

---

## Step 10 — End-to-end verification

Open the chat UI at `https://<apiAppName>.azurewebsites.net/` and try:

| Question | Exercises |
|----------|-----------|
| "What does collision coverage mean?" | KB agent only |
| "Is policy POL-001 active?" | Policy agent only |
| "Is policy POL-001 active, and explain collision coverage?" | Both (ontology router) |

Or via API:

```powershell
$u = "https://<apiAppName>.azurewebsites.net"
Invoke-RestMethod "$u/api/agents/kb"     -Method Post -ContentType application/json -Body (@{question="What is a deductible?"} | ConvertTo-Json)
Invoke-RestMethod "$u/api/agents/policy" -Method Post -ContentType application/json -Body (@{question="Show policy POL-001"}   | ConvertTo-Json)
Invoke-RestMethod "$u/api/chat"          -Method Post -ContentType application/json -Body (@{question="Is POL-001 active and what is collision coverage?"} | ConvertTo-Json)
```

> Prefer verifying through `/api/chat` and `/api/agents/*` rather than
> `azd ai agent invoke`, which is unreliable in this tenant
> ([ARCHITECTURE.md §10](ARCHITECTURE.md#10-design-decisions--lessons)).

---

## Troubleshooting

| Symptom | Cause / Fix |
|---------|-------------|
| `COSMOS_ENDPOINT is not set` | `.env` loaded too late — `config.py` calls `load_dotenv` at import; ensure `.env` exists and `$env:PYTHONPATH="src"` is set |
| Cosmos `Forbidden ... blocked by firewall` | Public access is disabled (often Azure Policy-enforced) — the app reaches Cosmos via the **private endpoint + VNet integration** in Bicep. Ensure `WEBSITE_VNET_ROUTE_ALL=1`, the app is VNet-integrated, and the `privatelink.documents.azure.com` zone is linked to the VNet. Enabling public access won't stick if a `modify` policy reverts it |
| Hosted KB agent `403 Forbidden` from Search | Container cached a stale credential — use the Search query key (`AZURE_SEARCH_API_KEY` in `agent.yaml`) and redeploy with a code change |
| "Azure AI Search not supported by the selected model" | The agent is on a `gpt-5` model — move it to `gpt-4.1-mini` |
| Backend can't invoke agents | App Service MI needs the **Foundry User** role (`53ca6127-...`) on the Foundry account |
| `azd ai agent invoke` auth errors | Known limitation — verify via `/api/chat` instead |
| App Service "No module named uvicorn" | Don't use a self-extracting shell startup command; use the plain uvicorn startup command + `WEBSITES_PORT=8000` |

---

## Cleanup

```powershell
# stop the web app to save cost (keeps config/data)
az webapp stop -g rg-ai-search-demo -n <apiAppName>

# or remove everything
az group delete -n rg-ai-search-demo --yes
```

Local build artifacts (`.artifacts/`, `__pycache__/`, `.venv/`) and secrets
(`.env`, `.azure/`) are gitignored and safe to delete locally.
