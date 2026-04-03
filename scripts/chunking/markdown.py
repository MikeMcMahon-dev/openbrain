def chunk_markdown(text, max_tokens=600):
    chunks = []
    current_lines = []
    current_heading = "root"

    for line in text.splitlines():
        if line.startswith("#"):
            if current_lines:
                _emit_chunks(chunks, current_heading, current_lines, max_tokens)
                current_lines = []
            current_heading = line.strip()
        current_lines.append(line)

    if current_lines:
        _emit_chunks(chunks, current_heading, current_lines, max_tokens)

    return chunks


def _emit_chunks(chunks, heading, lines, max_tokens):
    text = "\n".join(lines)
    words = text.split()
    if len(words) <= max_tokens:
        chunks.append({"heading": heading, "text": text})
        return
    for i in range(0, len(words), max_tokens):
        sub_text = " ".join(words[i : i + max_tokens])
        if sub_text.strip():
            chunks.append({"heading": heading, "text": sub_text})
