# Copilot Studio action setup

Wire the Insurance Agentic-RAG API into Microsoft Copilot Studio as a custom
connector action, secured with Microsoft Entra ID (OAuth2).

## Prerequisites

- The API deployed to App Service (`scripts/deploy/deploy-app.ps1`) and reachable
  at `https://<your-api-app>.azurewebsites.net/health`.
- The OpenAPI spec: [../openapi/copilot-studio-insurance.openapi.yaml](../openapi/copilot-studio-insurance.openapi.yaml).

## 1. Protect the API with Entra ID (Easy Auth)

1. In the Azure portal, open the API App Service → **Authentication** → **Add identity provider** → **Microsoft**.
2. Create a new app registration (or reuse one). Note its **Application (client) ID**.
3. Set the **App ID URI** to `api://<api-app-registration-client-id>` and add a
   scope/role `access_as_application`.
4. Require authentication (return 401 on unauthenticated requests).

## 2. Fill in the OpenAPI placeholders

Edit `openapi/copilot-studio-insurance.openapi.yaml` and replace:

| Placeholder | Value |
|-------------|-------|
| `<your-api-app>.azurewebsites.net` | your App Service host |
| `<tenant-id>` | your Entra tenant id |
| `<api-app-registration-client-id>` | the app registration client id from step 1 |

## 3. Import as a custom connector

1. In Copilot Studio (or Power Platform → **Custom connectors**), choose
   **New custom connector → Import an OpenAPI file** and upload the edited YAML.
2. On the **Security** tab, confirm **OAuth 2.0** with:
   - Identity provider: **Azure Active Directory**
   - Client id / secret: from a confidential client app registration that has
     permission to the API scope above
   - Resource URL / scope: `api://<api-app-registration-client-id>/access_as_application`
3. **Create connector**, then **Test** the `insuranceQuery` action with a body like:

   ```json
   { "question": "What is my deductible on POL-001 and what does a deductible mean?" }
   ```

## 4. Use the action in an agent/topic

1. In your Copilot Studio agent, add an **Action** → select the `insuranceQuery` operation.
2. Map the input `question` to the user's message (e.g. `Activity.Text` or a
   topic variable). Optionally map `policy_id` / `customer_id` if collected.
3. Return `answer` to the user. Optionally surface `sources` / `citations` as
   references.

## Operations

| Operation | Method | Path | Purpose |
|-----------|--------|------|---------|
| `insuranceQuery` | POST | `/query` | Agentic-RAG answer over Cosmos DB + Azure AI Search |
| `healthCheck` | GET | `/health` | Service/config health |

The single `insuranceQuery` action is sufficient — the Foundry agent itself routes
to the knowledge base (Azure AI Search), the policy/claims data (Cosmos DB), or both.
