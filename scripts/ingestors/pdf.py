import base64
import hashlib
import io
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import fitz  # pymupdf
import pymupdf4llm
from PIL import Image, ImageEnhance
from pypdf import PdfReader

_VISION_PROMPT = (
    "Transcribe all text in this scanned document page as markdown.\n"
    "For any geometric diagrams, figures, or drawings, provide a brief description of what is\n"
    "shown (e.g., \"Circle with center O, radius labeled 5, chord AB drawn from A to B\").\n"
    "Preserve numbered lists, section headers, and question structure.\n"
    "If text is unclear due to scan quality, transcribe your best attempt and mark\n"
    "uncertain words with [?]."
)


def _classify_pdf(reader: PdfReader) -> tuple[str, dict]:
    """Classify a PDF as text_dominant, mixed, or image_dominant.

    Returns (classification, stats) where stats contains page_count,
    avg_chars_per_page, and image_page_pct.
    """
    page_count = len(reader.pages)
    char_counts = []
    image_page_count = 0
    for page in reader.pages:
        text = page.extract_text() or ""
        char_counts.append(len(text.strip()))
        if page.images:
            image_page_count += 1
    avg_chars = sum(char_counts) / page_count if page_count else 0
    image_page_pct = image_page_count / page_count if page_count else 0
    stats = {
        "page_count": page_count,
        "avg_chars_per_page": round(avg_chars, 1),
        "image_page_pct": round(image_page_pct, 2),
    }
    if avg_chars < 50:
        return "image_dominant", stats
    elif avg_chars < 200:
        return "mixed", stats
    else:
        return "text_dominant", stats


def _preprocess_page_image(page: fitz.Page, contrast: float = 2.0, sharpen: float = 1.5) -> str:
    """Render a pymupdf page to a pre-processed base64 PNG string."""
    pix = page.get_pixmap(dpi=150)
    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    img = img.convert("L")  # grayscale
    img = ImageEnhance.Contrast(img).enhance(contrast)
    img = ImageEnhance.Sharpness(img).enhance(sharpen)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.standard_b64encode(buf.getvalue()).decode()


def _call_vision_api(b64_image: str, model: str, api_key: str) -> str:
    """Call Anthropic vision API with a base64 image. Returns transcribed markdown text."""
    payload = json.dumps({
        "model": model,
        "max_tokens": 2048,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": b64_image,
                        },
                    },
                    {
                        "type": "text",
                        "text": _VISION_PROMPT,
                    },
                ],
            }
        ],
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["content"][0]["text"]


def _vision_ocr_pdf(file: Path, stats: dict) -> str:
    """Run vision OCR on an image-dominant PDF. Returns concatenated markdown."""
    api_key = os.getenv("ANTHROPIC_API_KEY") or os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError(
            "Vision OCR requires ANTHROPIC_API_KEY (or OPENROUTER_API_KEY) — "
            "neither is set. Set the env var and retry."
        )

    model = os.getenv("OPENBRAIN_OCR_MODEL", "claude-haiku-4-5-20251001")
    contrast = float(os.getenv("OPENBRAIN_OCR_CONTRAST", "2.0"))
    sharpen = float(os.getenv("OPENBRAIN_OCR_SHARPEN", "1.5"))

    doc = fitz.open(str(file))
    page_parts = []
    for page_num, page in enumerate(doc, start=1):
        print(f"  OCR page {page_num}/{stats['page_count']} — {file.name}")
        b64 = _preprocess_page_image(page, contrast=contrast, sharpen=sharpen)
        text = _call_vision_api(b64, model=model, api_key=api_key)
        page_parts.append(f"<!-- page {page_num} -->\n{text}")
    doc.close()

    return "\n\n".join(page_parts)


def _write_preview(file: Path, markdown: str) -> None:
    """Write markdown to a preview sidecar file. Best-effort — never raises."""
    try:
        preview_dir_env = os.getenv("OPENBRAIN_PREVIEW_DIR")
        preview_dir = Path(preview_dir_env) if preview_dir_env else Path.home() / ".openbrain" / "previews"
        preview_dir.mkdir(parents=True, exist_ok=True)
        preview_path = preview_dir / f"{file.stem}_preview.md"
        preview_path.write_text(markdown, encoding="utf-8")
        print(f"  Preview written: {preview_path}")
    except Exception as exc:
        print(f"  Warning: could not write preview for {file.name}: {exc}")


def load_pdf_documents(
    pdf_root: Path,
    subject: str | None = None,
    topic: str | None = None,
) -> list[dict[str, Any]]:
    documents = []

    for file in sorted(pdf_root.rglob("*.pdf")):
        try:
            reader = PdfReader(str(file))
            classification, stats = _classify_pdf(reader)
            print(
                f"PDF {file.name}: class={classification}, "
                f"pages={stats['page_count']}, "
                f"avg_chars={stats['avg_chars_per_page']}, "
                f"image_pct={stats['image_page_pct']}"
            )
        except Exception as exc:
            print(f"Error classifying PDF {file}: {exc}")
            continue

        # --- Route by classification ---
        markdown_text: str | None = None
        extra_meta: dict = {}

        if classification in ("text_dominant", "mixed"):
            try:
                markdown_text = pymupdf4llm.to_markdown(str(file))
            except Exception as exc:
                print(f"  pymupdf4llm failed for {file.name}: {exc}")
                continue
            if classification == "mixed" and stats["image_page_pct"] > 0.2:
                extra_meta["figures_present"] = True

        else:  # image_dominant
            try:
                markdown_text = _vision_ocr_pdf(file, stats)
            except RuntimeError as exc:
                # No API key — skip this file, don't abort the batch
                print(f"  Warning: skipping {file.name} — {exc}")
                documents.append(
                    {
                        "id": hashlib.md5(str(file).encode()).hexdigest(),
                        "text": "",
                        "source": str(file),
                        "file": file.name,
                        "section": str(file.parent).replace(str(pdf_root) + "/", ""),
                        "heading": "root",
                        "content_type": "pdf",
                        "subject": subject,
                        "topic": topic,
                        "status": "failed",
                        "reason": "requires_ocr_api",
                        "classification": classification,
                        **stats,
                    }
                )
                continue
            except Exception as exc:
                print(f"  Error during vision OCR for {file.name}: {exc}")
                continue

        if not markdown_text or not markdown_text.strip():
            print(f"  Skipping {file.name} — empty after extraction")
            continue

        _write_preview(file, markdown_text)

        doc_id = hashlib.md5(markdown_text.encode("utf-8")).hexdigest()
        documents.append(
            {
                "id": doc_id,
                "text": markdown_text,
                "source": str(file),
                "file": file.name,
                "section": str(file.parent).replace(str(pdf_root) + "/", ""),
                "heading": "root",
                "content_type": "markdown",
                "subject": subject,
                "topic": topic,
                "classification": classification,
                **stats,
                **extra_meta,
            }
        )

    return documents
