import hashlib
from pathlib import Path
from typing import Any


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
            text = "\n".join(
                (para.text or "").strip()
                for para in doc.paragraphs
                if para.text and para.text.strip()
            )
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
                "content_type": "docx",
                "subject": subject,
                "topic": topic,
            }
        )

    return documents
