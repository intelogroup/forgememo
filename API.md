# Forgemem HTTP API

Extends Forgemem from a CLI-only tool to a full HTTP API server. This allows any AI agent (not just Claude Code) to query and save learnings, with webhook support for downstream integrations.

## Quick Start

### 1. Install Dependencies

```bash
cd ~/Developer/Forgemem
pip install flask requests anthropic fastmcp
```

### 2. Run Locally (Development)

```bash
python3 forgemem_api.py
```

Server runs on `http://127.0.0.1:5555`

Health check:
```bash
curl http://127.0.0.1:5555/health
```

### 3. Install as System Service (macOS)

```bash
launchctl load ~/Library/LaunchAgents/com.forgemem.api.plist
launchctl start com.forgemem.api

# Verify it's running
curl http://127.0.0.1:5555/health

# View logs
tail -f ~/Developer/Forgemem/forgemem_daemon.log
tail -f ~/Developer/Forgemem/api_error.log
```

To stop:
```bash
launchctl stop com.forgemem.api
launchctl unload ~/Library/LaunchAgents/com.forgemem.api.plist
```

---

## API Endpoints

### Health Check
```
GET /health
```

Returns server status and timestamp.

**Example:**
```bash
curl http://127.0.0.1:5555/health
```

**Response:**
```json
{
  "status": "healthy",
  "timestamp": "2026-03-27T14:30:00.123456"
}
```

---

### Search
```
GET /search?q=<query>&k=5&project=<project>&type=<type>
```

Full-text search across traces and principles.

**Query Parameters:**
- `q` (required): search query (max 500 chars)
- `k` (optional): max results (default 5, max 100)
- `project` (optional): filter by project tag
- `type` (optional): filter by type (success, failure, plan, note)

**Example:**
```bash
curl "http://127.0.0.1:5555/search?q=database+optimization&k=10&project=ugent-app"
```

**Response:**
```json
{
  "query": "database optimization",
  "results": {
    "traces": [
      {
        "id": 123,
        "ts": "2026-03-27T14:30:00",
        "project_tag": "ugent-app",
        "type": "success",
        "content": "Optimized SQL queries..."
      }
    ],
    "principles": [
      {
        "id": 45,
        "ts": "2026-03-27T14:00:00",
        "project_tag": "ugent-app",
        "type": "success",
        "principle": "Always use indexes on frequently queried columns",
        "impact_score": 8,
        "tags": "database,performance"
      }
    ]
  },
  "count": {
    "traces": 1,
    "principles": 1
  }
}
```

---

### Save Trace
```
POST /traces
Content-Type: application/json
```

Save a new learning/trace to the memory store.

**Request Body:**
```json
{
  "type": "success",
  "content": "Successfully optimized N+1 queries by using joins",
  "project": "ugent-app",
  "session": "daily-scan-2026-03-27",
  "principle": "Always profile before optimizing",
  "score": 8,
  "tags": "database,performance"
}
```

**Fields:**
- `type` (required): `success`, `failure`, `plan`, or `note`
- `content` (required): learning text (max 50KB)
- `project` (required): project tag (alphanumeric + hyphen)
- `session` (optional): session ID for grouping
- `principle` (optional): extracted principle
- `score` (optional): impact score 0-10 (default 5)
- `tags` (optional): comma-separated tags

**Example:**
```bash
curl -X POST http://127.0.0.1:5555/traces \
  -H "Content-Type: application/json" \
  -d '{
    "type": "success",
    "content": "Fixed memory leak by removing circular references",
    "project": "ugent-app",
    "principle": "Use weak references for parent-child relationships",
    "score": 9
  }'
```

**Response:**
```json
{
  "trace_id": 456,
  "principle_id": 78,
  "message": "Saved trace #456 + principle #78",
  "status": 201
}
```

---

### List Principles
```
GET /principles?project=<project>&type=<type>&limit=10
```

List all principles, sorted by impact score.

**Query Parameters:**
- `project` (optional): filter by project tag
- `type` (optional): filter by trace type
- `limit` (optional): max results (default 10, max 100)

**Example:**
```bash
curl "http://127.0.0.1:5555/principles?project=ugent-app&limit=20"
```

**Response:**
```json
{
  "principles": [
    {
      "id": 45,
      "ts": "2026-03-27T14:00:00",
      "project_tag": "ugent-app",
      "type": "success",
      "principle": "Always use indexes on frequently queried columns",
      "impact_score": 9,
      "tags": "database,performance"
    }
  ],
  "count": 1
}
```

---

### Get Statistics
```
GET /stats
```

Get database overview: trace count, principle count, breakdown by type/project, top principles.

**Example:**
```bash
curl http://127.0.0.1:5555/stats
```

**Response:**
```json
{
  "trace_count": 1234,
  "principle_count": 89,
  "by_type": {
    "success": 800,
    "failure": 300,
    "plan": 100,
    "note": 34
  },
  "by_project": {
    "ugent-app": 450,
    "personal-projects": 300,
    "research": 200
  },
  "top_principles": [
    {
      "id": 1,
      "principle": "Always use connection pooling",
      "impact_score": 10
    }
  ]
}
```

---

### Event Polling (Real-Time)
```
GET /events?since=<iso_timestamp>
```

Poll for traces created after a given timestamp. Useful for real-time syncing.

**Query Parameters:**
- `since` (required): ISO 8601 timestamp (e.g., `2026-03-27T14:30:00Z`)

**Example:**
```bash
curl "http://127.0.0.1:5555/events?since=2026-03-27T14:00:00Z"
```

**Response:**
```json
{
  "events": [
    {
      "id": 1,
      "type": "trace_saved",
      "trace": {
        "id": 456,
        "ts": "2026-03-27T14:30:00",
        "project_tag": "ugent-app",
        "type": "success",
        "content": "Fixed memory leak..."
      },
      "principle": {
        "id": 78,
        "principle": "Use weak references...",
        "impact_score": 9,
        "tags": "memory"
      },
      "timestamp": "2026-03-27T14:30:00"
    }
  ],
  "count": 1,
  "next_poll_after": "2026-03-27T14:30:02Z"
}
```

**Client Example (Python):**
```python
import requests
from datetime import datetime
import time

last_ts = datetime.utcnow().isoformat() + "Z"
while True:
    resp = requests.get(
        f"http://127.0.0.1:5555/events?since={last_ts}",
        timeout=10
    )
    for event in resp.json()['events']:
        print(f"New trace: {event['trace']['content'][:50]}...")
    
    last_ts = resp.json()['next_poll_after']
    time.sleep(2)  # Poll every 2 seconds
```

---

### Register Webhook
```
POST /webhooks/register
Content-Type: application/json
```

Register a webhook to be triggered when matching traces are saved.

**Request Body:**
```json
{
  "url": "https://example.com/forgemem-webhook",
  "api_key": "secret_api_key_12345",
  "project_filter": "ugent-app,personal-projects",
  "type_filter": "success,failure",
  "min_impact_score": 6
}
```

**Fields:**
- `url` (required): webhook URL (http/https)
- `api_key` (required): API key for authentication (sent as Bearer token)
- `project_filter` (optional): comma-separated project tags (null = all projects)
- `type_filter` (optional): comma-separated trace types (null = all types)
- `min_impact_score` (optional): minimum impact score to trigger (default 0)

**Example:**
```bash
curl -X POST http://127.0.0.1:5555/webhooks/register \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://example.com/webhook",
    "api_key": "sk_live_abc123def456",
    "project_filter": "ugent-app",
    "min_impact_score": 7
  }'
```

**Response:**
```json
{
  "webhook_id": 12,
  "url": "https://example.com/webhook",
  "created_at": "2026-03-27T14:30:00Z",
  "status": 201
}
```

**Webhook Payload (sent to your URL):**
```json
{
  "event": "trace_saved",
  "trace_id": 456,
  "type": "success",
  "project": "ugent-app",
  "content": "Fixed memory leak by removing circular references",
  "timestamp": "2026-03-27T14:30:00Z"
}
```

**Webhook Retry Logic:**
- Retry 1: 5 minutes
- Retry 2: 30 minutes
- Retry 3: 2 hours
- Retry 4: 12 hours
- Retry 5: 24 hours
- After 5 failures: marked as failed

---

## Error Responses

All errors return consistent JSON format:

```json
{
  "error": "invalid_type",
  "message": "type must be one of: success, failure, plan, note",
  "status": 400
}
```

**Common Errors:**
- `400 Bad Request`: Invalid input (query, body, type, etc.)
- `404 Not Found`: Endpoint not found
- `409 Conflict`: Resource already exists (e.g., webhook URL)
- `500 Internal Error`: Server error

---

## Integration with Existing Tools

### Daily Scan (`daily_scan.py`)

Change from subprocess to HTTP calls:

**Before:**
```python
result = subprocess.run([PYTHON, FORGEMEM_CLI, "save", ...], capture_output=True)
```

**After:**
```python
import requests

resp = requests.post(
    "http://127.0.0.1:5555/traces",
    json={
        "type": learning.get("type"),
        "content": learning.get("content"),
        "principle": learning.get("principle"),
        "project": project,
        "session": "daily-scan-2026-03-27"
    },
    timeout=10
)
if resp.status_code == 201:
    trace_id = resp.json()['trace_id']
    print(f"Saved trace #{trace_id}")
```

### MCP Server (`mcp_server.py`)

No changes required — continues to work with SQLite directly. The API adds an alternative access method for non-Claude agents.

---

## Performance Notes

**Connection Pool:**
- 5 concurrent SQLite connections by default
- Thread-safe with Queue-based acquisition/release
- For >5 concurrent agents, consider upgrading to FastAPI + async

**Webhook Delivery:**
- Asynchronous (non-blocking) via background thread
- Exponential backoff retry (5 attempts over 24 hours)
- Failed webhooks stored in `webhook_queue` table for manual replay

**FTS5 Indexing:**
- Traces and principles indexed on save
- Search queries optimized with LIMIT clauses
- Consider `VACUUM` after large deletions

---

## Logging

**API Server:**
- `~/Developer/Forgemem/api.log` (stdout)
- `~/Developer/Forgemem/api_error.log` (stderr)
- `~/Developer/Forgemem/forgemem_daemon.log` (daemon process logs)

View logs in real-time:
```bash
tail -f ~/Developer/Forgemem/forgemem_daemon.log
```

---

## Testing the API

### Quick Test Script

```bash
#!/bin/bash

BASE="http://127.0.0.1:5555"

echo "1. Health check..."
curl -s "$BASE/health" | jq .

echo -e "\n2. Save a trace..."
curl -s -X POST "$BASE/traces" \
  -H "Content-Type: application/json" \
  -d '{
    "type": "success",
    "content": "Test trace from API",
    "project": "test-project",
    "principle": "Always test before deploying"
  }' | jq .

echo -e "\n3. Get statistics..."
curl -s "$BASE/stats" | jq .

echo -e "\n4. Search..."
curl -s "$BASE/search?q=test&project=test-project" | jq .

echo -e "\n5. List principles..."
curl -s "$BASE/principles?limit=10" | jq .

echo -e "\nDone!"
```

---

## Scaling Beyond Local

When you have >5 concurrent agents:

1. **Upgrade to FastAPI** — async/await for better concurrency
2. **Add Redis** — for webhook queue (instead of in-DB)
3. **Split webhook worker** — separate process from Flask
4. **Add reverse proxy** — nginx with TLS, rate limiting, IP whitelisting
5. **Enable audit logging** — all API calls logged to separate table

For now, the SQLite + Flask approach handles 1-5 concurrent agents efficiently.

---

## Deployment Checklist

- [x] Phase 1: Flask server + connection pool
- [x] Phase 1: 6 REST endpoints with validation
- [x] Phase 2: Webhook registration & dispatch (async background thread)
- [x] Phase 2: Webhook retry worker (exponential backoff)
- [x] Phase 3: Real-time event polling endpoint
- [ ] Phase 3: Daemon daemonization + LaunchAgent setup
- [ ] Phase 4: API key management (optional)
- [ ] Migrate daily_scan.py to HTTP calls
- [ ] Load test (10 concurrent requests)

---

## Next Steps

**Phase 2 (In Progress):**
- Webhook triggers on matching traces
- Async dispatch with retry logic

**Phase 3:**
- Real-time polling endpoint (`/events`)
- Daemon process with graceful shutdown

**Phase 4 (Optional):**
- API key management (`/auth/register`, `/auth/revoke`)
- Per-key project scoping
- Rate limiting

**Future Enhancements:**
- WebSocket for real-time push (instead of polling)
- GraphQL endpoint for complex queries
- Admin dashboard (webhook management, API key rotation)
- Batch operations (`/traces/batch`)
- Redis caching for popular queries
