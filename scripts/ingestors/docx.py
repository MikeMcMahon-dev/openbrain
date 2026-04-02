import hashlib
from pathlib import Path
from typing import Any

HEADING_MAP = {
    "Heading 1": "#",
    "Heading 2": "##",
    "Heading 3": "###",
    "Heading 4": "####",
    "Title": "#",
}


def load_docx_documents(
    docx_root: Path,
    subject: str | None = None,
    topic: str | None = None,
) -> list[dict[str, Any]]:
    try:
        from docx import Document
    except Exception as exc:
        raise RuntimeError(
            "DOCX ingestion requires the 'python-docx' package. "
            "Add it to requirements and install before running DOCX ingest."
        ) from exc

    documents = []

    for file in sorted(docx_root.rglob("*.docx")):
        try:
            doc = Document(str(file))
            lines = []
            for para in doc.paragraphs:
                if not para.text.strip():
                    continue
                prefix = HEADING_MAP.get(para.style.name, "")
                lines.append(f"{prefix} {para.text}".strip() if prefix else para.text)
            text = "\n".join(lines)
        except Exception as exc:
            print(f"Error reading DOCX {file}: {exc}")
            continue

        if not text:
            continue

        doc_id = hashlib.md5(text.encode("utf-8")).hexdigest()
        documents.append(
            {
                "id": doc_id,
                "text": text,
                "source": str(file),
                "file": file.name,
                "section": str(file.parent).replace(str(docx_root) + "/", ""),
                "heading": "root",
                "content_type": "markdown",
                "subject": subject,
                "topic": topic,
            }
        )

    return documents
