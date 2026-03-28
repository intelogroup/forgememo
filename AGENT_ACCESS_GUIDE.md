# How AI Agents Access Forgemem Database

## Quick Answer

Any AI agent working in their project terminal/session can access the Forgemem memory database through the HTTP API running on `http://127.0.0.1:5555`.

```
Agent Terminal/Session
        ↓ (HTTP request)
   Forgemem API Server (port 5555)
        ↓ (SQLite query)
   forgemem_memory.db
```

---

## The 3 Main Patterns

### Pattern 1: Direct MCP (Claude Code Only)

**Claude Code has NATIVE access** via the existing MCP server:

```python
# In Claude Code terminal - these are automatically available:
search_memory(query="caching", project="my-app")
save_trace(type="success", content="...", project="my-app")
```

**No setup needed** - Claude Code already knows how to use Forgemem!

---

### Pattern 2: HTTP API from Terminal (All Agents)

**Any agent can call the HTTP API from their project terminal:**

```bash
# Copilot in VS Code terminal
curl -X POST http://127.0.0.1:5555/traces \
  -H "Content-Type: application/json" \
  -d '{"type":"success","content":"...","project":"my-app"}'

# Or via Python
import requests
resp = requests.post("http://127.0.0.1:5555/traces", json={...})
```

---

### Pattern 3: Wrapper Functions (Easiest for Daily Use)

**Add to shell profile for one-command access:**

```bash
# Add to ~/.zshrc
forgemem() {
  curl -X POST http://127.0.0.1:5555/traces \
    -H "Content-Type: application/json" \
    -d "{\"type\":\"${1:-note}\",\"content\":\"$2\",\"project\":\"$3\"}"
}

# Then just:
forgemem success "Fixed auth bug" "my-project"
```

---

## Detailed Access Methods by Agent Type

### Claude Code

**Option A: MCP (Built-in)**
- No code needed
- Uses existing `mcp_server.py`
- Direct SQLite access
- Available in Claude Code terminal automatically

**Option B: HTTP (More flexible)**
```python
import requests

# Save a learning
resp = requests.post("http://127.0.0.1:5555/traces", json={
    "type": "success",
    "content": "Fixed N+1 query with joins",
    "project": "my-app",
    "principle": "Always use database indexes",
    "score": 8
})

# Search past learnings
resp = requests.get("http://127.0.0.1:5555/search", 
    params={"q": "database optimization", "project": "my-app"})
```

---

### GitHub Copilot

**Via Terminal Commands:**
```bash
# Simple curl wrapper
curl "http://127.0.0.1:5555/search?q=caching&project=my-app"

# Or Python script
python3 << 'EOF'
import requests
resp = requests.post("http://127.0.0.1:5555/traces", json={...})
print(resp.json())
EOF
```

**Via Makefile (for project teams):**
```makefile
learn:
	@curl -X POST http://127.0.0.1:5555/traces \
	  -H "Content-Type: application/json" \
	  -d '{"type":"note","content":"...","project":"$(PROJECT)"}'

search:
	@curl "http://127.0.0.1:5555/search?q=$(QUERY)&project=$(PROJECT)"
```

---

### Google Gemini / Claude API

**Add as custom tool:**
```json
{
  "name": "forgemem_search",
  "description": "Search project learnings and principles",
  "url": "http://127.0.0.1:5555/search",
  "method": "GET",
  "parameters": {
    "q": "search query",
    "project": "project name"
  }
}
```

Then Gemini can call it directly in prompts:
```
"Search Forgemem for caching patterns: forgemem_search(q='caching', project='api')"
```

---

### Any Other Language/Framework

**HTTP is universal:**

**JavaScript:**
```javascript
const resp = await fetch("http://127.0.0.1:5555/traces", {
  method: "POST",
  body: JSON.stringify({type: "success", content: "...", project: "app"})
});
```

**Go:**
```go
req, _ := http.NewRequest("GET", "http://127.0.0.1:5555/search?q=test", nil)
resp, _ := client.Do(req)
```

**Rust:**
```rust
client.get("http://127.0.0.1:5555/stats").send()
```

---

## Real-World Setup for Your Projects

### Start the API Server

```bash
# Option 1: Development (manual)
python3 ~/Developer/Forgemem/forgemem_api.py

# Option 2: Production (auto-restart on macOS)
launchctl load ~/Library/LaunchAgents/com.forgemem.api.plist

# Option 3: Linux/Docker
systemctl start forgemem-api
```

### From Any Project Terminal

**Scenario: You're in `/path/to/my-project` with Claude Code open**

```python
# Claude Code terminal:
>>> import requests
>>> 
>>> # Before working: What have we learned about this problem?
>>> resp = requests.get("http://127.0.0.1:5555/search",
...   params={"q": "authentication", "project": "my-project"})
>>> 
>>> # Work on the feature...
>>> 
>>> # After fixing: Save what we learned
>>> requests.post("http://127.0.0.1:5555/traces", json={
...   "type": "success",
...   "content": "Implemented JWT refresh token rotation",
...   "project": "my-project",
...   "principle": "Always rotate tokens on each refresh",
...   "score": 9
... })
```

**Scenario: Shell workflow**

```bash
# In your project terminal with any AI agent
$ forgemem success "Fixed race condition with mutexes" "my-project"
✓ Saved trace #1234

$ brain_search "race condition patterns"
Found 3 principles:
- Always use RwLock for read-heavy concurrent access
- Implement circuit breaker for external service calls

$ brain_stats
Traces: 250 | Principles: 45
```

---

## How the Knowledge Flows

```
Day 1: Claude Code works on authentication
  → Saves 5 learnings to Forgemem
  → Database now has auth principles

Day 2: Copilot works on the same auth bug
  → Searches Forgemem: "authentication errors"
  → Finds Claude's 5 principles immediately
  → Applies solutions in minutes (not hours)

Day 3: Gemini works on API rate limiting
  → Uses custom Forgemem tool
  → Finds cross-cutting patterns about resilience
  → Builds on accumulated knowledge

Result: Each agent benefits from ALL previous work!
```

---

## Key Architecture Points

### Why HTTP?

1. **Universal** — Any language, any agent
2. **Localhost-safe** — No security needed locally
3. **Standard** — curl, Python, JavaScript all work
4. **Extensible** — Add webhooks, event polling, webhooks
5. **Future-proof** — Easy to migrate to REST API server

### Why Connection Pool?

1. **Prevents race conditions** — SQLite + concurrent access
2. **Bounded resource usage** — Max 5 connections (configurable)
3. **Efficient** — Reuse connections instead of creating new ones
4. **Safe** — Thread-safe Queue-based design

### Why Async Webhooks?

1. **Non-blocking** — Saving a trace never waits for webhooks
2. **Reliable** — Webhook queue + retry logic
3. **Flexible** — Send learnings to Slack, email, etc
4. **Scalable** — Can handle many webhooks per trace

---

## Common Workflows

### Save a Learning (After Bug Fix)

```bash
curl -X POST http://127.0.0.1:5555/traces \
  -H "Content-Type: application/json" \
  -d '{
    "type": "success",
    "content": "Fixed issue by using useCallback to memoize expensive computations",
    "project": "web-ui",
    "principle": "Always memoize render functions in React",
    "score": 8,
    "tags": "react,performance"
  }'
```

### Search Before Starting Work

```bash
# What do we know about this problem?
curl "http://127.0.0.1:5555/search?q=database+connection+pooling&k=10"
```

### Get Project Statistics

```bash
# How much have we learned?
curl http://127.0.0.1:5555/stats | jq '{
  trace_count,
  principle_count,
  by_type
}'
```

### Register a Webhook (Send Important Learnings to Slack)

```bash
curl -X POST http://127.0.0.1:5555/webhooks/register \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://hooks.slack.com/services/YOUR/WEBHOOK",
    "api_key": "sk_test",
    "min_impact_score": 8
  }'
```

---

## Troubleshooting

**"Connection refused"**
```bash
curl http://127.0.0.1:5555/health
# If fails: python3 ~/Developer/Forgemem/forgemem_api.py
```

**Agent can't find localhost:5555**
```bash
# Make sure API is running in a different terminal/session
launchctl start com.forgemem.api  # On macOS
```

**"Database is locked"**
```bash
# Increase pool size in forgemem_api.py: POOL_SIZE = 10
```

**Webhook not firing**
```bash
# Check webhook_queue table for errors
sqlite3 ~/Developer/Forgemem/forgemem_memory.db
SELECT * FROM webhook_queue WHERE status='failed';
```

---

## Summary

```
┌─────────────────────────────────────────────────┐
│  AI Agent (Claude/Copilot/Gemini)               │
│  In project terminal/session                    │
└────────────────┬────────────────────────────────┘
                 │
                 │ HTTP requests to
                 │ localhost:5555
                 ▼
┌─────────────────────────────────────────────────┐
│  Forgemem HTTP API                              │
│  (running as background service)                │
└────────────────┬────────────────────────────────┘
                 │
                 │ SQLite operations
                 ▼
┌─────────────────────────────────────────────────┐
│  forgemem_memory.db                             │
│  Shared knowledge base for all agents           │
└─────────────────────────────────────────────────┘
```

**Result:** Any AI agent in any project can:
1. Search for past learnings (before working)
2. Save new learnings (after discovering)
3. Build on accumulated knowledge (benefit from everyone)

It's like having a persistent memory that all your AI assistants can read and write!

