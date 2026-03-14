def chunk_markdown(text):
    chunks = []
    current_chunk = []
    current_heading = "root"

    for line in text.splitlines():
        if line.startswith("#"):
            if current_chunk:
                chunks.append({
                    "heading": current_heading,
                    "text": "\n".join(current_chunk),
                })
                current_chunk = []
            current_heading = line.strip()

        current_chunk.append(line)

    if current_chunk:
        chunks.append({
            "heading": current_heading,
            "text": "\n".join(current_chunk),
        })

    return chunks
