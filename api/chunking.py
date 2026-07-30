"""Heading-based document chunking (ADR-017).

Splits a markdown document into per-section chunks so a narrow sub-topic query
matches one tight section instead of a diluted whole-doc embedding (the precision
gate measured a mean +0.217 cosine gain, section vs whole).

Pure and side-effect-free: no DB, no embedding calls. `chunk_document` returns
chunk dicts with `content` (clean display text) and `embed_text` (the text to embed,
= document title prefixed onto the section so a section still carries the parent
entity). The caller supplies title/inherited fields and computes the embedding.
"""
from __future__ import annotations

import re
from typing import Any

_HEADING_RE = re.compile(r"^(#{1,6})\s+(\S.*)$")
_MIN_CHUNK_WORDS = 40   # sections shorter than this merge into a neighbour
_MAX_CHUNK_WORDS = 800  # sections longer than this are sub-split (rare in this corpus)


def _wc(text: str) -> int:
    return len((text or "").split())


def infer_title(content: str) -> str | None:
    """Document title = first heading, else first non-empty line (truncated)."""
    for ln in (content or "").splitlines():
        m = _HEADING_RE.match(ln)
        if m:
            return m.group(2).strip()
    for ln in (content or "").splitlines():
        if ln.strip():
            return ln.strip()[:120]
    return None


def split_sections(content: str) -> list[tuple[str | None, str]]:
    """Split markdown into (heading|None, section_text) pairs. Text before the first
    heading is a preamble section with heading=None. Section text retains its heading
    line so a fetched chunk reads as natural markdown."""
    lines = (content or "").splitlines()
    sections: list[tuple[str | None, str]] = []
    cur_head: str | None = None
    cur_lines: list[str] = []
    for ln in lines:
        m = _HEADING_RE.match(ln)
        if m:
            if cur_lines:
                sections.append((cur_head, "\n".join(cur_lines).strip()))
            cur_head = m.group(2).strip()
            cur_lines = [ln]
        else:
            cur_lines.append(ln)
    if cur_lines:
        sections.append((cur_head, "\n".join(cur_lines).strip()))
    return [(h, t) for h, t in sections if t.strip()]


def _merge_tiny(
    sections: list[tuple[str | None, str]],
    min_words: int,
) -> list[tuple[str | None, str]]:
    """Merge sections under `min_words` forward into the next (a trailing tiny section
    merges back into the previous) so no fragment gets its own embedding."""
    out: list[tuple[str | None, str]] = []
    pending: tuple[str | None, str] | None = None
    for h, t in sections:
        if pending is not None:
            ph, pt = pending
            h = ph if ph is not None else h  # leading section's heading wins
            t = (pt + "\n\n" + t).strip()
            pending = None
        if _wc(t) < min_words:
            pending = (h, t)
        else:
            out.append((h, t))
    if pending is not None:
        if out:
            lh, lt = out[-1]
            out[-1] = (lh, (lt + "\n\n" + pending[1]).strip())
        else:
            out.append(pending)  # whole doc is tiny — one chunk
    return out


def _split_large(
    heading: str | None,
    text: str,
    max_words: int,
) -> list[tuple[str | None, str]]:
    """Greedily pack paragraphs of an oversized section into <= max_words groups,
    each keeping the section heading. Rare on this corpus; keeps embeddings in-window."""
    if _wc(text) <= max_words:
        return [(heading, text)]
    paras = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    groups: list[tuple[str | None, str]] = []
    buf: list[str] = []
    n = 0
    for p in paras:
        pw = _wc(p)
        if buf and n + pw > max_words:
            groups.append((heading, "\n\n".join(buf).strip()))
            buf, n = [], 0
        buf.append(p)
        n += pw
    if buf:
        groups.append((heading, "\n\n".join(buf).strip()))
    return groups


def _embed_text(title: str | None, section_text: str) -> str:
    """Prefix the document title so a section still embeds as being about the parent
    entity — unless the section already leads with that title (avoid duplication).
    Strips leading markdown heading marks before comparing so an H1 section that IS
    the title is not prefixed with its own title again."""
    if not title:
        return section_text
    body_start = section_text.lstrip().lstrip("#").lstrip()
    if body_start[: len(title)].lower() == title.lower():
        return section_text
    return f"{title}\n\n{section_text}"


def chunk_document(
    content: str,
    title: str | None = None,
    *,
    min_words: int = _MIN_CHUNK_WORDS,
    max_words: int = _MAX_CHUNK_WORDS,
) -> list[dict[str, Any]]:
    """Split a document into chunk dicts.

    Returns a list of {chunk_index, heading, content, embed_text}. A document with no
    headings (or one short section) yields a single chunk equal to the whole doc, so
    short notes behave exactly as pre-chunking. Empty content yields [].
    """
    content = (content or "").strip()
    if not content:
        return []
    if title is None:
        title = infer_title(content)

    sections = split_sections(content)
    if not sections:  # no headings and no body captured — treat whole doc as one chunk
        sections = [(title, content)]

    sections = _merge_tiny(sections, min_words)
    expanded: list[tuple[str | None, str]] = []
    for h, t in sections:
        expanded.extend(_split_large(h, t, max_words))

    return [
        {
            "chunk_index": idx,
            "heading": h,
            "content": t,
            "embed_text": _embed_text(title, t),
        }
        for idx, (h, t) in enumerate(expanded)
    ]


def collapse_chunks(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse chunk-level retrieval results to one row per parent document (ADR-017).

    Keeps the best-scoring chunk per `document_id`, attaches a `sibling_chunks` pointer
    to the document's other retrieved chunks, and orders by best score. Rows without a
    `document_id` (unchunked `knowledge`) are each their own document — a no-op — so this
    is safe to apply on either store. Also prevents a boosted living doc (ADR-014) from
    presenting as N boosted rows.
    """
    groups: dict[Any, list[dict[str, Any]]] = {}
    order: list[Any] = []
    for r in results:
        key = r.get("document_id") or r.get("id")
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(r)

    collapsed: list[dict[str, Any]] = []
    for key in order:
        members = groups[key]
        best_i = max(range(len(members)), key=lambda i: (members[i].get("score") or 0))
        best = dict(members[best_i])
        siblings = [
            {"id": m.get("id"), "chunk_index": m.get("chunk_index"), "heading": m.get("heading")}
            for i, m in enumerate(members)
            if i != best_i
        ]
        if siblings:
            best["sibling_chunks"] = siblings
        collapsed.append(best)

    collapsed.sort(key=lambda x: (x.get("score") or 0), reverse=True)
    return collapsed
