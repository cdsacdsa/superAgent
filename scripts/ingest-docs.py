"""Index a folder of documents (Markdown, text, HTML) into the agent's Azure AI Search index.

Usage:
  python scripts/ingest-docs.py https://<service>.search.windows.net <folder> [--index superagent-kedb]
         [--source docs] [--base-url https://docs.contoso.com]

Creates the index if needed (same schema as scripts/create-search-index.py) and upserts one
search document per section, so re-running after edits updates the content in place.
Auth: your az login via DefaultAzureCredential (needs Search Service Contributor and
Search Index Data Contributor; the Bicep grants both to the user who runs azd).
"""

import argparse
import subprocess
import sys
from pathlib import Path

import httpx
from azure.identity import DefaultAzureCredential

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from superagent.knowledge.ingest import iter_documents  # noqa: E402

API = "2024-07-01"

p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
p.add_argument("endpoint")
p.add_argument("folder")
p.add_argument("--index", default="superagent-kedb")
p.add_argument("--source", default="docs")
p.add_argument("--base-url", default="")
args = p.parse_args()

endpoint = args.endpoint.rstrip("/")
create = Path(__file__).with_name("create-search-index.py")
subprocess.run([sys.executable, str(create), endpoint, args.index], check=True)

token = DefaultAzureCredential().get_token("https://search.azure.com/.default").token
headers = {"Authorization": f"Bearer {token}"}
docs = list(iter_documents(args.folder, args.source, args.base_url))
for i in range(0, len(docs), 500):
    batch = [{"@search.action": "mergeOrUpload", **d} for d in docs[i : i + 500]]
    r = httpx.post(f"{endpoint}/indexes/{args.index}/docs/index?api-version={API}", json={"value": batch},
                   headers=headers, timeout=60)
    r.raise_for_status()
print(f"indexed {len(docs)} sections from {args.folder} into {args.index}")
