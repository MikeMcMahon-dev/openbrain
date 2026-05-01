# MCP Setup — Connect OpenBrain to Claude.ai

OpenBrain now exposes an **MCP (Model Context Protocol) endpoint** that allows you to use your knowledge vault directly in Claude.ai (both web and macOS app).

## What is MCP?

MCP is a standard protocol that allows AI assistants (like Claude) to call external tools reliably. With OpenBrain's MCP endpoint, Claude can:
- Query your knowledge vault
- Ingest new content
- Generate quizzes
- Generate flashcards

## Connect in Claude.ai macOS App

### Step 1: Get Your Token
Your OpenBrain token: `opbr_ZTn4cWWIUuhpBppbtSmgPEgSAVMr8VGJjJmRz3`

Your owner ID: `mike.mcmahon67`

### Step 2: Add Custom Connector

1. Open Claude.ai macOS app
2. Go to **Settings** > **Connectors**
3. Click **Add Custom Connector**
4. Fill in:
   - **URL:** `https://openbrain-rouge.vercel.app/mcp/messages`
   - **User ID:** `mike.mcmahon67`
   - **Token:** `opbr_ZTn4cWWIUuhpBppbtSmgPEgSAVMr8VGJjJmRz3`
5. Click **Connect**
6. Status should show **Connected** ✅

### Step 3: Use in Chat

Once connected, you can ask Claude:

**Query your vault:**
- "Query my brain about [topic]"
- "What do I know about photosynthesis?"
- "Find notes on Kubernetes RBAC"

**Ingest new content:**
- "Save this to my vault: [content]" (with optional subject/topic)
- "Add this URL to my vault: [URL]"

**Generate quizzes:**
- "Generate a quiz about [topic]"
- "Create quiz questions on cell division"

**Generate flashcards:**
- "Make flashcards for [topic]"
- "Create study cards on photosynthesis"

## Connect in Claude.ai Web (claude.ai)

### Step 1: Open the Claude.ai Web App

Go to [claude.ai](https://claude.ai) in your browser.

### Step 2: Settings & Tools

1. Click your profile icon → **Settings**
2. Look for **Tools** or **Connectors** section
3. Click **Add Tool** or **Add Custom Connection**
4. Select **MCP** or **Custom Protocol**

### Step 3: Enter MCP Endpoint Details

- **URL:** `https://openbrain-rouge.vercel.app/mcp/messages`
- **Token:** `opbr_ZTn4cWWIUuhpBppbtSmgPEgSAVMr8VGJjJmRz3`
- **User ID:** `mike.mcmahon67`

### Step 4: Confirm

Click **Connect** or **Add** to enable the tools.

## Available Tools

### 1. `query`
Search your knowledge vault using hybrid keyword + vector retrieval.

**Parameters:**
- `query` (required) — The question or topic to look up
- `n_results` (optional) — Max chunks to return (default: 5)
- `mode` (optional) — Response mode: `explain`, `quiz`, `flashcards` (default: `explain`)
- `student_attempt` (optional) — Your attempt at an answer (tutor will respond to it)

**Returns:**
- Relevant chunks from your vault
- Tutor guidance rules
- Suggested tutor prompt
- Search results with scores and sources

---

### 2. `ingest`
Save new content to your knowledge vault.

**Parameters:**
- `source_type` (required) — Type of content: `text`, `url`
- `source` (required) — For `text`: the content. For `url`: the URL to fetch
- `subject` (optional) — Subject label (e.g., "Biology", "Kubernetes")
- `topic` (optional) — Topic tag (e.g., "Photosynthesis", "RBAC")

**Returns:**
- `ingest_id` — Unique identifier for tracing
- `status` — `accepted`, `queued`, or `failed`
- Confirmation message

---

### 3. `generate_quiz`
Generate quiz questions from your vault on a given topic.

**Parameters:**
- `query` (required) — Topic to generate quiz questions about
- `n_results` (optional) — Number of chunks to draw from (default: 5)

**Returns:**
- Quiz questions formatted for presentation
- Tutor context and guidance rules
- Source material used

---

### 4. `generate_flashcards`
Generate flashcard decks from your vault on a given topic.

**Parameters:**
- `query` (required) — Topic to generate flashcards for
- `n_results` (optional) — Number of chunks to draw from (default: 5)

**Returns:**
- Front/back flashcard pairs
- Formatted for spaced repetition study
- Source material references

---

## Authentication

All requests are authenticated using **Bearer token authentication**:

```
Authorization: Bearer opbr_ZTn4cWWIUuhpBppbtSmgPEgSAVMr8VGJjJmRz3
```

The MCP endpoint validates your token and owner ID before executing any tool.

## Troubleshooting

### "Connection Failed" Error

**Problem:** Connector shows "connect" instead of "connected"

**Solution:**
- Verify URL is exactly: `https://openbrain-rouge.vercel.app/mcp/messages`
- Check token is correct: `opbr_ZTn4cWWIUuhpBppbtSmgPEgSAVMr8VGJjJmRz3`
- Check user ID is correct: `mike.mcmahon67`
- Try connecting again

### "Unauthorized" Error

**Problem:** Tools fail with 401 error

**Solution:**
- Verify your token has not been rotated
- Check that the token is being passed correctly as Bearer token
- Try re-adding the connector

### Tool Calls Failing

**Problem:** Tools execute but return errors

**Solution:**
- Check that your query is specific and clear
- Verify you have content in your vault to search
- For ingest, check that source_type and source are provided
- Review error message for more details

## API Endpoint Details

**Endpoint:** `https://openbrain-rouge.vercel.app/mcp/messages`

**Protocol:** JSON-RPC 2.0 over HTTP

**Methods:**
- `POST /mcp/messages` — Execute JSON-RPC method calls
- `GET /mcp/messages` — Discover available tools (schema)

**Response Format:**
```json
{
  "jsonrpc": "2.0",
  "result": { /* tool result */ },
  "id": "request-id"
}
```

---

## For Family Members

If you're Beth (snapple01) or Annie (anneliesepaige), your tokens are different. Contact Mike for your personal token and owner ID, then follow the same setup steps above.

---

## Questions?

See `/docs/MCP_CONTRACT.md` for the full technical specification, or `/docs/CHATGPT_CONNECTOR.md` for ChatGPT-specific integration.
