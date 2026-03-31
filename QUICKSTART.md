# Forgememoo Quick Reference (Daemon + Socket)

## Start the Daemon

```bash
forgememo daemon
```

Default socket: `/tmp/forgememo.sock`  
Opt-in HTTP: `FORGEMEMO_HTTP_PORT=5555`

### Health Check (socket)
```bash
curl --unix-socket /tmp/forgememo.sock http://localhost/health
```

### Health Check (HTTP, opt-in)
```bash
FORGEMEMO_HTTP_PORT=5555 forgememo daemon
curl http://127.0.0.1:5555/health
```

## Ingest an Event (Hook-style)

```bash
curl --unix-socket /tmp/forgememo.sock \
  -X POST http://localhost/events \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "s1",
    "project_id": "/path/to/repo",
    "source_tool": "codex",
    "event_type": "PostToolUse",
    "tool_name": "Bash",
    "payload": "{\"command\":\"echo hi\",\"stdout\":\"hi\"}",
    "seq": 1
  }'
```

## Search (Layer 1)

```bash
curl --unix-socket /tmp/forgememo.sock \
  "http://localhost/search?q=database&k=10&project_id=/path/to/repo"
```

## Timeline (Layer 2)

```bash
curl --unix-socket /tmp/forgememo.sock \
  "http://localhost/timeline?anchor_id=d:42&project_id=/path/to/repo"
```

## Observation (Layer 3)

```bash
curl --unix-socket /tmp/forgememo.sock \
  "http://localhost/observation/d/42"
```

## Session Summary

```bash
curl --unix-socket /tmp/forgememo.sock \
  -X POST http://localhost/session_summaries \
  -H "Content-Type: application/json" \
  -d '{
    "request": "Investigate auth timeout",
    "project_id": "/path/to/repo",
    "source_tool": "mcp",
    "learnings": "Root cause was idle connection pool",
    "next_steps": "Add keepalive + retry",
    "concepts": ["performance","gotcha"]
  }'
```

## Worker

```bash
forgememo worker
```

The worker distills `events` into `distilled_summaries`.

