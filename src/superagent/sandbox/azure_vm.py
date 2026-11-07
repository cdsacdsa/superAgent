"""Ephemeral Azure VM sandbox (Windows or Linux) for full-OS reproductions.

Isolation model (provisioned by ``infra/modules/sandbox-vm-network.bicep`` when enabled):
* a dedicated resource group ``rg-<env>-sandbox``; the agent identity has VM/Network
  Contributor **only** there
* a VNet/subnet whose NSG denies all Internet egress and all inbound traffic
* no public IP, commands run through the Azure *Run Command* control plane
* VMs are tagged with the case id and a TTL; the worker deletes them on case close or expiry

Product setup: ``setup_script`` runs once on every new VM (e.g. installs your desktop product).
If ``installer_blob_url`` is set, ``{installer_url}`` in the script is replaced with a 2-hour,
read-only user-delegation SAS for that blob (the agent identity needs *Storage Blob Data Reader*
and *Storage Blob Delegator* on the installers storage account; no account keys are used).
"""

from __future__ import annotations

import asyncio
import logging
import re
import secrets
import time
from datetime import UTC, datetime, timedelta

from .base import ExecResult, Sandbox

log = logging.getLogger(__name__)

IMAGES = {
    "windows": {"publisher": "MicrosoftWindowsServer", "offer": "WindowsServer", "sku": "2022-datacenter-azure-edition-core", "version": "latest"},
    "linux": {"publisher": "Canonical", "offer": "ubuntu-24_04-lts", "sku": "server", "version": "latest"},
}  # fmt: skip


class AzureVmSandbox(Sandbox):
    name = "azure_vm"
    targets = ("windows", "linux")

    def __init__(self, subscription_id: str, resource_group: str, subnet_id: str, location: str,
                 vm_size: str = "Standard_B2s", ttl_minutes: int = 120, mi_client_id: str = "",
                 default_target: str = "windows", setup_script: str = "", installer_blob_url: str = ""):  # fmt: skip
        from azure.identity import DefaultAzureCredential
        from azure.mgmt.compute import ComputeManagementClient
        from azure.mgmt.network import NetworkManagementClient

        cred = DefaultAzureCredential(managed_identity_client_id=mi_client_id or None)
        self.cred = cred
        self.compute = ComputeManagementClient(cred, subscription_id)
        self.network = NetworkManagementClient(cred, subscription_id)
        self.rg, self.subnet_id, self.location = resource_group, subnet_id, location
        self.vm_size, self.ttl = vm_size, ttl_minutes
        self.default_target = default_target
        self.setup_script, self.installer_blob_url = setup_script, installer_blob_url
        self._os: dict[str, str] = {}

    @staticmethod
    def _vm_name(session_id: str) -> str:
        return ("sbx-" + re.sub(r"[^a-z0-9-]", "-", session_id.lower()))[:15].rstrip("-")

    def _ensure_vm(self, session_id: str, target: str) -> str:
        name = self._vm_name(session_id)
        try:
            self.compute.virtual_machines.get(self.rg, name)
            return name
        except Exception:  # noqa: BLE001 - not found -> create
            pass
        nic = self.network.network_interfaces.begin_create_or_update(
            self.rg, f"{name}-nic",
            {"location": self.location, "ip_configurations": [{"name": "ip", "subnet": {"id": self.subnet_id}}],
             "tags": {"superagent-case": session_id}},
        ).result()  # fmt: skip
        expires = (datetime.now(UTC) + timedelta(minutes=self.ttl)).isoformat()
        os_profile = {"computer_name": name, "admin_username": "sbxadmin", "admin_password": _password()}
        if target == "linux":
            os_profile["linux_configuration"] = {"disable_password_authentication": False}
        params = {
            "location": self.location,
            "tags": {"superagent-case": session_id, "superagent-expires": expires},
            "hardware_profile": {"vm_size": self.vm_size},
            "storage_profile": {
                "image_reference": IMAGES[target],
                "os_disk": {"create_option": "FromImage", "delete_option": "Delete",
                            "managed_disk": {"storage_account_type": "StandardSSD_LRS"}},
            },  # fmt: skip
            "os_profile": os_profile,
            "network_profile": {"network_interfaces": [{"id": nic.id, "delete_option": "Delete"}]},
            "security_profile": {"security_type": "TrustedLaunch",
                                 "uefi_settings": {"secure_boot_enabled": True, "v_tpm_enabled": True}},
        }  # fmt: skip
        log.info("creating sandbox VM %s (%s) for %s", name, target, session_id)
        self.compute.virtual_machines.begin_create_or_update(self.rg, name, params).result()
        self._os[name] = target
        if self.setup_script:
            res = self._run_command(name, self.render_setup_script(), target)
            log.info("sandbox VM %s setup finished (exit %s): %s", name, res.exit_code, res.stderr[:500])
        return name

    def render_setup_script(self) -> str:
        script = self.setup_script
        if "{installer_url}" in script and self.installer_blob_url:
            script = script.replace("{installer_url}", self._installer_sas_url())
        return script

    def _installer_sas_url(self) -> str:
        from urllib.parse import urlparse

        from azure.storage.blob import BlobSasPermissions, BlobServiceClient, generate_blob_sas

        u = urlparse(self.installer_blob_url)
        account = u.netloc.split(".")[0]
        container, _, blob = u.path.lstrip("/").partition("/")
        now = datetime.now(UTC)
        key = BlobServiceClient(f"{u.scheme}://{u.netloc}", credential=self.cred).get_user_delegation_key(
            now - timedelta(minutes=5), now + timedelta(hours=2)
        )
        sas = generate_blob_sas(account, container, blob, user_delegation_key=key,
                                permission=BlobSasPermissions(read=True), expiry=now + timedelta(hours=2))  # fmt: skip
        return f"{self.installer_blob_url}?{sas}"

    def _run(self, session_id: str, command: str, target: str) -> ExecResult:
        return self._run_command(self._ensure_vm(session_id, target), command, target)

    def _run_command(self, name: str, command: str, target: str) -> ExecResult:
        command_id = "RunPowerShellScript" if target == "windows" else "RunShellScript"
        t0 = time.monotonic()
        res = self.compute.virtual_machines.begin_run_command(
            self.rg, name, {"command_id": command_id, "script": command.splitlines() or [command]}
        ).result()
        out, err = "", ""
        for v in res.value or []:
            code = (v.code or "").lower()
            if "stderr" in code:
                err += v.message or ""
            else:
                out += v.message or ""
        return ExecResult(1 if err.strip() else 0, out, err, int((time.monotonic() - t0) * 1000))

    async def exec(self, session_id, command, timeout_s, network="none", target: str | None = None):
        return await asyncio.to_thread(self._run, session_id, command, target or self.default_target)

    async def destroy(self, session_id):
        name = self._vm_name(session_id)

        def _delete():
            try:
                self.compute.virtual_machines.begin_delete(self.rg, name).result()  # NIC + disk deleted with VM
            except Exception as e:  # noqa: BLE001
                log.info("sandbox VM %s not deleted: %s", name, e)

        await asyncio.to_thread(_delete)

    async def reap_expired(self) -> int:
        """Delete sandbox VMs whose TTL expired (called by the worker's housekeeping loop)."""

        def _reap() -> int:
            n = 0
            now = datetime.now(UTC)
            for vm in self.compute.virtual_machines.list(self.rg):
                exp = (vm.tags or {}).get("superagent-expires")
                if exp and datetime.fromisoformat(exp) < now:
                    self.compute.virtual_machines.begin_delete(self.rg, vm.name)
                    n += 1
            return n

        return await asyncio.to_thread(_reap)


def _password() -> str:
    # Random, never stored or shown: access is only via Run Command.
    return "Sbx!" + secrets.token_urlsafe(18) + "9a"
