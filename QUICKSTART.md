# Forgemem API Quick Reference

## Start the Server

```bash
cd ~/Developer/Forgemem
python3 forgemem_api.py
```

Server runs on `http://127.0.0.1:5555`

## Quick API Examples

### Health Check
```bash
curl http://127.0.0.1:5555/health
```

### Save a Trace
```bash
curl -X POST http://127.0.0.1:5555/traces \
  -H "Content-Type: application/json" \
  -d '{
    "type": "success",
    "content": "Fixed N+1 query problem with eager loading",
    "project": "my-app",
    "principle": "Always profile database queries",
    "score": 8
  }'
```

### Search
```bash
curl "http://127.0.0.1:5555/search?q=database&k=10"
```

### List Principles
```bash
curl "http://127.0.0.1:5555/principles?limit=20"
```

### Get Stats
```bash
curl http://127.0.0.1:5555/stats
```

### Register a Webhook
```bash
curl -X POST http://127.0.0.1:5555/webhooks/register \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://example.com/webhook",
    "api_key": "sk_live_abc123"
  }'
```

### Poll Events
```bash
curl "http://127.0.0.1:5555/events?since=2026-03-27T14:00:00Z"
```

## Test Suite

```bash
# Run all tests
python3 test_api.py
```

## Install as macOS Service

```bash
# Load the service
launchctl load ~/Library/LaunchAgents/com.forgemem.api.plist

# Start the service
launchctl start com.forgemem.api

# Check status
curl http://127.0.0.1:5555/health

# View logs
tail -f ~/Developer/Forgemem/forgemem_daemon.log

# Stop the service
launchctl stop com.forgemem.api

# Unload the service
launchctl unload ~/Library/LaunchAgents/com.forgemem.api.plist
```

## Common Errors

**Connection refused:**
- Server not running: `python3 forgemem_api.py`

**Module not found (flask, requests):**
- Install dependencies: `pip install flask requests`

**Database locked:**
- Check connection pool size (default 5)
- Close other connections

**Webhook not firing:**
- Check webhook_queue table: invalid payload or network error
- Check webhook failure_count for retry attempts

## API Endpoint Summary

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/health` | Health check |
| GET | `/stats` | Database stats |
| GET | `/search?q=...` | Full-text search |
| POST | `/traces` | Save trace/learning |
| GET | `/principles` | List principles |
| GET | `/events?since=...` | Poll events |
| POST | `/webhooks/register` | Register webhook |

## Performance Tips

1. **Batch saves** — Multiple traces at once instead of individual POSTs
2. **Limit search results** — Use `?k=10` instead of default 5
3. **Filter by project** — `?project=my-app` reduces search space
4. **Monitor webhook_queue** — Check for failed deliveries

## Documentation

- **API.md** — Complete API reference
- **IMPLEMENTATION.md** — Architecture & design decisions
- **test_api.py** — Usage examples

## Environment Variables

```bash
# Set database location (optional)
export FORGEMEM_DB=/path/to/forgemem_memory.db
```

## Python Client Example

```python
import requests
from datetime import datetime, timedelta

BASE = "http://127.0.0.1:5555"

# Save a trace
resp = requests.post(f"{BASE}/traces", json={
    "type": "success",
    "content": "Optimized API response time",
    "project": "my-app",
    "principle": "Cache frequently accessed data"
})
print(f"Saved trace #{resp.json()['trace_id']}")

# Search
resp = requests.get(f"{BASE}/search", params={"q": "cache", "k": 10})
for trace in resp.json()['results']['traces']:
    print(f"- {trace['content'][:60]}")

# Poll events
since = (datetime.utcnow() - timedelta(hours=1)).isoformat() + "Z"
resp = requests.get(f"{BASE}/events", params={"since": since})
for event in resp.json()['events']:
    print(f"New: {event['trace']['content'][:60]}")
```

## Webhook Example

Your webhook endpoint will receive:

```json
{
  "event": "trace_saved",
  "trace_id": 123,
  "type": "success",
  "project": "my-app",
  "content": "Fixed memory leak",
  "timestamp": "2026-03-27T14:30:00Z"
}
```

Respond with `200 OK` to acknowledge. Failed responses trigger retries.

