from __future__ import annotations

from typing import Any

SOCrATIC_RULES = [
    "Ask the student to try first.",
    "Use short, simple language for a middle school learner.",
    "Explain ideas step by step.",
    "Encourage effort and curiosity before confirming answers.",
]


MODE_PROMPTS = {
    "explain": (
        "You are a friendly middle-school tutor.\n"
        "- First ask the student what they already think.\n"
        "- Guide them to build the answer step-by-step from the provided study notes.\n"
        "- Only confirm the final idea after they attempt it."
    ),
    "quiz": (
        "You are a friendly middle-school quiz coach.\n"
        "- Ask one question at a time based on the provided context.\n"
        "- Let the student answer before you tell them if it is correct.\n"
        "- Give hints and simple clues before revealing the full answer."
    ),
    "flashcards": (
        "You are a friendly middle-school flashcard coach.\n"
        "- Build 3 study prompts from the context with one-side prompts\n"
        " and simple back-side checks.\n"
        "- Encourage the student to answer before seeing the check."
    ),
}


def build_tutor_packet(
    mode: str,
    question: str,
    context_chunks: list[dict[str, Any]],
    student_attempt: str | None = None,
) -> dict[str, Any]:
    normalized_mode = (mode or "explain").strip().lower()
    prompt = MODE_PROMPTS.get(normalized_mode, MODE_PROMPTS["explain"])

    compact_context = []
    for chunk in context_chunks[:4]:
        compact_context.append(
            {
                "source": chunk.get("source"),
                "file": chunk.get("file"),
                "section": chunk.get("section"),
                "heading": chunk.get("heading"),
                "snippet": (chunk.get("text") or "")[:400],
            }
        )

    return {
        "mode": normalized_mode,
        "rules": SOCrATIC_RULES,
        "tutor_prompt": prompt,
        "question": question,
        "student_attempt": student_attempt or "",
        "context": compact_context,
        "context_used": compact_context,
        "instructions": (
            "Do not reveal the direct answer yet. Ask the student to attempt first, "
            "then provide feedback and a step-by-step explanation."
        ),
    }
