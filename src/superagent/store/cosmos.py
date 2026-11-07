"""Azure Cosmos DB (NoSQL, serverless) store. Authenticates with the managed identity
(role *Cosmos DB Built-in Data Contributor*, assigned by the Bicep).

Containers: ``cases`` (partition key ``/id``) and ``events`` (partition key ``/case_id``).
Optimistic concurrency uses Cosmos' native ``_etag`` with ``If-Match``.
"""

from __future__ import annotations

import json

from azure.core import MatchConditions
from azure.cosmos import exceptions
from azure.cosmos.aio import CosmosClient

from ..models import Case, CaseEvent
from .base import CaseStore, ConflictError


class CosmosStore(CaseStore):
    def __init__(self, endpoint: str, database: str, mi_client_id: str = "", key: str = ""):
        if key:
            self.client = CosmosClient(endpoint, credential=key)
        else:
            from azure.identity.aio import DefaultAzureCredential

            self.client = CosmosClient(endpoint, credential=DefaultAzureCredential(managed_identity_client_id=mi_client_id or None))
        db = self.client.get_database_client(database)
        self.cases = db.get_container_client("cases")
        self.ev = db.get_container_client("events")

    async def aclose(self):
        await self.client.close()

    @staticmethod
    def _from_doc(doc: dict) -> Case:
        c = Case.model_validate_json(doc["doc"])
        c.etag = doc["_etag"]
        return c

    async def get(self, case_id):
        try:
            return self._from_doc(await self.cases.read_item(case_id, partition_key=case_id))
        except exceptions.CosmosResourceNotFoundError:
            return None

    async def save(self, case):
        # Override: Cosmos generates the etag itself.
        from ..models import utcnow

        case.updated_at = utcnow()
        body = {"id": case.id, "status": case.status.value, "updated_at": case.updated_at.isoformat(),
                "doc": case.model_copy(update={"etag": None}).model_dump_json()}  # fmt: skip
        try:
            if case.etag:
                res = await self.cases.replace_item(case.id, body, etag=case.etag,
                                                    match_condition=MatchConditions.IfNotModified)  # fmt: skip
            else:
                res = await self.cases.create_item(body)
        except (exceptions.CosmosAccessConditionFailedError, exceptions.CosmosResourceExistsError) as e:
            raise ConflictError(case.id) from e
        case.etag = res["_etag"]
        return case

    async def _write(self, case, expected_etag):  # pragma: no cover - save() is overridden
        raise NotImplementedError

    async def list(self, status=None, limit=200):
        q = "SELECT * FROM c"
        params: list[dict] = []
        if status:
            q += " WHERE ARRAY_CONTAINS(@s, c.status)"
            params.append({"name": "@s", "value": [s.value for s in status]})
        q += f" ORDER BY c.updated_at DESC OFFSET 0 LIMIT {int(limit)}"
        items = self.cases.query_items(q, parameters=params)
        return [self._from_doc(d) async for d in items]

    async def add_event(self, event):
        await self.ev.create_item(json.loads(event.model_dump_json()))

    async def events(self, case_id, limit=500):
        items = self.ev.query_items(
            f"SELECT * FROM c WHERE c.case_id=@id ORDER BY c.at ASC OFFSET 0 LIMIT {int(limit)}",
            parameters=[{"name": "@id", "value": case_id}],
            partition_key=case_id,
        )
        return [CaseEvent.model_validate(d) async for d in items]
