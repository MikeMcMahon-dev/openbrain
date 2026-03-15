import hashlib
from pathlib import Path
from typing import Any


def load_markdown_documents(
    vault_path: Path,
    subject: str | None = None,
    topic: str | None = None,
) -> list[dict[str, Any]]:
    documents = []
    markdown_files = list(vault_path.rglob("*.md"))

    for file in markdown_files:
        text = file.read_text(encoding="utf-8", errors="ignore")
        doc_id = hashlib.md5(text.encode("utf-8")).hexdigest()

        documents.append(
            {
                "id": doc_id,
                "text": text,
                "source": str(file),
                "file": file.name,
                "section": str(file.parent).replace(str(vault_path) + "/", ""),
                "heading": "root",
                "content_type": "markdown",
                "subject": subject,
                "topic": topic,
            }
        )

    return documents
