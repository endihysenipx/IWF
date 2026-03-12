# Azure Test Deployment Runbook

This runbook implements the deployment described in `PLAN.MD` against the existing Azure test resources:

- Resource group: `rg-iwf-test`
- Container Apps environment: `cae-iwf-test`
- Key Vault: `kv-iwf-test`
- Storage account: `stiwftest01`
- Service Bus namespace: `sb-iwf-test`

It uses:

- one new Basic Azure Container Registry
- one shared `Dockerfile` with two targets: `api` and `worker`
- manual Container Apps secrets populated from Key Vault values for this first deployment

## 1. Prerequisites

Install or update the Azure CLI and Container Apps extension, then sign in:

```powershell
az login
az extension add --name containerapp --upgrade
az provider register --namespace Microsoft.App
az provider register --namespace Microsoft.OperationalInsights
```

## 2. Set deployment variables

Choose a globally unique lowercase ACR name before running these commands.

```powershell
$ResourceGroup = "rg-iwf-test"
$Location = "westeurope"
$ContainerEnv = "cae-iwf-test"
$KeyVault = "kv-iwf-test"
$AcrName = "iwftestacr001"
$ApiAppName = "iwf-api"
$WorkerAppName = "iwf-worker"
$ApiImage = "$AcrName.azurecr.io/iwf-api:test"
$WorkerImage = "$AcrName.azurecr.io/iwf-worker:test"
```

## 3. Create the registry

Create a Basic registry, enable the admin user for this test deployment, and capture the credentials.

```powershell
az acr create `
  --resource-group $ResourceGroup `
  --name $AcrName `
  --sku Basic `
  --location $Location `
  --admin-enabled true

$AcrUsername = az acr credential show --name $AcrName --query username --output tsv
$AcrPassword = az acr credential show --name $AcrName --query "passwords[0].value" --output tsv
```

This uses the ACR admin account because the goal here is the fastest working test deployment. Microsoft documents that the admin account is mainly for testing and recommends identity-based auth for shared or production scenarios. Source: [ACR FAQ](https://learn.microsoft.com/en-us/azure/container-registry/container-registry-faq), [Container Apps existing image quickstart](https://learn.microsoft.com/en-us/azure/container-apps/get-started-existing-container-image).

## 4. Build and push the two images

Build directly in ACR from the repo root using the two `Dockerfile` targets.

```powershell
az acr build --registry $AcrName --image "iwf-api:test" --target api .
az acr build --registry $AcrName --image "iwf-worker:test" --target worker .
```

## 5. Load Key Vault secrets into local variables

These commands read the existing secret values so they can be written into Container Apps secrets for this first pass.

```powershell
$ApiBearerToken = az keyvault secret show --vault-name $KeyVault --name "api-bearer-token" --query value --output tsv
$OpenAiApiKey = az keyvault secret show --vault-name $KeyVault --name "openai-api-key" --query value --output tsv
$IwfApiUrl = az keyvault secret show --vault-name $KeyVault --name "iwf-api-url" --query value --output tsv
$IwfApiEmail = az keyvault secret show --vault-name $KeyVault --name "iwf-api-email" --query value --output tsv
$IwfApiPassword = az keyvault secret show --vault-name $KeyVault --name "iwf-api-password" --query value --output tsv
$ServiceBusConnection = az keyvault secret show --vault-name $KeyVault --name "azure-service-bus-connection-string" --query value --output tsv
$StorageConnection = az keyvault secret show --vault-name $KeyVault --name "azure-storage-connection-string" --query value --output tsv
```

## 6. Deploy `iwf-api`

Create the public API app on port `8000` with one replica.

```powershell
az containerapp create `
  --name $ApiAppName `
  --resource-group $ResourceGroup `
  --environment $ContainerEnv `
  --image $ApiImage `
  --target-port 8000 `
  --ingress external `
  --min-replicas 1 `
  --max-replicas 1 `
  --registry-server "$AcrName.azurecr.io" `
  --registry-username $AcrUsername `
  --registry-password $AcrPassword `
  --secrets `
    "api-bearer-token=$ApiBearerToken" `
    "openai-api-key=$OpenAiApiKey" `
    "iwf-api-url=$IwfApiUrl" `
    "iwf-api-email=$IwfApiEmail" `
    "iwf-api-password=$IwfApiPassword" `
    "azure-service-bus-connection-string=$ServiceBusConnection" `
    "azure-storage-connection-string=$StorageConnection" `
  --env-vars `
    "API_BEARER_TOKEN=secretref:api-bearer-token" `
    "OPENAI_API_KEY=secretref:openai-api-key" `
    "IWF_API_URL=secretref:iwf-api-url" `
    "IWF_API_EMAIL=secretref:iwf-api-email" `
    "IWF_API_PASSWORD=secretref:iwf-api-password" `
    "AZURE_SERVICE_BUS_CONNECTION_STRING=secretref:azure-service-bus-connection-string" `
    "AZURE_SERVICE_BUS_QUEUE_NAME=document-processing" `
    "AZURE_STORAGE_CONNECTION_STRING=secretref:azure-storage-connection-string" `
    "AZURE_BLOB_CONTAINER_NAME=document-jobs" `
    "AZURE_TABLE_NAME=DocumentJobs" `
    "LOCAL_DEV_MODE=false" `
    "MAX_AB_PAGES=0"
```

`POPPLER_PATH` is intentionally omitted. The image installs `poppler-utils`, and the app now only passes `poppler_path` when the env var is explicitly set.

## 7. Deploy `iwf-worker`

Create the worker without ingress. The scale rule is based on Service Bus queue length using the same Service Bus connection string stored as a Container Apps secret.

```powershell
az containerapp create `
  --name $WorkerAppName `
  --resource-group $ResourceGroup `
  --environment $ContainerEnv `
  --image $WorkerImage `
  --min-replicas 0 `
  --max-replicas 3 `
  --registry-server "$AcrName.azurecr.io" `
  --registry-username $AcrUsername `
  --registry-password $AcrPassword `
  --secrets `
    "api-bearer-token=$ApiBearerToken" `
    "openai-api-key=$OpenAiApiKey" `
    "iwf-api-url=$IwfApiUrl" `
    "iwf-api-email=$IwfApiEmail" `
    "iwf-api-password=$IwfApiPassword" `
    "azure-service-bus-connection-string=$ServiceBusConnection" `
    "azure-storage-connection-string=$StorageConnection" `
  --env-vars `
    "API_BEARER_TOKEN=secretref:api-bearer-token" `
    "OPENAI_API_KEY=secretref:openai-api-key" `
    "IWF_API_URL=secretref:iwf-api-url" `
    "IWF_API_EMAIL=secretref:iwf-api-email" `
    "IWF_API_PASSWORD=secretref:iwf-api-password" `
    "AZURE_SERVICE_BUS_CONNECTION_STRING=secretref:azure-service-bus-connection-string" `
    "AZURE_SERVICE_BUS_QUEUE_NAME=document-processing" `
    "AZURE_STORAGE_CONNECTION_STRING=secretref:azure-storage-connection-string" `
    "AZURE_BLOB_CONTAINER_NAME=document-jobs" `
    "AZURE_TABLE_NAME=DocumentJobs" `
    "LOCAL_DEV_MODE=false" `
    "MAX_AB_PAGES=0" `
  --scale-rule-name "service-bus-queue" `
  --scale-rule-type "azure-servicebus" `
  --scale-rule-metadata `
    "queueName=document-processing" `
    "namespace=sb-iwf-test" `
    "messageCount=1" `
  --scale-rule-auth "connection=azure-service-bus-connection-string"
```

The Azure CLI examples for `--env-vars`, `secretref:...`, `--secrets`, and Service Bus scale rule auth/metadata follow Microsoft Learn documentation:

- [Manage environment variables on Azure Container Apps](https://learn.microsoft.com/en-us/azure/container-apps/environment-variables)
- [Manage secrets in Azure Container Apps](https://learn.microsoft.com/en-us/azure/container-apps/manage-secrets)
- [Scaling in Azure Container Apps](https://learn.microsoft.com/en-us/azure/container-apps/scale-app)

## 8. Verify the deployment

Get the API hostname and test the health endpoint.

```powershell
$ApiFqdn = az containerapp show --name $ApiAppName --resource-group $ResourceGroup --query properties.configuration.ingress.fqdn --output tsv
Invoke-WebRequest -Uri "https://$ApiFqdn/health"
```

Then run an authenticated smoke test through the public API:

```powershell
$Headers = @{ Authorization = "Bearer $ApiBearerToken" }
Invoke-RestMethod `
  -Method Post `
  -Uri "https://$ApiFqdn/v1/document-jobs" `
  -Headers $Headers `
  -Form @{
    file = Get-Item ".\temp_incoming_ab.pdf"
    callback_url = "https://webhook.site/your-test-id"
    correlation_id = "smoke-test-1"
  }
```

## 9. Check logs

```powershell
az containerapp logs show --name $ApiAppName --resource-group $ResourceGroup --follow
az containerapp logs show --name $WorkerAppName --resource-group $ResourceGroup --follow
```

## 10. Local container validation

If Docker is available locally, validate the two targets before Azure deployment:

```powershell
docker build --target api -t iwf-api:local .
docker build --target worker -t iwf-worker:local .
docker run --rm -p 8000:8000 --env-file .env iwf-api:local
docker run --rm --env-file .env iwf-worker:local
```

The API health check should be reachable at `http://localhost:8000/health`.
