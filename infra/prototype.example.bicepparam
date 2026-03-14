using './main.bicep'

param location = 'westeurope'

param logAnalyticsWorkspaceName = 'log-iwf-test'
param containerAppsEnvironmentName = 'cae-iwf-test'
param containerRegistryName = 'iwftestacr001'
param storageAccountName = 'stiwftest01'
param serviceBusNamespaceName = 'sb-iwf-test'
param keyVaultName = 'kv-iwf-test'

param apiAppName = 'iwf-api'
param workerAppName = 'iwf-worker'

param serviceBusQueueName = 'document-processing'
param blobContainerName = 'document-jobs'
param tableName = 'DocumentJobs'

param deployContainerApps = false

param apiImageRepository = 'iwf-api'
param apiImageTag = 'test'
param workerImageRepository = 'iwf-worker'
param workerImageTag = 'test'

param apiCpu = '0.5'
param apiMemory = '1.0Gi'
param workerCpu = '0.5'
param workerMemory = '1.0Gi'

param apiMinReplicas = 1
param apiMaxReplicas = 1
param workerMinReplicas = 0
param workerMaxReplicas = 20
param queueMessageCountThreshold = 1

param tags = {
  project: 'IWF'
  environment: 'test'
  managedBy: 'bicep'
}
