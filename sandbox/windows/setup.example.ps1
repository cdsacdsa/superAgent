# Example product setup for Windows sandbox VMs. SuperAgent runs this once on every new VM.
# Enable it: copy to config/sandbox/windows-setup.ps1, set SA_VM_SETUP_SCRIPT=/app/config/sandbox/windows-setup.ps1,
# upload your installer to the "installers" container of the sandbox storage account and set
# SA_VM_INSTALLER_BLOB_URL=https://<account>.blob.core.windows.net/installers/<file>.
# {installer_url} below is replaced with a 2-hour read-only link to that blob.
$ErrorActionPreference = "Stop"
New-Item -ItemType Directory -Force C:\sandbox | Out-Null
Invoke-WebRequest -Uri "{installer_url}" -OutFile C:\sandbox\product-setup.msi -UseBasicParsing
Start-Process msiexec.exe -ArgumentList "/i C:\sandbox\product-setup.msi /qn /norestart /l*v C:\sandbox\install.log" -Wait
Write-Output "product installed"
