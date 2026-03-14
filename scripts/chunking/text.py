import re


def chunk_text_by_tokens(text, max_tokens=500, overlap=100, min_tokens=80):
    cleaned = re.sub(r"\s+", " ", text).strip()
    if not cleaned:
        return []

    tokens = cleaned.split(" ")
    if len(tokens) <= max_tokens:
        return [{"heading": "root", "text": cleaned}]

    if overlap >= max_tokens:
        overlap = max(0, max_tokens // 2)

    step = max(max_tokens - overlap, 1)
    chunks = []

    for start in range(0, len(tokens), step):
        end = start + max_tokens
        chunk_tokens = tokens[start:end]
        if len(chunk_tokens) < min_tokens and start > 0:
            break

        chunks.append({
            "heading": "root",
            "text": " ".join(chunk_tokens),
        })

        if end >= len(tokens):
            break

    return chunks
