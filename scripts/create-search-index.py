"""Create the Azure AI Search index used as Known Error DB / resolution memory.

Usage: python scripts/create-search-index.py https://<service>.search.windows.net [index-name]
Auth: your az login (needs Search Service Contributor) via DefaultAzureCredential.
"""
import sys

import httpx
from azure.identity import DefaultAzureCredential

endpoint = sys.argv[1].rstrip("/")
index = sys.argv[2] if len(sys.argv) > 2 else "superagent-kedb"
token = DefaultAzureCredential().get_token("https://search.azure.com/.default").token
schema = {
    "name": index,
    "fields": [
        {"name": "id", "type": "Edm.String", "key": True, "filterable": True},
        {"name": "title", "type": "Edm.String", "searchable": True},
        {"name": "content", "type": "Edm.String", "searchable": True},
        {"name": "url", "type": "Edm.String"},
        {"name": "source", "type": "Edm.String", "filterable": True},
        {"name": "ticket_id", "type": "Edm.String", "filterable": True},
        {"name": "category", "type": "Edm.String", "filterable": True, "facetable": True},
    ],
}
r = httpx.put(f"{endpoint}/indexes/{index}?api-version=2024-07-01", json=schema,
              headers={"Authorization": f"Bearer {token}"})
print(r.status_code, r.text[:500])
