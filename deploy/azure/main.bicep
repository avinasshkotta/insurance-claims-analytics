// Azure Container Apps for the claims console.
//
// One-time deploy (creates the environment, the app and the secret):
//   az deployment group create -g rg-claims-demo -f deploy/azure/main.bicep \
//     --parameters openaiApiKey="sk-..." image="ghcr.io/avinasshkotta/claims-console:latest"
//
// After that, GitHub Actions only updates the image; secrets and scale settings
// stay as declared here.

@description('Container App name and resource prefix')
param appName string = 'claims-console'

@description('Azure region. Check availability first: az provider show -n Microsoft.App --query "resourceTypes[?resourceType==\'managedEnvironments\'].locations" -o json')
param location string = 'centralindia'

@description('Container image. The first deploy can use any public image; CI replaces it.')
param image string = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'

@secure()
@description('OpenAI-compatible API key. Stored as a Container App secret, never in the repo.')
param openaiApiKey string

@description('Scale-to-zero by default; cap keeps a traffic spike from running up a bill.')
param minReplicas int = 0
param maxReplicas int = 2

param demoRpm int = 20
param demoDailyUsd string = '1.00'
param maxOutputTokens int = 500
param llmModel string = 'gpt-4o-mini'

var logsName = '${appName}-logs'
var envName = '${appName}-env'
var port = 8000

resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: logsName
  location: location
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

resource managedEnv 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: envName
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logs.properties.customerId
        sharedKey: logs.listKeys().primarySharedKey
      }
    }
  }
}

resource app 'Microsoft.App/containerApps@2024-03-01' = {
  name: appName
  location: location
  properties: {
    managedEnvironmentId: managedEnv.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: port
        transport: 'auto'
        allowInsecure: false
      }
      secrets: [
        {
          name: 'openai-key'
          value: openaiApiKey
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'app'
          image: image
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: [
            { name: 'OPENAI_API_KEY', secretRef: 'openai-key' }
            { name: 'DEMO_MODE', value: '1' }
            { name: 'DEMO_RPM', value: string(demoRpm) }
            { name: 'DEMO_DAILY_USD', value: demoDailyUsd }
            { name: 'DEMO_ALLOW_UPLOADS', value: '0' }
            { name: 'MAX_OUTPUT_TOKENS', value: string(maxOutputTokens) }
            { name: 'LLM_MODEL', value: llmModel }
          ]
          probes: [
            {
              type: 'startup'
              httpGet: {
                path: '/api/health'
                port: port
                scheme: 'HTTP'
              }
              initialDelaySeconds: 5
              periodSeconds: 10
              failureThreshold: 30
            }
            {
              type: 'readiness'
              httpGet: {
                path: '/api/health'
                port: port
                scheme: 'HTTP'
              }
              periodSeconds: 10
            }
          ]
        }
      ]
      scale: {
        minReplicas: minReplicas
        maxReplicas: maxReplicas
      }
    }
  }
}

output fqdn string = app.properties.configuration.ingress.fqdn
output url string = 'https://${app.properties.configuration.ingress.fqdn}'
