import hashlib
from typing import Any
from urllib.parse import urlparse

import requests
from markdownify import markdownify as md


def load_url_documents(
    url_or_urls,
    subject: str | None = None,
    topic: str | None = None,
) -> list[dict[str, Any]]:
    urls = [url_or_urls] if isinstance(url_or_urls, str) else list(url_or_urls or [])
    documents = []

    for url in urls:
        normalized = str(url).strip()
        if not normalized:
            continue

        parsed = urlparse(normalized)
        if not parsed.scheme.startswith("http"):
            print(f"Skipping invalid URL (expected http/https): {normalized}")
            continue

        try:
            response = requests.get(
                normalized,
                timeout=20,
                headers={"User-Agent": "OpenBrain/1.0 (+https://openbrain.local)"},
            )
            response.raise_for_status()
            text = md(response.text, heading_style="ATX", strip=["script", "style"])
        except Exception as exc:
            print(f"Error reading URL {normalized}: {exc}")
            continue

        if not text:
            continue

        doc_id = hashlib.md5(text.encode("utf-8")).hexdigest()
        documents.append(
            {
                "id": doc_id,
                "text": text,
                "source": normalized,
                "file": parsed.netloc or normalized,
                "section": "url",
                "heading": "root",
                "content_type": "markdown",
                "subject": subject,
                "topic": topic,
            }
        )

    return documents
