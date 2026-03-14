# IWF Azure Bicep Deployment

This folder contains the Infrastructure as Code for the Azure prototype.

## What a Bicep script is

A Bicep deployment is usually:

- one or more `.bicep` files that define Azure resources
- optionally one `.bicepparam` file that sets names, sizing, and non-secret parameters

For this repo:

- `main.bicep` is the Azure infrastructure blueprint
- `prototype.example.bicepparam` is a parameter template for the current prototype shape

The application code stays in the Python files. Bicep does not replace that code. It creates and configures the Azure infrastructure the code runs on.

## What this deployment creates

`main.bicep` provisions the current Azure prototype stack in one resource group:

- Log Analytics workspace
- Container Apps managed environment
- Azure Container Registry
- Storage account
- blob container `document-jobs`
- table `DocumentJobs`
- Service Bus namespace
- queue `document-processing`
- Key Vault
- Container App `iwf-api`
- Container App `iwf-worker`

It also wires the Container Apps environment variables and secrets to match the current app configuration in `app/config.py`.

## Important deployment flow

The Container Apps need images from ACR, so the clean flow is two steps:

1. Deploy infrastructure first with `deployContainerApps=false`
2. Build and push the `iwf-api` and `iwf-worker` images to ACR
3. Redeploy with `deployContainerApps=true`

## Example deployment

First deploy the shared infrastructure:

```powershell
az deployment group create `
  --resource-group rg-iwf-test `
  --parameters infra\prototype.example.bicepparam `
  --template-file infra\main.bicep `
  --parameters `
    deployContainerApps=false `
    apiBearerToken="..." `
    openAiApiKey="..." `
    iwfApiUrl="..." `
    iwfApiEmail="..." `
    iwfApiPassword="..."
```

Build and push images:

```powershell
az acr build --registry iwftestacr001 --image iwf-api:test --target api .
az acr build --registry iwftestacr001 --image iwf-worker:test --target worker .
```

Then deploy the Container Apps:

```powershell
az deployment group create `
  --resource-group rg-iwf-test `
  --parameters infra\prototype.example.bicepparam `
  --template-file infra\main.bicep `
  --parameters `
    deployContainerApps=true `
    apiBearerToken="..." `
    openAiApiKey="..." `
    iwfApiUrl="..." `
    iwfApiEmail="..." `
    iwfApiPassword="..."
```

## Notes

- The secure runtime values are passed as deployment parameters and also written into Key Vault to mirror the current prototype setup.
- This first pass uses the ACR admin account for Container Apps image pulls because it matches the current prototype approach and keeps the deployment straightforward.
- A later production hardening pass should replace that with managed identity, tighter network controls, and a CI/CD deployment pipeline.
