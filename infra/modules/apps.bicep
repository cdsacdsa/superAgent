// Container Apps environment, sandbox session pool, API + worker apps
param location string
param tags object
param prefix string
param token string
param identityId string
param identityClientId string
param identityPrincipalId string
param registryServer string
param keyVaultUri string
param logAnalyticsWorkspaceId string
@secure()
param appInsightsConnectionString string
param cosmosEndpoint string
param serviceBusNamespace string
param serviceBusQueue string
param searchEndpoint string
param itsmProvider string
param chatProvider string
param fastBaseUrl string
param fastModel string
param expertProvider string
param expertModel string
param foundryResource string
param servicenowInstance string
param servicenowUser string
param jiraBaseUrl string
param jiraUser string
param jiraProjectKey string
param ragApiUrl string
param sharepointEnabled bool
param vmResourceGroup string
param vmSubnetId string
param entraClientId string
@secure()
param entraClientSecret string

// Demo mode (mock ITSM): the mock ticket system lives in memory, so the API runs the worker in-process.
var demoMode = itsmProvider == 'mock'
var placeholderImage = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'
var apiName = 'ca-${prefix}-api'
var workerName = 'ca-${prefix}-worker'
var sessionExecutorRole = '0fb8eba5-a2bb-4abe-b1c1-49dfad359bb0'

resource cae 'Microsoft.App/managedEnvironments@2025-01-01' = {
  name: 'cae-${prefix}-${token}'
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: reference(logAnalyticsWorkspaceId, '2023-09-01').customerId
        sharedKey: listKeys(logAnalyticsWorkspaceId, '2023-09-01').primarySharedKey
      }
    }
    workloadProfiles: [{ name: 'Consumption', workloadProfileType: 'Consumption' }]
  }
}

// ---------------- sandbox: ACA dynamic sessions (Hyper-V isolated, egress disabled)
resource pool 'Microsoft.App/sessionPools@2024-08-02-preview' = {
  name: 'sp-${prefix}-${token}'
  location: location
  tags: tags
  properties: {
    environmentId: cae.id
    poolManagementType: 'Dynamic'
    containerType: 'PythonLTS'
    scaleConfiguration: { maxConcurrentSessions: 20 }
    dynamicPoolConfiguration: { executionType: 'Timed', cooldownPeriodInSeconds: 900 }
    sessionNetworkConfiguration: { status: 'EgressDisabled' }
  }
}

resource poolRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(pool.id, identityId, sessionExecutorRole)
  scope: pool
  properties: {
    principalId: identityPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', sessionExecutorRole)
  }
}

// ---------------- shared app configuration
var secretNames = [
  'fast-api-key'
  'expert-api-key'
  'servicenow-password'
  'jira-api-token'
  'teams-webhook-url'
  'slack-webhook-url'
  'rag-api-key'
  'webhook-secret'
  'console-api-key'
]
var kvSecrets = [for s in secretNames: {
  name: s
  keyVaultUrl: '${keyVaultUri}secrets/${s}'
  identity: identityId
}]
var authSecret = empty(entraClientId) ? [] : [{ name: 'microsoft-provider-authentication-secret', value: entraClientSecret }]

var secretEnv = [
  { name: 'SA_FAST_API_KEY', secretRef: 'fast-api-key' }
  { name: 'SA_EXPERT_API_KEY', secretRef: 'expert-api-key' }
  { name: 'SA_SERVICENOW_PASSWORD', secretRef: 'servicenow-password' }
  { name: 'SA_JIRA_API_TOKEN', secretRef: 'jira-api-token' }
  { name: 'SA_TEAMS_WEBHOOK_URL', secretRef: 'teams-webhook-url' }
  { name: 'SA_SLACK_WEBHOOK_URL', secretRef: 'slack-webhook-url' }
  { name: 'SA_RAG_API_KEY', secretRef: 'rag-api-key' }
  { name: 'SA_WEBHOOK_SECRET', secretRef: 'webhook-secret' }
  { name: 'SA_CONSOLE_API_KEY', secretRef: 'console-api-key' }
]

var commonEnv = [
  { name: 'SA_ENVIRONMENT', value: 'azure' }
  { name: 'AZURE_CLIENT_ID', value: identityClientId }
  { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsightsConnectionString }
  { name: 'SA_PUBLIC_BASE_URL', value: 'https://${apiName}.${cae.properties.defaultDomain}' }
  { name: 'SA_STORE_PROVIDER', value: 'cosmos' }
  { name: 'SA_COSMOS_ENDPOINT', value: cosmosEndpoint }
  { name: 'SA_QUEUE_PROVIDER', value: demoMode ? 'memory' : 'servicebus' }
  { name: 'SA_SERVICEBUS_NAMESPACE', value: serviceBusNamespace }
  { name: 'SA_SERVICEBUS_QUEUE', value: serviceBusQueue }
  { name: 'SA_SANDBOX_PROVIDER', value: 'aca_sessions' }
  { name: 'SA_ACA_POOL_ENDPOINT', value: pool.properties.poolManagementEndpoint }
  { name: 'SA_ACA_POOL_TYPE', value: 'python' }
  { name: 'SA_VM_SUBSCRIPTION_ID', value: subscription().subscriptionId }
  { name: 'SA_VM_RESOURCE_GROUP', value: vmResourceGroup }
  { name: 'SA_VM_SUBNET_ID', value: vmSubnetId }
  { name: 'SA_VM_LOCATION', value: location }
  { name: 'SA_FAST_PROVIDER', value: 'openai_compat' }
  { name: 'SA_FAST_BASE_URL', value: fastBaseUrl }
  { name: 'SA_FAST_MODEL', value: fastModel }
  { name: 'SA_EXPERT_PROVIDER', value: expertProvider }
  { name: 'SA_EXPERT_MODEL', value: expertModel }
  { name: 'SA_FOUNDRY_RESOURCE', value: foundryResource }
  { name: 'SA_ITSM_PROVIDER', value: itsmProvider }
  { name: 'SA_SERVICENOW_INSTANCE', value: servicenowInstance }
  { name: 'SA_SERVICENOW_USER', value: servicenowUser }
  { name: 'SA_JIRA_BASE_URL', value: jiraBaseUrl }
  { name: 'SA_JIRA_USER', value: jiraUser }
  { name: 'SA_JIRA_PROJECT_KEY', value: jiraProjectKey }
  { name: 'SA_CHAT_PROVIDER', value: chatProvider }
  { name: 'SA_RAG_API_URL', value: ragApiUrl }
  { name: 'SA_SHAREPOINT_ENABLED', value: string(sharepointEnabled) }
  { name: 'SA_AZURE_SEARCH_ENDPOINT', value: searchEndpoint }
  { name: 'SA_AZURE_SEARCH_INDEX', value: empty(searchEndpoint) ? '' : 'superagent-kedb' }
]

// ---------------- API (console, webhooks)
resource api 'Microsoft.App/containerApps@2025-01-01' = {
  name: apiName
  location: location
  tags: union(tags, { 'azd-service-name': 'api' })
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${identityId}': {} } }
  dependsOn: [poolRole]
  properties: {
    environmentId: cae.id
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: { external: true, targetPort: 8000, transport: 'auto', allowInsecure: false }
      registries: [{ server: registryServer, identity: identityId }]
      secrets: concat(kvSecrets, authSecret)
    }
    template: {
      containers: [
        {
          name: 'api'
          image: placeholderImage
          resources: { cpu: json('1.0'), memory: '2Gi' }
          env: concat(commonEnv, secretEnv, [
            { name: 'SA_RUN_WORKER_INPROC', value: string(demoMode) }
          ])
          probes: [
            { type: 'Liveness', httpGet: { path: '/healthz', port: 8000 }, initialDelaySeconds: 10, periodSeconds: 30 }
          ]
        }
      ]
      scale: { minReplicas: 1, maxReplicas: demoMode ? 1 : 3 }
    }
  }
}

// ---------------- Worker (poller, queue consumers, SLA monitor); KEDA scales on queue depth
resource worker 'Microsoft.App/containerApps@2025-01-01' = {
  name: workerName
  location: location
  tags: union(tags, { 'azd-service-name': 'worker' })
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${identityId}': {} } }
  dependsOn: [poolRole]
  properties: {
    environmentId: cae.id
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      registries: [{ server: registryServer, identity: identityId }]
      secrets: kvSecrets
    }
    template: {
      containers: [
        {
          name: 'worker'
          image: placeholderImage
          command: ['python', '-m', 'superagent.worker']
          resources: { cpu: json('1.0'), memory: '2Gi' }
          env: concat(commonEnv, secretEnv, [
            { name: 'SA_WORKER_IDLE', value: string(demoMode) }
          ])
        }
      ]
      scale: {
        minReplicas: demoMode ? 0 : 1 // one replica always runs the poller + SLA monitor
        maxReplicas: demoMode ? 1 : 5
        rules: [
          {
            name: 'tickets-queue'
            custom: {
              type: 'azure-servicebus'
              metadata: { queueName: serviceBusQueue, namespace: split(serviceBusNamespace, '.')[0], messageCount: '3' }
              identity: identityId
            }
          }
        ]
      }
    }
  }
}

// ---------------- Entra ID sign-in for the console (optional)
resource auth 'Microsoft.App/containerApps/authConfigs@2025-01-01' = if (!empty(entraClientId)) {
  parent: api
  name: 'current'
  properties: {
    platform: { enabled: true }
    globalValidation: {
      unauthenticatedClientAction: 'RedirectToLoginPage'
      redirectToProvider: 'azureactivedirectory'
      excludedPaths: ['/healthz', '/readyz', '/webhooks/servicenow', '/webhooks/jira']
    }
    identityProviders: {
      azureActiveDirectory: {
        enabled: true
        registration: {
          clientId: entraClientId
          clientSecretSettingName: 'microsoft-provider-authentication-secret'
          openIdIssuer: '${environment().authentication.loginEndpoint}${tenant().tenantId}/v2.0'
        }
        validation: { allowedAudiences: ['api://${entraClientId}', entraClientId] }
      }
    }
  }
}

output apiUrl string = 'https://${api.properties.configuration.ingress.fqdn}'
output sessionPoolEndpoint string = pool.properties.poolManagementEndpoint
