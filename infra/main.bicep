targetScope = 'resourceGroup'

@description('Azure region for all resources.')
param location string = resourceGroup().location

@description('Log Analytics workspace name.')
param logAnalyticsWorkspaceName string = 'log-iwf-test'

@description('Container Apps managed environment name.')
param containerAppsEnvironmentName string = 'cae-iwf-test'

@description('Azure Container Registry name. Must be globally unique and use only lowercase letters and numbers.')
param containerRegistryName string = 'iwftestacr001'

@description('Storage account name. Must be globally unique and use only lowercase letters and numbers.')
param storageAccountName string = 'stiwftest01'

@description('Service Bus namespace name. Must be globally unique.')
param serviceBusNamespaceName string = 'sb-iwf-test'

@description('Key Vault name. Must be globally unique.')
param keyVaultName string = 'kv-iwf-test'

@description('Public API Container App name.')
param apiAppName string = 'iwf-api'

@description('Background worker Container App name.')
param workerAppName string = 'iwf-worker'

@description('Service Bus queue used between API and worker.')
param serviceBusQueueName string = 'document-processing'

@description('Blob container used for input and output documents.')
param blobContainerName string = 'document-jobs'

@description('Azure Table Storage table used for job metadata.')
param tableName string = 'DocumentJobs'

@description('Set to false for the first infra-only deployment so ACR exists before building images.')
param deployContainerApps bool = true

@description('ACR repository name for the API image.')
param apiImageRepository string = 'iwf-api'

@description('ACR tag for the API image.')
param apiImageTag string = 'test'

@description('ACR repository name for the worker image.')
param workerImageRepository string = 'iwf-worker'

@description('ACR tag for the worker image.')
param workerImageTag string = 'test'

@description('API CPU allocation in Container Apps. Allowed values are the standard Container Apps CPU sizes.')
param apiCpu string = '0.5'

@description('API memory allocation in Container Apps, for example 1.0Gi.')
param apiMemory string = '1.0Gi'

@description('Worker CPU allocation in Container Apps. Allowed values are the standard Container Apps CPU sizes.')
param workerCpu string = '0.5'

@description('Worker memory allocation in Container Apps, for example 1.0Gi.')
param workerMemory string = '1.0Gi'

@description('Minimum API replicas.')
param apiMinReplicas int = 1

@description('Maximum API replicas.')
param apiMaxReplicas int = 1

@description('Minimum worker replicas.')
param workerMinReplicas int = 0

@description('Maximum worker replicas.')
param workerMaxReplicas int = 20

@description('Queue length threshold for worker scaling.')
param queueMessageCountThreshold int = 1

@description('API bearer token expected by the FastAPI application.')
@secure()
param apiBearerToken string

@description('OpenAI API key used by OCR processing.')
@secure()
param openAiApiKey string

@description('Upstream IWF API base URL.')
@secure()
param iwfApiUrl string

@description('Upstream IWF API email/username.')
@secure()
param iwfApiEmail string

@description('Upstream IWF API password.')
@secure()
param iwfApiPassword string

@description('Optional tags applied to all resources.')
param tags object = {}

var containerRegistryApiVersion = '2023-07-01'
var storageApiVersion = '2023-05-01'
var serviceBusApiVersion = '2022-10-01-preview'
var logAnalyticsApiVersion = '2023-09-01'
var registryLoginServer = '${containerRegistryName}.azurecr.io'
var apiImage = '${registryLoginServer}/${apiImageRepository}:${apiImageTag}'
var workerImage = '${registryLoginServer}/${workerImageRepository}:${workerImageTag}'
var acrCredentials = listCredentials(resourceId('Microsoft.ContainerRegistry/registries', containerRegistryName), containerRegistryApiVersion)
var acrUsername = acrCredentials.username
var acrPassword = acrCredentials.passwords[0].value
var storageKeys = listKeys(resourceId('Microsoft.Storage/storageAccounts', storageAccountName), storageApiVersion).keys
var storageConnectionString = 'DefaultEndpointsProtocol=https;AccountName=${storageAccountName};AccountKey=${storageKeys[0].value};EndpointSuffix=${environment().suffixes.storage}'
var serviceBusConnectionString = listKeys(serviceBusRootAuthRule.id, serviceBusApiVersion).primaryConnectionString
var containerAppSecrets = [
  {
    name: 'acr-password'
    value: acrPassword
  }
  {
    name: 'api-bearer-token'
    value: apiBearerToken
  }
  {
    name: 'openai-api-key'
    value: openAiApiKey
  }
  {
    name: 'iwf-api-url'
    value: iwfApiUrl
  }
  {
    name: 'iwf-api-email'
    value: iwfApiEmail
  }
  {
    name: 'iwf-api-password'
    value: iwfApiPassword
  }
  {
    name: 'azure-service-bus-connection-string'
    value: serviceBusConnectionString
  }
  {
    name: 'azure-storage-connection-string'
    value: storageConnectionString
  }
]
var sharedEnv = [
  {
    name: 'API_BEARER_TOKEN'
    secretRef: 'api-bearer-token'
  }
  {
    name: 'OPENAI_API_KEY'
    secretRef: 'openai-api-key'
  }
  {
    name: 'IWF_API_URL'
    secretRef: 'iwf-api-url'
  }
  {
    name: 'IWF_API_EMAIL'
    secretRef: 'iwf-api-email'
  }
  {
    name: 'IWF_API_PASSWORD'
    secretRef: 'iwf-api-password'
  }
  {
    name: 'AZURE_SERVICE_BUS_CONNECTION_STRING'
    secretRef: 'azure-service-bus-connection-string'
  }
  {
    name: 'AZURE_SERVICE_BUS_QUEUE_NAME'
    value: serviceBusQueueName
  }
  {
    name: 'AZURE_STORAGE_CONNECTION_STRING'
    secretRef: 'azure-storage-connection-string'
  }
  {
    name: 'AZURE_BLOB_CONTAINER_NAME'
    value: blobContainerName
  }
  {
    name: 'AZURE_TABLE_NAME'
    value: tableName
  }
  {
    name: 'LOCAL_DEV_MODE'
    value: 'false'
  }
  {
    name: 'MAX_AB_PAGES'
    value: '0'
  }
]

resource logAnalyticsWorkspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: logAnalyticsWorkspaceName
  location: location
  tags: tags
  properties: {
    retentionInDays: 30
  }
  sku: {
    name: 'PerGB2018'
  }
}

resource containerAppEnvironment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: containerAppsEnvironmentName
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalyticsWorkspace.properties.customerId
        sharedKey: listKeys(logAnalyticsWorkspace.id, logAnalyticsApiVersion).primarySharedKey
      }
    }
  }
}

resource containerRegistry 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: containerRegistryName
  location: location
  tags: tags
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: true
    publicNetworkAccess: 'Enabled'
  }
}

resource storageAccount 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageAccountName
  location: location
  tags: tags
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    allowBlobPublicAccess: false
    allowSharedKeyAccess: true
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storageAccount
  name: 'default'
}

resource documentJobsContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: blobContainerName
}

resource tableService 'Microsoft.Storage/storageAccounts/tableServices@2023-05-01' = {
  parent: storageAccount
  name: 'default'
}

resource documentJobsTable 'Microsoft.Storage/storageAccounts/tableServices/tables@2023-05-01' = {
  parent: tableService
  name: tableName
}

resource serviceBusNamespace 'Microsoft.ServiceBus/namespaces@2022-10-01-preview' = {
  name: serviceBusNamespaceName
  location: location
  tags: tags
  sku: {
    name: 'Basic'
    tier: 'Basic'
  }
  properties: {}
}

resource serviceBusQueue 'Microsoft.ServiceBus/namespaces/queues@2022-10-01-preview' = {
  parent: serviceBusNamespace
  name: serviceBusQueueName
  properties: {
    deadLetteringOnMessageExpiration: true
    maxDeliveryCount: 10
    requiresDuplicateDetection: false
  }
}

resource serviceBusRootAuthRule 'Microsoft.ServiceBus/namespaces/AuthorizationRules@2022-10-01-preview' existing = {
  parent: serviceBusNamespace
  name: 'RootManageSharedAccessKey'
}

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: keyVaultName
  location: location
  tags: tags
  properties: {
    enableRbacAuthorization: true
    enabledForTemplateDeployment: true
    tenantId: tenant().tenantId
    publicNetworkAccess: 'Enabled'
    sku: {
      family: 'A'
      name: 'standard'
    }
    softDeleteRetentionInDays: 90
  }
}

resource apiBearerTokenSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'api-bearer-token'
  properties: {
    value: apiBearerToken
  }
}

resource openAiApiKeySecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'openai-api-key'
  properties: {
    value: openAiApiKey
  }
}

resource iwfApiUrlSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'iwf-api-url'
  properties: {
    value: iwfApiUrl
  }
}

resource iwfApiEmailSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'iwf-api-email'
  properties: {
    value: iwfApiEmail
  }
}

resource iwfApiPasswordSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'iwf-api-password'
  properties: {
    value: iwfApiPassword
  }
}

resource serviceBusConnectionStringSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'azure-service-bus-connection-string'
  properties: {
    value: serviceBusConnectionString
  }
  dependsOn: [
    serviceBusNamespace
    serviceBusQueue
  ]
}

resource storageConnectionStringSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'azure-storage-connection-string'
  properties: {
    value: storageConnectionString
  }
  dependsOn: [
    storageAccount
  ]
}

resource apiContainerApp 'Microsoft.App/containerApps@2024-03-01' = if (deployContainerApps) {
  name: apiAppName
  location: location
  tags: tags
  properties: {
    managedEnvironmentId: containerAppEnvironment.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8000
        transport: 'auto'
        allowInsecure: false
      }
      registries: [
        {
          server: registryLoginServer
          username: acrUsername
          passwordSecretRef: 'acr-password'
        }
      ]
      secrets: containerAppSecrets
    }
    template: {
      containers: [
        {
          name: 'api'
          image: apiImage
          env: sharedEnv
          probes: [
            {
              type: 'Liveness'
              httpGet: {
                path: '/health'
                port: 8000
              }
              initialDelaySeconds: 10
              periodSeconds: 10
            }
            {
              type: 'Readiness'
              httpGet: {
                path: '/ready'
                port: 8000
              }
              initialDelaySeconds: 10
              periodSeconds: 10
            }
          ]
          resources: {
            cpu: json(apiCpu)
            memory: apiMemory
          }
        }
      ]
      scale: {
        minReplicas: apiMinReplicas
        maxReplicas: apiMaxReplicas
      }
    }
  }
  dependsOn: [
    containerRegistry
    apiBearerTokenSecret
    openAiApiKeySecret
    iwfApiUrlSecret
    iwfApiEmailSecret
    iwfApiPasswordSecret
    serviceBusConnectionStringSecret
    storageConnectionStringSecret
    documentJobsContainer
    documentJobsTable
    serviceBusQueue
  ]
}

resource workerContainerApp 'Microsoft.App/containerApps@2024-03-01' = if (deployContainerApps) {
  name: workerAppName
  location: location
  tags: tags
  properties: {
    managedEnvironmentId: containerAppEnvironment.id
    configuration: {
      activeRevisionsMode: 'Single'
      registries: [
        {
          server: registryLoginServer
          username: acrUsername
          passwordSecretRef: 'acr-password'
        }
      ]
      secrets: containerAppSecrets
    }
    template: {
      containers: [
        {
          name: 'worker'
          image: workerImage
          env: sharedEnv
          resources: {
            cpu: json(workerCpu)
            memory: workerMemory
          }
        }
      ]
      scale: {
        minReplicas: workerMinReplicas
        maxReplicas: workerMaxReplicas
        rules: [
          {
            name: 'service-bus-queue'
            custom: {
              type: 'azure-servicebus'
              metadata: {
                queueName: serviceBusQueueName
                namespace: serviceBusNamespace.name
                messageCount: string(queueMessageCountThreshold)
              }
              auth: [
                {
                  secretRef: 'azure-service-bus-connection-string'
                  triggerParameter: 'connection'
                }
              ]
            }
          }
        ]
      }
    }
  }
  dependsOn: [
    containerRegistry
    apiBearerTokenSecret
    openAiApiKeySecret
    iwfApiUrlSecret
    iwfApiEmailSecret
    iwfApiPasswordSecret
    serviceBusConnectionStringSecret
    storageConnectionStringSecret
    documentJobsContainer
    documentJobsTable
    serviceBusQueue
  ]
}

output containerRegistryLoginServer string = registryLoginServer
output apiImageReference string = apiImage
output workerImageReference string = workerImage
output apiUrl string = deployContainerApps ? 'https://${apiContainerApp.properties.configuration.ingress.fqdn}' : ''
output keyVaultUri string = keyVault.properties.vaultUri
output serviceBusQueueResourceId string = serviceBusQueue.id
output blobContainerResourceId string = documentJobsContainer.id
output tableResourceId string = documentJobsTable.id
