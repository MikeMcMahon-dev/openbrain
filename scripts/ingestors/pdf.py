import hashlib
from pathlib import Path
from typing import Any

from pypdf import PdfReader


def load_pdf_documents(
    pdf_root: Path,
    subject: str | None = None,
    topic: str | None = None,
) -> list[dict[str, Any]]:
    documents = []

    for file in sorted(pdf_root.rglob("*.pdf")):
        text_parts = []
        try:
            reader = PdfReader(str(file))
            for page in reader.pages:
                extracted = page.extract_text() or ""
                text_parts.append(extracted)
        except Exception as exc:
            print(f"Error reading PDF {file}: {exc}")
            continue

        text = "\n".join(part.strip() for part in text_parts if part and part.strip())
        if not text:
            continue

        doc_id = hashlib.md5(text.encode("utf-8")).hexdigest()
        documents.append(
            {
                "id": doc_id,
                "text": text,
                "source": str(file),
                "file": file.name,
                "section": str(file.parent).replace(str(pdf_root) + "/", ""),
                "heading": "root",
                "content_type": "pdf",
                "subject": subject,
                "topic": topic,
            }
        )

    return documents
