# Azure Test Deployment Plan For `IWF`

## Summary
Use the existing Azure test resources as the base, add one Azure Container Registry, containerize the app in the repo, and deploy two Container Apps from ACR:
- `iwf-api` for the FastAPI service
- `iwf-worker` for queue processing

Chosen defaults for this first deployment:
- optimize for **fastest working Azure test**
- use **Azure Container Registry**
- keep the existing resources already created in `rg-iwf-test`
- use **manual Container Apps secrets/config** for the first deployment, with Key Vault remaining the source of truth outside the app
- keep Service Bus on the current **Basic** tier for this first end-to-end test

## Implementation Changes
### App/runtime changes
- Make the OCR runtime Linux-container compatible by removing the hard dependency on the current Windows Poppler default in `app/config.py` and `app/ocr_extractor.py`.
- Treat `POPPLER_PATH` as optional: in containers, rely on `poppler-utils` installed on the image and only pass `poppler_path` when the env var is explicitly set.
- Keep the existing API and worker entrypoints (`run.py`, `worker.py`) and do not change the external API shape.
- Keep Azure-backed storage/queue behavior as-is; Azure resources already match the current env defaults:
  - queue: `document-processing`
  - blob container: `document-jobs`
  - table: `DocumentJobs`

### Container packaging
- Add one shared Docker build path for the Python app plus two runnable image variants:
  - API image command: `python run.py`
  - worker image command: `python worker.py`
- Install required Linux packages in the image, especially Poppler for `pdf2image`.
- Add `.dockerignore` to exclude `venv`, `.localdata`, `.git`, caches, temp PDFs/XMLs, and other local-only files.
- Keep the image minimal but not overly optimized for this first test; correctness and reproducibility matter more than image size.

### Azure deployment path
- Create one **Basic ACR** in `rg-iwf-test` in `West Europe`.
- Build and push two tagged images to ACR: one for API, one for worker.
- Deploy two Container Apps into the existing `cae-iwf-test` environment:
  - `iwf-api`
  - `iwf-worker`
- Configure Container Apps secrets from the values already stored in Key Vault, but do not implement direct Key Vault secret references in this first pass.
- Use the existing Service Bus namespace/queue and Storage Account resources already provisioned.

### Environment variable contract
Use these env vars in Container Apps:
- `API_BEARER_TOKEN` from secret `api-bearer-token`
- `OPENAI_API_KEY` from secret `openai-api-key`
- `IWF_API_URL` from secret `iwf-api-url`
- `IWF_API_EMAIL` from secret `iwf-api-email`
- `IWF_API_PASSWORD` from secret `iwf-api-password`
- `AZURE_SERVICE_BUS_CONNECTION_STRING` from secret `azure-service-bus-connection-string`
- `AZURE_SERVICE_BUS_QUEUE_NAME=document-processing`
- `AZURE_STORAGE_CONNECTION_STRING` from secret `azure-storage-connection-string`
- `AZURE_BLOB_CONTAINER_NAME=document-jobs`
- `AZURE_TABLE_NAME=DocumentJobs`
- `LOCAL_DEV_MODE=false`
- `MAX_AB_PAGES=0`
- leave `POPPLER_PATH` unset in Azure

### Container App settings
- `iwf-api`
  - external ingress enabled
  - target port `8000`
  - min replicas `1`
  - max replicas `1` for the first deployment
- `iwf-worker`
  - no external ingress
  - min replicas `0`
  - max replicas `3` for the first smoke-test deployment
  - scale rule based on Service Bus queue length
- After the first successful end-to-end test, raise worker max replicas from `3` toward `20` only if needed.

## Public Interfaces / Operational Additions
- No API contract changes to `/health`, `/v1/document-jobs`, or job status endpoints.
- New deployment artifacts will be added:
  - Docker packaging files
  - a short Azure deployment/runbook for build, push, deploy, and secret/env mapping
- New operational dependency:
  - Azure Container Registry for image storage

## Test Plan
### Local/container validation
- Build the API and worker images locally.
- Start the API container locally and confirm `/health` returns success.
- Start the worker container locally with Azure env vars pointed at the created Azure resources.
- Submit one sample PDF and confirm:
  - job is accepted
  - message appears on Service Bus
  - worker processes the queue entry
  - output XML lands in blob storage
  - job row updates in `DocumentJobs`
  - callback path works with a test callback URL

### Azure smoke test
- Deploy both Container Apps from ACR.
- Confirm `iwf-api` is reachable and authenticated with the bearer token.
- Run one document through the full Azure-hosted flow.
- Run a small batch of 3 documents to confirm worker scale-out from zero works.
- Confirm logs are visible in the Container Apps / Log Analytics views.

### Acceptance criteria
- Both Container Apps start without crash looping.
- One end-to-end PDF completes successfully through Azure.
- Queue, blob, and table integration all work using the provisioned resources.
- OCR works inside the Linux container without the current Windows Poppler path.
- No repo secrets are required at runtime outside Azure secret configuration.

## Assumptions And Defaults
- Existing Azure resources remain the deployment target:
  - `rg-iwf-test`
  - `cae-iwf-test`
  - `stiwftest01`
  - `sb-iwf-test`
  - `kv-iwf-test`
- Service Bus **Basic** is sufficient for the current code because the app only uses a simple queue and no advanced Service Bus features.
- ACR will be created fresh rather than using Docker Hub or GHCR.
- The first deployment prioritizes speed over production-grade secret/identity integration; direct managed identity + Key Vault reference wiring is explicitly deferred.
- The current `cae-iwf-test` environment in `West Europe` will be reused even though it is a workload-profile environment; no replacement environment is planned in this first pass.
