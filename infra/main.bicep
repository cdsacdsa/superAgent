// SuperAgent: Azure infrastructure (deploy with `azd up` or scripts/deploy.sh)
targetScope = 'subscription'

@minLength(1)
@maxLength(20)
@description('Environment name, e.g. "superagent-dev". Used to name all resources.')
param environmentName string

@description('Azure region for all resources.')
param location string

// ---------------- behaviour
@allowed(['mock', 'servicenow', 'jira'])
@description('ITSM tool. "mock" = demo mode (built-in sample tickets, the API runs the worker in-process).')
param itsmProvider string = 'mock'

@allowed(['console', 'teams', 'slack'])
param chatProvider string = 'console'

@description('Fast tier (DeepSeek) OpenAI-compatible endpoint and model.')
param fastBaseUrl string = 'https://openrouter.ai/api/v1'
param fastModel string = 'deepseek/deepseek-v4.1-flash'

@allowed(['anthropic', 'foundry', 'openai_compat', 'fake'])
@description('Expert tier provider. "foundry" = Claude in Microsoft Foundry using the managed identity.')
param expertProvider string = 'anthropic'
param expertModel string = 'claude-opus-5-5'
@description('Microsoft Foundry resource name when expertProvider = foundry.')
param foundryResource string = ''

@description('ServiceNow instance host (e.g. contoso.service-now.com) and integration user.')
param servicenowInstance string = ''
param servicenowUser string = ''
param jiraBaseUrl string = ''
param jiraUser string = ''
param jiraProjectKey string = ''

param ragApiUrl string = ''
@allowed(['true', 'false'])
param sharepointEnabled string = 'false'

@description('Create the ephemeral-VM sandbox (isolated resource group + VNet without Internet egress).')
@allowed(['true', 'false'])
param enableVmSandbox string = 'false'
@description('Create an Azure AI Search service used as Known Error DB / resolution memory.')
@allowed(['true', 'false'])
param enableSearch string = 'false'

@description('Entra ID app registration (client id) protecting the console. Empty = API-key auth.')
param entraClientId string = ''

@description('Run both model tiers on Microsoft Foundry with the managed identity (no API keys). Overrides the fast*/expert* settings above.')
@allowed(['true', 'false'])
param enableFoundry string = 'false'
@description('Foundry deployments: name = model name; version empty = the model\'s default version.')
param foundryFastModel string = 'gpt-5.4-mini'
param foundryFastModelFormat string = 'OpenAI'
param foundryFastModelVersion string = ''
param foundryFastSku string = 'GlobalStandard'
param foundryFastCapacity int = 50
@description('reasoning_effort for the fast tier (GPT-5-class models: low keeps it fast and cheap; empty = not sent).')
param fastReasoningEffort string = ''
@description('Expert tier model; "none" = no expert deployment (the agent hands hard cases to a human).')
param foundryExpertModel string = 'claude-opus-5-5'
param foundryExpertModelFormat string = 'Anthropic'
param foundryExpertModelVersion string = ''
param foundryExpertSku string = 'GlobalStandard'
param foundryExpertCapacity int = 10
@description('Claude in Foundry only: legal organization name, ISO country code and industry (technology, finance, healthcare, education, retail, manufacturing, government, media, other). Deploying accepts the Anthropic Marketplace terms for that organization.')
param claudeOrganizationName string = ''
param claudeCountryCode string = ''
param claudeIndustry string = ''

@allowed(['free', 'basic', 'standard'])
param searchSku string = 'free'
@description('The deploying user (azd sets AZURE_PRINCIPAL_ID): gets data roles to ingest docs and upload installers.')
param principalId string = ''

@description('Product sandbox image in ACR (see scripts/build-product-image). Creates a custom-container pool: one dedicated node is billed around the clock. Empty = none.')
param productSandboxImage string = ''
@description('Cheaper product sandbox: files in the app image copied into the code-interpreter session, e.g. /app/sandbox/product/demo_app.')
param productBootstrapDir string = ''

@description('Confluence + Jira knowledge (Atlassian Cloud site, read-only user).')
param atlassianBaseUrl string = ''
param atlassianUser string = ''
@allowed(['true', 'false'])
param confluenceEnabled string = 'false'
param confluenceSpaces string = ''
@allowed(['true', 'false'])
param jiraKbEnabled string = 'false'
param jiraKbProjects string = ''

@description('Windows sandbox VMs: installer blob (in the sandbox storage account) and setup script path inside the image.')
param vmInstallerBlobUrl string = ''
param vmSetupScript string = ''

// ---------------- secrets (stored in Key Vault, never in app settings)
@secure()
param fastApiKey string = ''
@secure()
param expertApiKey string = ''
@secure()
param servicenowPassword string = ''
@secure()
param jiraApiToken string = ''
@secure()
param teamsWebhookUrl string = ''
@secure()
param slackWebhookUrl string = ''
@secure()
param ragApiKey string = ''
@secure()
param webhookSecret string = ''
@secure()
param consoleApiKey string = ''
@secure()
param entraClientSecret string = ''
@secure()
param atlassianApiToken string = ''
@secure()
param mcpToken string = ''

var vmSandbox = enableVmSandbox == 'true'
var foundryOn = enableFoundry == 'true'
var searchEnabled = enableSearch == 'true'
var tags = { 'azd-env-name': environmentName, app: 'superagent' }
var token = toLower(uniqueString(subscription().id, environmentName, location))
var prefix = take(replace(toLower(environmentName), '_', '-'), 12)

resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: 'rg-${environmentName}'
  location: location
  tags: tags
}

resource sandboxRg 'Microsoft.Resources/resourceGroups@2024-03-01' = if (vmSandbox) {
  name: 'rg-${environmentName}-sandbox'
  location: location
  tags: union(tags, { purpose: 'superagent-sandbox-vms' })
}

module core 'modules/core.bicep' = {
  name: 'core'
  scope: rg
  params: {
    location: location
    tags: tags
    prefix: prefix
    token: token
    enableSearch: searchEnabled
    searchSku: searchSku
    userPrincipalId: principalId
    secrets: {
      'fast-api-key': fastApiKey
      'expert-api-key': expertApiKey
      'servicenow-password': servicenowPassword
      'jira-api-token': jiraApiToken
      'teams-webhook-url': teamsWebhookUrl
      'slack-webhook-url': slackWebhookUrl
      'rag-api-key': ragApiKey
      'webhook-secret': webhookSecret
      'console-api-key': consoleApiKey
      'atlassian-api-token': atlassianApiToken
      'mcp-token': mcpToken
    }
  }
}

module sandboxNet 'modules/sandbox-vm-network.bicep' = if (vmSandbox) {
  name: 'sandbox-vm-network'
  scope: sandboxRg
  params: {
    location: location
    tags: tags
    prefix: prefix
    principalId: core.outputs.identityPrincipalId
    userPrincipalId: principalId
  }
}

module foundry 'modules/foundry.bicep' = if (foundryOn) {
  name: 'foundry'
  scope: rg
  params: {
    location: location
    tags: tags
    prefix: prefix
    token: token
    principalId: core.outputs.identityPrincipalId
    userPrincipalId: principalId
    fastModel: {
      name: foundryFastModel
      format: foundryFastModelFormat
      version: foundryFastModelVersion
      sku: foundryFastSku
      capacity: foundryFastCapacity
    }
    expertModel: {
      name: foundryExpertModel == 'none' ? '' : foundryExpertModel
      format: foundryExpertModelFormat
      version: foundryExpertModelVersion
      sku: foundryExpertSku
      capacity: foundryExpertCapacity
    }
    expertProviderData: {
      organizationName: claudeOrganizationName
      countryCode: claudeCountryCode
      industry: claudeIndustry
    }
  }
}

module apps 'modules/apps.bicep' = {
  name: 'apps'
  scope: rg
  params: {
    location: location
    tags: tags
    prefix: prefix
    token: token
    identityId: core.outputs.identityId
    identityClientId: core.outputs.identityClientId
    identityPrincipalId: core.outputs.identityPrincipalId
    registryServer: core.outputs.registryLoginServer
    keyVaultUri: core.outputs.keyVaultUri
    logAnalyticsWorkspaceId: core.outputs.logAnalyticsWorkspaceId
    appInsightsConnectionString: core.outputs.appInsightsConnectionString
    cosmosEndpoint: core.outputs.cosmosEndpoint
    serviceBusNamespace: core.outputs.serviceBusNamespace
    serviceBusQueue: core.outputs.serviceBusQueue
    searchEndpoint: core.outputs.searchEndpoint
    itsmProvider: itsmProvider
    chatProvider: chatProvider
    fastBaseUrl: foundryOn ? foundry!.outputs.openaiBaseUrl : fastBaseUrl
    fastModel: foundryOn ? foundry!.outputs.fastDeployment : fastModel
    fastUseEntra: foundryOn
    fastReasoningEffort: fastReasoningEffort
    expertProvider: foundryOn ? (foundryExpertModelFormat == 'Anthropic' ? 'foundry' : 'openai_compat') : expertProvider
    expertModel: foundryOn ? foundry!.outputs.expertDeployment : expertModel
    foundryResource: foundryOn ? foundry!.outputs.name : foundryResource
    productSandboxImage: productSandboxImage
    productBootstrapDir: productBootstrapDir
    atlassianBaseUrl: atlassianBaseUrl
    atlassianUser: atlassianUser
    confluenceEnabled: confluenceEnabled == 'true'
    confluenceSpaces: confluenceSpaces
    jiraKbEnabled: jiraKbEnabled == 'true'
    jiraKbProjects: jiraKbProjects
    vmInstallerBlobUrl: vmInstallerBlobUrl
    vmSetupScript: vmSetupScript
    servicenowInstance: servicenowInstance
    servicenowUser: servicenowUser
    jiraBaseUrl: jiraBaseUrl
    jiraUser: jiraUser
    jiraProjectKey: jiraProjectKey
    ragApiUrl: ragApiUrl
    sharepointEnabled: sharepointEnabled == 'true'
    vmResourceGroup: vmSandbox ? sandboxRg.name : ''
    vmSubnetId: vmSandbox ? sandboxNet!.outputs.subnetId : ''
    entraClientId: entraClientId
    entraClientSecret: entraClientSecret
  }
}

output AZURE_LOCATION string = location
output AZURE_TENANT_ID string = tenant().tenantId
output AZURE_RESOURCE_GROUP string = rg.name
output AZURE_CONTAINER_REGISTRY_ENDPOINT string = core.outputs.registryLoginServer
output AZURE_CONTAINER_REGISTRY_NAME string = core.outputs.registryName
output AZURE_KEY_VAULT_NAME string = core.outputs.keyVaultName
output AZURE_MANAGED_IDENTITY_PRINCIPAL_ID string = core.outputs.identityPrincipalId
output AZURE_MANAGED_IDENTITY_CLIENT_ID string = core.outputs.identityClientId
output SERVICE_API_ENDPOINT string = apps.outputs.apiUrl
output SESSION_POOL_ENDPOINT string = apps.outputs.sessionPoolEndpoint
output PRODUCT_POOL_ENDPOINT string = apps.outputs.productPoolEndpoint
output AZURE_SEARCH_ENDPOINT string = core.outputs.searchEndpoint
output AZURE_FOUNDRY_NAME string = foundryOn ? foundry!.outputs.name : ''
output SANDBOX_INSTALLERS_URL string = vmSandbox ? sandboxNet!.outputs.installersUrl : ''
