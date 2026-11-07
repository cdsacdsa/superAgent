"""Turn a folder of documents (Markdown, text, HTML) into Azure AI Search documents.

Used by ``scripts/ingest-docs.py``. Markdown is split at headings so every chunk is a
self-contained section; long sections are split again at paragraph boundaries.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from pathlib import Path

from .atlassian import _html_text

EXTENSIONS = {".md", ".markdown", ".txt", ".html", ".htm"}


def chunk_markdown(text: str, max_chars: int = 2000) -> list[tuple[str, str]]:
    """Return ``(heading, body)`` chunks."""
    sections: list[tuple[str, list[str]]] = [("", [])]
    for line in text.splitlines():
        m = re.match(r"^(#{1,3})\s+(.*)", line)
        if m:
            sections.append((m.group(2).strip(), []))
        else:
            sections[-1][1].append(line)
    chunks: list[tuple[str, str]] = []
    for heading, lines in sections:
        body = "\n".join(lines).strip()
        if not body:
            continue
        buf = ""
        for para in re.split(r"\n\s*\n", body):
            if buf and len(buf) + len(para) + 2 > max_chars:
                chunks.append((heading, buf))
                buf = ""
            buf = f"{buf}\n\n{para}" if buf else para
        if buf:
            chunks.append((heading, buf[: max_chars * 2]))
    return chunks


def iter_documents(folder: str | Path, source: str = "docs", base_url: str = "") -> Iterator[dict]:
    root = Path(folder)
    for path in sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in EXTENSIONS):
        rel = path.relative_to(root).as_posix()
        raw = path.read_text(encoding="utf-8", errors="replace")
        text = _html_text(raw) if path.suffix.lower() in (".html", ".htm") else raw
        title_match = re.search(r"^#\s+(.+)$", text, re.M)
        doc_title = title_match.group(1).strip() if title_match else path.stem.replace("-", " ").title()
        url = f"{base_url.rstrip('/')}/{rel}" if base_url else rel
        for i, (heading, body) in enumerate(chunk_markdown(text)):
            yield {
                "id": hashlib.sha1(f"{source}:{rel}:{i}".encode()).hexdigest(),
                "title": doc_title if not heading or heading == doc_title else f"{doc_title}: {heading}",
                "content": body,
                "url": url,
                "source": source,
                "category": path.parent.name if path.parent != root else "",
            }
