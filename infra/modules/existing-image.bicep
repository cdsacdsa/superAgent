// The image a container app runs today, so re-provisioning never rolls back what CI or azd deployed.
param name string
param exists bool

resource app 'Microsoft.App/containerApps@2025-01-01' existing = if (exists) {
  name: name
}

output image string = exists ? app!.properties.template.containers[0].image : ''
