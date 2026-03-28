# Accessing Forgemem API from AI Agent Terminals

This guide shows how Claude, Copilot, Gemini, or any AI agent can access the Forgemem memory store while working in their project terminal/session.

## Architecture Overview

```
┌─────────────────────────────────────────┐
│  AI Agent Terminal/Session              │
│  (Claude Code, Copilot, Gemini, etc)   │
│                                         │
│  curl / Python / JavaScript requests   │
└──────────────┬──────────────────────────┘
               │ HTTP requests to
               │ localhost:5555
               ▼
┌─────────────────────────────────────────┐
│  Forgemem HTTP API Server               │
│  (forgemem_api.py on port 5555)         │
│                                         │
│  - Connection pool (5 concurrent)       │
│  - 7 REST endpoints                     │
│  - Webhook dispatch                     │
│  - Event polling                        │
└──────────────┬──────────────────────────┘
               │ SQLite 
               │ queries
               ▼
┌─────────────────────────────────────────┐
│  SQLite Database                        │
│  (forgemem_memory.db)                   │
│                                         │
│  - traces (213 entries)                 │
│  - principles (213 entries)             │
│  - webhooks                             │
│  - webhook_queue                        │
└─────────────────────────────────────────┘
```

---

## Prerequisites

The Forgemem API server must be running in the background:

```bash
# Terminal 1 (background/systemd/launchd)
python3 ~/Developer/Forgemem/forgemem_api.py

# OR auto-start on macOS
launchctl load ~/Library/LaunchAgents/com.forgemem.api.plist
```

The API listens on `http://127.0.0.1:5555` (localhost only - safe for local development).

---

## Method 1: curl (CLI - Works Everywhere)

### Save a trace from terminal
```bash
curl -X POST http://127.0.0.1:5555/traces \
  -H "Content-Type: application/json" \
  -d '{
    "type": "success",
    "content": "Fixed bug in async handler by using proper error boundaries",
    "project": "my-project",
    "principle": "Always wrap async operations in try-catch blocks",
    "score": 8,
    "tags": "async,error-handling"
  }'
```

### Search the knowledge base
```bash
curl "http://127.0.0.1:5555/search?q=async+error+handling&k=10&project=my-project"
```

### Get statistics
```bash
curl http://127.0.0.1:5555/stats | jq .
```

### Register a webhook (e.g., to Slack, email, etc.)
```bash
curl -X POST http://127.0.0.1:5555/webhooks/register \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://hooks.slack.com/services/YOUR/WEBHOOK/URL",
    "api_key": "sk_test_abc123",
    "project_filter": "my-project",
    "min_impact_score": 7
  }'
```

**Used by**: Shell scripts, Makefile, CI/CD pipelines, manual debugging

---

## Method 2: Python Client (Claude Code, Interactive Debugging)

### In Claude Code or Python terminal:

```python
import requests
import json
from datetime import datetime, timedelta

BASE = "http://127.0.0.1:5555"

# Save a learning
resp = requests.post(f"{BASE}/traces", json={
    "type": "success",
    "content": "Refactored authentication module - reduced complexity by 40%",
    "project": "auth-service",
    "principle": "Extract cross-cutting concerns early",
    "score": 9,
    "tags": "refactoring,architecture"
})
trace_id = resp.json()['trace_id']
print(f"✓ Saved trace #{trace_id}")

# Search for related learnings
resp = requests.get(f"{BASE}/search", params={
    "q": "authentication module refactoring",
    "k": 10,
    "project": "auth-service"
})
for trace in resp.json()['results']['traces']:
    print(f"- {trace['content'][:60]}...")

# Get principles for this project
resp = requests.get(f"{BASE}/principles", params={
    "project": "auth-service",
    "limit": 5
})
for p in resp.json()['principles']:
    print(f"[{p['impact_score']}/10] {p['principle']}")

# Poll for recent events (real-time sync)
since = (datetime.utcnow() - timedelta(hours=1)).isoformat() + "Z"
resp = requests.get(f"{BASE}/events", params={"since": since})
for event in resp.json()['events']:
    print(f"New: {event['trace']['content'][:60]}")
```

**Used by**: Claude Code, Jupyter notebooks, interactive Python sessions, scripts

---

## Method 3: JavaScript/Node.js (Copilot, Web Terminals)

### In Node.js terminal or JavaScript context:

```javascript
const BASE = "http://127.0.0.1:5555";

// Save a trace
async function saveTrace() {
  const resp = await fetch(`${BASE}/traces`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      type: "success",
      content: "Optimized React component rendering with useMemo - 60% faster",
      project: "web-ui",
      principle: "Memoize expensive computations in React",
      score: 8
    })
  });
  const data = await resp.json();
  console.log(`✓ Saved trace #${data.trace_id}`);
  return data;
}

// Search knowledge base
async function search(query) {
  const params = new URLSearchParams({
    q: query,
    k: 10,
    project: "web-ui"
  });
  const resp = await fetch(`${BASE}/search?${params}`);
  const data = await resp.json();
  
  console.log(`Found ${data.count.traces} traces:`);
  data.results.traces.forEach(t => {
    console.log(`- ${t.content.slice(0, 60)}...`);
  });
}

// Get project statistics
async function getStats() {
  const resp = await fetch(`${BASE}/stats`);
  const stats = await resp.json();
  console.log(`Total traces: ${stats.trace_count}`);
  console.log(`Total principles: ${stats.principle_count}`);
  console.log(`Top principles:`, stats.top_principles);
}

// Real-time event polling
async function pollEvents() {
  const since = new Date(Date.now() - 3600000).toISOString();
  const params = new URLSearchParams({ since });
  
  setInterval(async () => {
    const resp = await fetch(`${BASE}/events?${params}`);
    const data = await resp.json();
    
    data.events.forEach(e => {
      console.log(`[${new Date(e.timestamp).toLocaleTimeString()}] ${e.trace.type}: ${e.trace.content.slice(0, 50)}`);
    });
  }, 2000);
}

// Run
(async () => {
  await saveTrace();
  await search("performance optimization");
  await getStats();
})();
```

**Used by**: GitHub Copilot, Node.js scripts, Electron apps, browser consoles

---

## Method 4: MCP Integration (Claude Code Direct)

Claude Code already has MCP server integration. Forgemem's **existing MCP server** (`mcp_server.py`) provides direct SQLite access without needing the HTTP API:

```python
# In Claude Code - already available via MCP
# These tools are automatically registered:

# 1. search_memory(query, k, project, type)
#    → Returns matching principles and traces

# 2. save_trace(type, content, project, session, principle, score, tags)
#    → Saves to database directly

# 3. list_principles(project, type, limit)
#    → Lists top principles

# 4. get_stats(project)
#    → Returns statistics
```

**No code needed** - Claude Code can call these directly!

### However, the HTTP API enables:
- **Other AI agents** (Copilot, Gemini, etc) to access without MCP
- **Any language/platform** (JavaScript, Go, Rust, etc)
- **Webhooks** for downstream integration
- **Event polling** for real-time sync
- **Distributed agents** on different machines

---

## Method 5: Shell Wrapper Function (Easiest for CLI)

Add this to your `~/.zshrc` or `~/.bashrc`:

```bash
# Forgemem CLI wrapper - makes saving learnings trivial
forgemem() {
  local type="${1:-note}"
  local content="${2:?Content required}"
  local project="${3:-.}"
  local principle="${4}"
  local score="${5:-5}"
  
  curl -s -X POST http://127.0.0.1:5555/traces \
    -H "Content-Type: application/json" \
    -d $(jq -n \
      --arg type "$type" \
      --arg content "$content" \
      --arg project "$project" \
      --arg principle "$principle" \
      --arg score "$score" \
      '{type: $type, content: $content, project: $project, principle: $principle, score: ($score | tonumber)}' \
    ) | jq -r '.trace_id' | xargs echo "✓ Saved trace #"
}

brain_search() {
  local query="${1:?Query required}"
  local limit="${2:-10}"
  
  curl -s "http://127.0.0.1:5555/search?q=$query&k=$limit" | jq '.results | {traces: (.traces | length), principles: (.principles | length)}'
}

brain_stats() {
  curl -s http://127.0.0.1:5555/stats | jq '{trace_count, principle_count, by_type}'
}
```

**Usage from terminal:**
```bash
forgemem success "Fixed N+1 query with joins" "my-project" "Always use database indexes"

brain_search "database optimization" 20

brain_stats
```

**Used by**: Shell scripts, terminal workflows, Makefiles, dev automation

---

## Method 6: Makefile Integration (Project-Level)

Add to your project `Makefile`:

```makefile
# Forgemem knowledge base integration
.PHONY: learn learn-fix learn-note search-brain stats-brain

learn:
	@read -p "Type (success/failure/plan/note) [note]: " TYPE; \
	read -p "Learning: " CONTENT; \
	read -p "Principle (optional): " PRINCIPLE; \
	curl -X POST http://127.0.0.1:5555/traces \
	  -H "Content-Type: application/json" \
	  -d "{\"type\":\"$${TYPE:-note}\",\"content\":\"$$CONTENT\",\"project\":\"$$(basename $$PWD)\",\"principle\":\"$$PRINCIPLE\"}" | jq .

learn-fix:
	@curl -X POST http://127.0.0.1:5555/traces \
	  -H "Content-Type: application/json" \
	  -d '{"type":"failure","content":"From last git commit","project":"'$$(basename $$PWD)'","tags":"debug"}' | jq .

search-brain:
	@read -p "Search: " Q; \
	curl "http://127.0.0.1:5555/search?q=$$Q&k=10&project=$$(basename $$PWD)" | jq '.results'

stats-brain:
	@curl http://127.0.0.1:5555/stats | jq '.by_project'
```

**Usage:**
```bash
make learn          # Interactively save a learning
make search-brain   # Search knowledge base
make stats-brain    # View statistics
```

---

## Method 7: IDE Integration (VS Code Extension)

Create a VS Code extension to add Forgemem commands:

```json
// .vscode/extensions/forgemem/package.json
{
  "name": "forgemem-helper",
  "displayName": "Forgemem Memory Helper",
  "version": "1.0.0",
  "engines": { "vscode": "^1.50.0" },
  "contributes": {
    "commands": [
      {
        "command": "forgemem.saveLearning",
        "title": "Save Learning to Forgemem"
      },
      {
        "command": "forgemem.search",
        "title": "Search Forgemem Memory"
      }
    ]
  }
}
```

Then bind keyboard shortcuts:

```json
// .vscode/keybindings.json
[
  {
    "key": "ctrl+shift+l",
    "command": "forgemem.saveLearning"
  },
  {
    "key": "ctrl+shift+b",
    "command": "forgemem.search"
  }
]
```

**Usage**: Press `Ctrl+Shift+L` in VS Code to save a learning

---

## Method 8: Git Hooks Integration

Automatically capture learnings from commits:

```bash
# .git/hooks/post-commit
#!/bin/bash

COMMIT_MSG=$(git log -1 --format=%B)
COMMIT_HASH=$(git log -1 --format=%H)
PROJECT=$(basename $(git rev-parse --show-toplevel))

# Extract learning from commit message if it has [LEARN: ...] tag
if [[ $COMMIT_MSG =~ \[LEARN:([^\]]+)\] ]]; then
  LEARNING="${BASH_REMATCH[1]}"
  
  curl -X POST http://127.0.0.1:5555/traces \
    -H "Content-Type: application/json" \
    -d "{
      \"type\": \"success\",
      \"content\": \"$LEARNING\",
      \"project\": \"$PROJECT\",
      \"tags\": \"git-commit,$COMMIT_HASH\"
    }"
fi
```

**Usage:**
```bash
git commit -m "Fixed auth bug [LEARN: Always validate JWT signature before processing]"
# Automatically saves to Forgemem!
```

---

## Real-World Example: Claude Code Session

Here's how Claude Code would work with Forgemem in a typical development session:

```
┌─────────────────────────────────────────────────────┐
│  Claude Code Terminal (in project directory)        │
└─────────────────────────────────────────────────────┘

> python3
>>> import requests
>>> 
>>> # Claude is working on a bug fix
>>> # Before starting, search for related learnings:
>>> resp = requests.get("http://127.0.0.1:5555/search", 
...   params={"q": "authentication error", "project": "my-project"})
>>> for p in resp.json()['results']['principles']:
...   print(f"- {p['principle']}")
- Always validate JWT signature before processing
- Use secure cookie flags (httpOnly, sameSite, secure)
- Implement exponential backoff for OAuth retries

>>> # Now Claude fixes the bug...
>>> # After fixing, save the learning:
>>> requests.post("http://127.0.0.1:5555/traces", json={
...   "type": "success",
...   "content": "Fixed auth timeout by implementing circuit breaker pattern",
...   "project": "my-project",
...   "principle": "Use circuit breaker for external service calls",
...   "score": 9
... })
```

---

## How to Choose the Right Method

| Method | Agent | Use Case | Best For |
|--------|-------|----------|----------|
| **curl** | Any | CLI, scripts, CI/CD | Shell workflows, quick commands |
| **Python** | Claude | Interactive development | Data analysis, testing, debugging |
| **JavaScript** | Copilot | Node.js, browser | Web projects, automation |
| **MCP** | Claude | Direct SQLite | Claude Code native integration |
| **Shell function** | Any | CLI wrapper | Daily workflows, quick saves |
| **Makefile** | Any | Project automation | Build workflows, team commands |
| **IDE extension** | Any | VS Code | Developer convenience |
| **Git hooks** | Any | Auto-capture | Automatic learning capture |
| **Webhooks** | Any | Notifications | Slack, email, external services |

---

## Security Considerations

**Current Setup (Local Only):**
- ✓ HTTP on localhost (127.0.0.1)
- ✓ No authentication required (safe on local machine)
- ✓ Connection pool prevents race conditions
- ✓ SQL injection prevention via parameterized queries

**When Exposing Remotely (Future):**
- Add API key authentication
- Use HTTPS/TLS
- Implement rate limiting
- Add IP whitelisting
- Audit all requests

---

## Troubleshooting

**"Connection refused":**
```bash
# Check if API server is running
curl http://127.0.0.1:5555/health

# Start it if not
python3 ~/Developer/Forgemem/forgemem_api.py
```

**"Database is locked":**
```bash
# Increase connection pool size in forgemem_api.py
POOL_SIZE = 10  # instead of 5
```

**"Webhook not firing":**
```bash
# Check webhook queue for errors
sqlite3 ~/Developer/Forgemem/forgemem_memory.db
SELECT * FROM webhook_queue WHERE status = 'failed';
```

---

## Quick Start Commands

```bash
# Start the server
python3 ~/Developer/Forgemem/forgemem_api.py

# In another terminal, try:
curl http://127.0.0.1:5555/health
curl http://127.0.0.1:5555/stats | jq .

# Save a test trace
curl -X POST http://127.0.0.1:5555/traces \
  -H "Content-Type: application/json" \
  -d '{"type":"note","content":"Test","project":"test"}'

# Run the test suite
python3 ~/Developer/Forgemem/test_api.py
```

