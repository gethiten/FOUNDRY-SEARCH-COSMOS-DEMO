# CI/CD setup

GitHub Actions workflows for this repo. All Azure auth uses **OIDC federated
credentials** — no secrets are stored in GitHub.

## Workflows

| Workflow | Trigger | Purpose |
| --- | --- | --- |
| [ci.yml](ci.yml) | PR → `master` | Ruff lint + byte-compile (no Azure creds) |
| [infra.yml](infra.yml) | PR (preview) / push → `master`, `infra/**` | Bicep `what-if` on PR, deploy on merge |
| [deploy-app.yml](deploy-app.yml) | push → `master`, `src/**` | Zip-deploy the API to App Service |
| [deploy-hosted-agents.yml](deploy-hosted-agents.yml) | push → `master`, `hosted-agents/**` | `azd deploy` kb + policy agents |

## 1. Create the OIDC app registration

```bash
APP_ID=$(az ad app create --display-name "github-foundry-search-cosmos" --query appId -o tsv)
az ad sp create --id "$APP_ID"
SP_OID=$(az ad sp show --id "$APP_ID" --query id -o tsv)
```

Grant it the rights it needs on the resource group (Contributor covers infra +
app deploy; add the data-plane roles only if a workflow needs them):

```bash
SUB=$(az account show --query id -o tsv)
az role assignment create --assignee "$APP_ID" --role Contributor \
  --scope "/subscriptions/$SUB/resourceGroups/<your-resource-group>"
```

## 2. Add federated credentials

One subject per trigger you use. Replace `<owner>/<repo>`:

```bash
# Pushes to master (deploy jobs)
az ad app federated-credential create --id "$APP_ID" --parameters '{
  "name": "master",
  "issuer": "https://token.actions.githubusercontent.com",
  "subject": "repo:<owner>/<repo>:ref:refs/heads/master",
  "audiences": ["api://AzureADTokenExchange"]
}'

# Pull requests (infra what-if)
az ad app federated-credential create --id "$APP_ID" --parameters '{
  "name": "pull-request",
  "issuer": "https://token.actions.githubusercontent.com",
  "subject": "repo:<owner>/<repo>:pull_request",
  "audiences": ["api://AzureADTokenExchange"]
}'

# Production environment (deploy jobs use `environment: production`)
az ad app federated-credential create --id "$APP_ID" --parameters '{
  "name": "env-production",
  "issuer": "https://token.actions.githubusercontent.com",
  "subject": "repo:<owner>/<repo>:environment:production",
  "audiences": ["api://AzureADTokenExchange"]
}'
```

## 3. Configure repository variables

Settings → Secrets and variables → Actions → **Variables** (these are IDs/names,
not secrets):

| Variable | Example |
| --- | --- |
| `AZURE_CLIENT_ID` | the `$APP_ID` from step 1 |
| `AZURE_TENANT_ID` | `az account show --query tenantId -o tsv` |
| `AZURE_SUBSCRIPTION_ID` | `az account show --query id -o tsv` |
| `AZURE_RESOURCE_GROUP` | `<your-resource-group>` |
| `AZURE_LOCATION` | `eastus` |
| `AZURE_API_APP_NAME` | `<apiAppName>` (App Service name) |
| `AZURE_AGENTS_ENV_NAME` | base azd env name, e.g. `insrag` (suffixed `-kb`/`-policy`) |
| `AZURE_AGENTS_LOCATION` | `eastus` (a Foundry-supported region) |

## 4. Create a `production` environment (optional gate)

Settings → Environments → **New environment** → `production`. Add required
reviewers there if you want manual approval before any deploy.

## Notes

- `deploy-hosted-agents.yml` runs `azd deploy` against an **already-provisioned**
  agent. The first time, provision the Foundry project/agent and the
  `kb-search-key` connection by running the workflow manually with the
  **`provision`** input enabled (Actions → Deploy Hosted Agents → Run workflow →
  check *Run `azd provision`*), or run `azd provision` locally. See
  [ARCHITECTURE.md §11](../../ARCHITECTURE.md#11-security-notes).
- The Search query key is **not** in the repo — it lives in the Foundry project
  connection `kb-search-key` and is injected at container start.
