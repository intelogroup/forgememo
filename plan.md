# Forgemem HTTP API, Webhooks & Real-Time Sync Plan

## Overview

Extend Forgemem from Claude Code MCP-only to a full HTTP API service that any AI agent can query and save learnings to. Add webhook triggers for downstream services, real-time event polling, and a daemon process for background task management.

---

## Phase 1: HTTP API Server (Week 1)

### 1.1 Create `forgemem_api.py` - Flask HTTP Wrapper

**File**: `forgemem_api.py` (~300 lines)

**Dependencies**: Flask, requests (add to requirements)

**Features**:
- SQLite connection pooling (Queue-based, 5 worker connections)
- 6 REST endpoints
- Request validation & JSON error responses
- CORS headers for cross-origin agent access

**Core Endpoints**:

```
GET  /search?q=<query>&k=5&project=<proj>&type=<type>
     → FTS5 search, returns {principles, traces}

POST /traces
     → Body: {type, content, project, session, principle?, score?, tags?}
     → Returns: {trace_id, principle_id?, message}

GET  /principles?project=<proj>&type=<type>&limit=10
     → List all principles, sorted by impact_score DESC

GET  /stats
     → Returns: {trace_count, principle_count, by_type, top_projects}

GET  /events?since=<timestamp>
     → Polling-based real-time: returns traces created after timestamp

POST /webhooks/register
     → Body: {url, api_key, project_filter?, type_filter?, min_impact_score?}
     → Returns: {webhook_id, created_at}
```

**Connection Pool Pattern**:
```python
from queue import Queue
import threading

class DBPool:
    def __init__(self, db_path, pool_size=5):
        self.queue = Queue(maxsize=pool_size)
        for _ in range(pool_size):
            conn = sqlite3.connect(db_path)
            self.queue.put(conn)
    
    def get_conn(self):
        return self.queue.get()
    
    def return_conn(self, conn):
        self.queue.put(conn)
```

---

### 1.2 Request Validation

**Add input sanitization**:
- Query string validation (max 500 chars)
- Trace content max 50KB
- Project tag whitelist (alphanumeric + hyphen)
- Type enum validation

**Error responses** (consistent JSON):
```json
{
  "error": "invalid_type",
  "message": "type must be one of: success, failure, plan, note",
  "status": 400
}
```

---

## Phase 2: Webhook Triggers (Week 2)

### 2.1 Extend Database Schema

**New tables**:

```sql
CREATE TABLE webhooks (
  id              INTEGER PRIMARY KEY,
  url             TEXT NOT NULL UNIQUE,
  api_key         TEXT NOT NULL,
  project_filter  TEXT,          -- Comma-separated; NULL = all projects
  type_filter     TEXT,          -- Comma-separated; NULL = all types
  min_impact_score INTEGER DEFAULT 0,
  active          INTEGER DEFAULT 1,
  created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
  last_fired      DATETIME,
  failure_count   INTEGER DEFAULT 0
);

CREATE TABLE webhook_queue (
  id              INTEGER PRIMARY KEY,
  trace_id        INTEGER NOT NULL REFERENCES traces(id),
  webhook_id      INTEGER NOT NULL REFERENCES webhooks(id),
  status          TEXT CHECK(status IN ('pending', 'delivered', 'failed')),
  retry_count     INTEGER DEFAULT 0,
  next_retry_at   DATETIME,
  created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
  error_message   TEXT,
  payload         TEXT  -- Store request body for manual replay
);
```

**Schema migration**:
- Add `PRAGMA user_version` to forgemem.py init to track schema version
- Safe to run multiple times (idempotent)

---

### 2.2 Webhook Dispatch System

**In `forgemem_api.py`**:

```python
def trigger_webhooks_async(trace_id, project, trace_type, impact_score):
    """Fire webhook tasks in background thread (non-blocking)."""
    threading.Thread(
        target=dispatch_webhooks,
        args=(trace_id, project, trace_type, impact_score),
        daemon=True
    ).start()

def dispatch_webhooks(trace_id, project, trace_type, impact_score):
    """Enqueue webhooks matching filters, attempt delivery with retry logic."""
    conn = pool.get_conn()
    webhooks = conn.execute(
        "SELECT id, url, project_filter, type_filter, min_impact_score FROM webhooks WHERE active=1"
    ).fetchall()
    conn.close()
    
    for webhook in webhooks:
        if matches_filter(webhook, project, trace_type, impact_score):
            enqueue_webhook_delivery(webhook['id'], trace_id)
```

**Retry logic** (exponential backoff):
- Retry 1: 5 minutes
- Retry 2: 30 minutes
- Retry 3: 2 hours
- Retry 4: 12 hours
- Retry 5: 24 hours
- After 5 failed: mark as 'failed', send alert email (future)

**Webhook payload**:
```json
{
  "event": "trace_saved",
  "trace_id": 123,
  "type": "success",
  "project": "ugent-app",
  "content": "...",
  "principle": "...",
  "impact_score": 8,
  "timestamp": "2026-03-27T14:30:00Z"
}
```

---

### 2.3 Background Worker Thread

**In `forgemem_api.py`**:

```python
def webhook_retry_worker():
    """Runs continuously; checks for pending webhooks and retries."""
    while True:
        time.sleep(30)  # Check every 30 seconds
        conn = pool.get_conn()
        pending = conn.execute(
            "SELECT id, webhook_id, trace_id, retry_count FROM webhook_queue "
            "WHERE status='pending' AND next_retry_at <= datetime('now')"
        ).fetchall()
        
        for queue_entry in pending:
            attempt_webhook_delivery(queue_entry)
        
        conn.close()

# Start on Flask app startup
threading.Thread(target=webhook_retry_worker, daemon=True).start()
```

---

## Phase 3: Real-Time Sync (Week 3)

### 3.1 Event Polling Endpoint

**Endpoint**: `GET /events?since=<iso_timestamp>`

**Returns**:
```json
{
  "events": [
    {
      "id": 123,
      "type": "trace_saved",
      "trace": {id, type, project, content, ts},
      "principle": {id, principle, impact_score, tags},
      "timestamp": "2026-03-27T14:30:00Z"
    }
  ],
  "next_poll_after": "2026-03-27T14:30:02Z"
}
```

**Client-side polling**:
```python
last_ts = datetime.utcnow()
while True:
    resp = requests.get(
        f"http://localhost:5555/events?since={last_ts.isoformat()}",
        headers={"Authorization": f"Bearer {api_key}"}
    )
    for event in resp.json()['events']:
        process_event(event)
    
    last_ts = resp.json()['next_poll_after']
    time.sleep(2)  # Poll every 2 seconds
```

### 3.2 Daemon Process - `forgemem_daemon.py`

**File**: `forgemem_daemon.py` (~100 lines)

**Responsibilities**:
- Spawn Flask app (on port 5555)
- Run webhook retry worker
- Graceful shutdown on SIGTERM
- Health check endpoint (`GET /health`)

```python
#!/usr/bin/env python3
import os
import signal
import sys
from forgemem_api import create_app

def signal_handler(sig, frame):
    print("Shutting down gracefully...")
    sys.exit(0)

if __name__ == "__main__":
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    app = create_app()
    app.run(host="127.0.0.1", port=5555, threaded=True)
```

---

### 3.3 LaunchAgent Registration (macOS)

**File**: `~/Library/LaunchAgents/com.forgemem.api.plist`

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.forgemem.api</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>/Users/kalinovdameus/Developer/Forgemem/forgemem_daemon.py</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
        <string>/Users/kalinovdameus/Developer/Forgemem/api.log</string>
    <key>StandardErrorPath</key>
        <string>/Users/kalinovdameus/Developer/Forgemem/api_error.log</string>
</dict>
</plist>
```

**Install**:
```bash
launchctl load ~/Library/LaunchAgents/com.forgemem.api.plist
launchctl start com.forgemem.api
```

---

## Phase 4: API Key Management (Week 4 - Optional)

### 4.1 Add API Key Table

```sql
CREATE TABLE api_keys (
  id              INTEGER PRIMARY KEY,
  key             TEXT NOT NULL UNIQUE,  -- Generated via secrets.token_urlsafe(32)
  project_scope   TEXT,                  -- Restrict to specific projects (null = all)
  created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
  last_used_at    DATETIME,
  active          INTEGER DEFAULT 1
);
```

### 4.2 Endpoints

```
POST /auth/register
     → Requires: Bearer token from ~/.forgemem_key (pre-installed)
     → Returns: {api_key, project_scope, created_at}

POST /auth/revoke
     → Revoke an API key
```

---

## Integration with Existing Systems

### Daily Scan (`daily_scan.py`)

**Current**: Uses subprocess to call `forgemem.py save`

**Future**: Call Flask API directly via requests library
```python
# Old pattern:
result = subprocess.run([PYTHON, FORGEMEM_CLI, "save", ...], capture_output=True)

# New pattern:
import requests
resp = requests.post(
    "http://localhost:5555/traces",
    json={
        "type": learning.get("type"),
        "content": learning.get("content"),
        "principle": learning.get("principle"),
        "project": project,
        "session": "daily-scan-2026-03-27"
    },
    headers={"Authorization": f"Bearer {api_key}"}
)
```

### MCP Server (`mcp_server.py`)

**No changes required** — continues to work as-is. API adds alternative access method for non-Claude agents.

---

## Deployment Checklist

- [ ] Phase 1: Flask server + connection pool tested locally
- [ ] Phase 2: Webhook registration + dispatch tested with curl
- [ ] Phase 3: Polling endpoint tested; daemon daemonizes & restarts on crash
- [ ] Phase 4: API key management working; daily_scan migrated to HTTP calls
- [ ] Health check passing (`curl http://localhost:5555/health`)
- [ ] LaunchAgent installed & auto-starts on reboot
- [ ] Logs rotating (logrotate or similar)

---

## Scaling Notes

**Current assumptions**: 1-5 concurrent agents, local machine only

**If scaling beyond 5 agents**:
1. Migrate Flask → FastAPI (async/await)
2. Add Redis for webhook queue (instead of in-DB queue)
3. Extract webhook worker to separate process
4. Add reverse proxy (nginx) + TLS

**If exposing to remote agents**:
1. TLS termination (cert via letsencrypt)
2. Rate limiting per API key
3. IP whitelisting
4. Audit logging (all API calls)

---

## Testing Strategy

### Unit Tests
- Connection pool: acquire/release
- Request validation: bad inputs rejected
- Webhook filtering: correct webhooks selected

### Integration Tests
- Save trace → FTS5 indexed → retrievable
- Webhook fires on matching trace
- Webhook retries on failure
- Event polling returns correct timestamps

### Load Test
- 10 concurrent /search requests
- Ensure no connection pool deadlock
- Measure latency (target: <100ms p99)

---

## Future Enhancements

1. **WebSocket upgrade** — Real-time push (instead of polling) for <50ms latency
2. **GraphQL endpoint** — For complex multi-table queries
3. **Admin dashboard** — Webhook management UI, API key rotation
4. **Audit trail** — All API calls logged to separate table
5. **Batch operations** — `/traces/batch` for bulk inserts
6. **Caching layer** — Redis caching of popular queries
