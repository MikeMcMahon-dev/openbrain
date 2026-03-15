from __future__ import annotations

from typing import Any

from api._openbrain_api import query_payload

MODE_OPTIONS = {"explain", "quiz", "flashcards"}


def _normalize_query(query: str) -> str:
    return (query or "").strip()


def _normalize_mode(value: str) -> str:
    candidate = (value or "explain").strip().lower()
    return candidate if candidate in MODE_OPTIONS else "explain"


def _normalize_results_count(value: str) -> int:
    try:
        parsed = int(value)
    except Exception:
        return 5
    return max(1, min(parsed, 20))


def render_mode_output(mode: str, packet: dict[str, Any]) -> None:
    print(f"\nQuery: {packet.get('question')}")
    print(f"Mode: {packet.get('mode', mode)}")
    print("\nTutor prompt:")
    print(packet.get("tutor_prompt", ""))

    rules = packet.get("rules") or []
    if rules:
        print("\nTutor rules:")
        for rule in rules:
            print(f" - {rule}")

    context_used = packet.get("context_used") or []
    results = packet.get("results") or []
    print(f"\nContext used: {len(context_used)}")
    print(f"Results: {len(results)}")
    if results:
        for result in results[:3]:
            print("\nSOURCE:", result.get("source") or result.get("file"))
            text = (result.get("text") or "")
            print((text or "")[:280])

    print("\n" + packet.get("instructions", ""))


def main() -> None:
    print("Supabase query mode (query_payload / openbrain API logic)")

    query = _normalize_query(input("\nAsk a question: ").strip())
    while not query:
        query = _normalize_query(input("Ask a question: ").strip())

    mode = _normalize_mode(input("Tutor mode (explain, quiz, flashcards): ").strip())

    n_results = _normalize_results_count(input("Number of results [5]: ").strip())

    payload = {
        "query": query,
        "mode": mode,
        "n_results": n_results,
    }

    status, response = query_payload(payload, {"method": "POST", "headers": {}, "query": {}})
    if status >= 400:
        print(f"Request failed with status {status}: {response}")
        return

    render_mode_output(mode, response)


if __name__ == "__main__":
    main()
