# OpenBrain Agent Integration

OpenBrain is a personal and family RAG memory system accessible via three agent surfaces.

---

## 1. ChatGPT Custom GPTs

Three family Custom GPTs, one per user. Each uses the OpenAPI 3.1.0 action spec
at `docs/CUSTOM_GPT_ACTION_SPEC.yaml` and a per-user system prompt from `docs/gpt_instructions/`.

See `docs/CHATGPT_CONNECTOR.md` for full setup and auth details.

---

## 2. Claude Code MCP Server

`mcp_server/openbrain.py` — stdio MCP server registered via `.mcp.json` at project root.

Tools exposed natively in Claude Code sessions:
- `openbrain_query` — hybrid keyword + vector search over the vault
- `openbrain_ingest` — save notes or content to the vault
- `openbrain_generate_quiz` — generate quiz questions from vault content
- `openbrain_generate_flashcards` — generate flashcards from vault content

Reads `OPENBRAIN_TOOL_ACCESS_TOKEN` from `.env.local`, calls Vercel over HTTP.
Requires `mcp` and `httpx` packages (both in `requirements-full.txt`).

---

## 3. Claude API (tool_use format)

`api/claude.py` — thin HTTP adapter for the Claude native `tool_use` envelope.
Routes: `/claude_query`, `/claude_generate_quiz`, `/claude_generate_flashcards`, `/claude_ingest`

Same auth and owner resolution as ChatGPT adapter. Prioritises `input` key in payload.

---

## Supported Ingest Sources

| source_type | Description |
|------------|-------------|
| `text` | Inline content pasted directly as `source` field |
| `url` | Web URL to fetch and ingest |
| `pdf` | PDF file path (local ingest only) |
| `docx` | DOCX file path (local ingest only) |
| `obsidian` | Obsidian vault directory (CLI ingest only) |

---

## Tutor Behaviour

All query responses include:
- `rules` — list of tutor behaviour rules to apply in the response
- `tutor_prompt` — suggested opening line
- `context_used` — vault chunks used to build the response
- `results` — raw ranked results with scores and source metadata

See `docs/TUTOR_BEHAVIOR_CONTRACT.md` for full contract.

---

## Future Enhancements

- Student progress tracking (weak-topic detection across sessions)
- Adaptive quizzes that weight toward topics with low recent scores
- Per-user tutor behaviour tuning beyond system prompt guardrails
- K8s CronJob daemon for scheduled ingest and DB health evaluation
