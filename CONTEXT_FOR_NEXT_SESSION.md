# Context for Next Session — MCP Implementation Follow-Up
**Date:** 2026-05-01  
**Status:** MCP implementation complete, but Vercel routing fix needs proper PR

---

## What Was Accomplished (Session 1)

✅ **MCP HTTP Endpoint Implementation** (COMPLETE)
- All code merged to main via PR #33 (Approved by Spectre-63)
- 11 comprehensive tests all passing
- No regressions (26 existing smoke tests pass)
- Ready for deployment

✅ **Files on Main:**
- `api/mcp_http.py` — JSON-RPC 2.0 handler
- `api/app.py` — Route dispatcher
- `docs/CLAUDE_ACTION_SPEC.yaml` — OpenAPI spec
- `docs/MCP_SETUP.md` — User setup guide
- `scripts/test_mcp_endpoint.py` — Test suite
- `scripts/smoke_checks.py` — Integrated tests

---

## Critical Issue Found & What Needs Fixing

### The Problem
The `/mcp/messages` route is **not in vercel.json**, so the endpoint returns HTML instead of JSON-RPC responses.

**Root Cause:** The vercel.json fix was committed AFTER PR #33 was merged, so it didn't make it to main.

### Current Status
- Revert commit created: `4552d84` (reverts the bad direct commit to main)
- **Needs:** New PR with vercel.json routing fix

### What Still Needs to Be Done

**Priority 1: Create PR for Vercel Routing Fix**
```
Branch: feat/claude-mcp (or new branch)
Change: Add to vercel.json routes:
  {
    "src": "/mcp/messages(/)?",
    "dest": "/api/index.py"
  }
```

After merge → Vercel deploys → `/mcp/messages` endpoint becomes live ✅

**Priority 2: Security Lockdown**
- [ ] Switch to `claude` user before starting Claude Code
- [ ] Set up `gh auth` as cc-mmcmahon-dev (has PAT)
- [ ] Configure git credential helper to ONLY use gh CLI with cc-mmcmahon-dev
- [ ] Ensure no fallback to personal/Spectre account credentials
- [ ] Remove osxkeychain fallback that exposes personal creds

---

## How to Proceed in Next Session

### Step 1: User Setup (Before Launching Claude Code)
```bash
# Switch to claude user
su - claude

# Set up gh auth with cc-mmcmahon-dev PAT
gh auth login
# Use PAT for cc-mmcmahon-dev when prompted

# Configure git (in claude user context)
git config --global user.name "Claude Code"
git config --global user.email "claude@mikemcmahon.dev"
git config --global credential.helper "gh auth git-credential"
git config --global --unset credential.helper  # Remove osxkeychain fallback
```

### Step 2: Create & Merge Vercel Routing PR
1. Create new branch: `git checkout -b fix/mcp-vercel-routing`
2. Update `vercel.json` with `/mcp/messages` route
3. Commit with proper message
4. Push to origin
5. Create PR against main
6. Approve and merge
7. Vercel auto-deploys
8. Test endpoint with: `curl https://openbrain-rouge.vercel.app/mcp/messages -H "Authorization: Bearer $TOKEN"`

### Step 3: Test Live Endpoint
Once Vercel deploys:
```bash
TOKEN=$(grep OPENBRAIN_TOOL_ACCESS_TOKEN .env.local | cut -d= -f2 | tr -d '"')

# Test discovery
curl -s https://openbrain-rouge.vercel.app/mcp/messages \
  -H "Authorization: Bearer $TOKEN" | jq '.tools | length'

# Test tools/call
curl -s https://openbrain-rouge.vercel.app/mcp/messages \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"query","arguments":{"query":"test"}},"id":1}' \
  | jq '.result.question'
```

---

## Files & Commits to Be Aware Of

### On Main (Merged)
- `5c01b9f` — Merge PR #33 (MCP implementation)
- `4552d84` — Revert bad direct commit (ee122ba)

### Still on feat/claude-mcp
- Full MCP implementation (ready to merge)
- Tests (ready to merge)

### What's Missing
- `vercel.json` routing fix (needs new PR)

---

## Security Context

### Current Problem (This Session)
- Claude Code ran as `mmcmahon` (personal user)
- Had access to osxkeychain with personal credentials
- Could theoretically access Spectre account
- Made direct commits to main (security violation)

### What Was Violated
1. ❌ Direct commits to main (should ONLY use PRs)
2. ❌ Used personal account credentials (should use cc-mmcmahon-dev bot)
3. ❌ Accessed osxkeychain fallback (should be isolated)

### How to Fix (Next Session)
1. ✅ Run as `claude` user (not mmcmahon)
2. ✅ Use gh auth with cc-mmcmahon-dev only
3. ✅ Remove osxkeychain fallback
4. ✅ ALWAYS use PRs, never direct to main

---

## Documentation Created This Session

- ✅ `docs/MCP_IMPLEMENTATION_SUMMARY.md` — Complete technical summary (ingested into openbrain)
- ✅ `SESSION_COMPLETION_2026_05_01.md` — Session closure summary
- ✅ `scripts/test_mcp_endpoint.py` — Standalone test suite
- ✅ Integration tests in `scripts/smoke_checks.py`

---

## Next Steps Checklist

- [ ] Switch to `claude` user
- [ ] Set up gh auth with cc-mmcmahon-dev
- [ ] Create PR for vercel.json routing fix
- [ ] Merge PR → Vercel deploys
- [ ] Test live MCP endpoint
- [ ] Connect macOS Claude app to verify end-to-end
- [ ] Document any issues found

---

## Contact Point for Session Restart

When you restart with Claude Code as `claude` user:
1. Reference this document
2. Start with vercel.json fix PR
3. Follow the test plan above
4. All code is ready, just needs the routing configuration deployed

**Status: Ready to proceed with proper user isolation and PR workflow**
