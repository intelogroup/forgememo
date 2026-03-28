#!/usr/bin/env python3
"""
Example: How Claude Code would use Forgemem during development.

This script demonstrates a realistic workflow where an AI agent (Claude, Copilot, Gemini)
is working on a feature in their project and uses Forgemem to:
1. Search for relevant past learnings before starting
2. Save new learnings as they discover them
3. Build on accumulated knowledge over time
"""

import requests
import json
from datetime import datetime
from typing import Optional

BASE_URL = "http://127.0.0.1:5555"


class ForgememClient:
    """Simple wrapper for Forgemem API."""
    
    def __init__(self, base_url: str = BASE_URL):
        self.base = base_url
    
    def save_trace(
        self,
        type: str,
        content: str,
        project: str,
        principle: Optional[str] = None,
        score: int = 5,
        tags: Optional[str] = None,
        session: Optional[str] = None
    ) -> dict:
        """Save a trace (learning)."""
        resp = requests.post(
            f"{self.base}/traces",
            json={
                "type": type,
                "content": content,
                "project": project,
                "principle": principle,
                "score": score,
                "tags": tags,
                "session": session
            },
            timeout=5
        )
        return resp.json()
    
    def search(self, query: str, project: Optional[str] = None, limit: int = 10) -> dict:
        """Search for traces and principles."""
        params: dict = {"q": query, "k": limit}
        if project:
            params["project"] = project
        
        resp = requests.get(f"{self.base}/search", params=params, timeout=5)
        return resp.json()
    
    def get_principles(self, project: Optional[str] = None, limit: int = 10) -> list:
        """Get top principles for a project."""
        params = {"limit": limit}
        if project:
            params["project"] = project
        
        resp = requests.get(f"{self.base}/principles", params=params, timeout=5)
        return resp.json()["principles"]


def print_section(title: str):
    """Print formatted section header."""
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}\n")


def claude_dev_session_example():
    """
    Example: Claude Code working on a feature.
    
    Scenario: Claude is implementing a caching layer for a database-heavy API.
    """
    
    client = ForgememClient()
    project = "ecommerce-api"
    session = f"caching-feature-{datetime.now().strftime('%Y-%m-%d')}"
    
    # ─────────────────────────────────────────────────────────────────────────
    # PHASE 1: PRE-WORK - Search for relevant knowledge
    # ─────────────────────────────────────────────────────────────────────────
    
    print_section("PHASE 1: Claude starts working on caching feature")
    print("Claude: 'I need to implement a caching layer. Let me check what we know about this...'")
    print()
    
    # Search for past learnings about caching
    print("→ Searching for past learnings about caching...")
    results = client.search(query="caching", project=project, limit=10)
    
    if results['count']['principles'] > 0:
        print(f"Found {results['count']['principles']} related principles:")
        for p in results['results']['principles'][:3]:
            print(f"  • [{p['impact_score']}/10] {p['principle']}")
            print(f"    Tags: {p['tags']}")
    
    if results['count']['traces'] > 0:
        print(f"\nFound {results['count']['traces']} related traces:")
        for t in results['results']['traces'][:2]:
            print(f"  • {t['content'][:70]}...")
    
    # ─────────────────────────────────────────────────────────────────────────
    # PHASE 2: WORKING - Implement and save learnings as they discover them
    # ─────────────────────────────────────────────────────────────────────────
    
    print_section("PHASE 2: Claude implements caching (discovers learnings)")
    
    # Learning 1: Implementation approach
    print("→ Claude discovers first learning about Redis vs in-memory caching...")
    result = client.save_trace(
        type="success",
        content="Implemented Redis-backed cache with TTL for database queries. Chose Redis over in-memory because: (1) shared across instances, (2) persistent, (3) existing infrastructure. In-memory caching led to stale data in multi-instance setup.",
        project=project,
        principle="Use Redis for distributed caches when multi-instance deployment is expected",
        score=9,
        tags="caching,redis,architecture",
        session=session
    )
    print(f"✓ Saved principle #{result['principle_id']}: {result['message']}")
    
    # Learning 2: Invalidation strategy
    print("\n→ Claude discovers important lesson about cache invalidation...")
    result = client.save_trace(
        type="failure",
        content="First attempted simple TTL-based invalidation. Caused stale product data for 5 minutes during sales. Added write-through cache invalidation pattern: whenever inventory updates, immediately invalidate affected cache keys.",
        project=project,
        principle="Combine TTL with event-driven cache invalidation for critical data",
        score=10,
        tags="caching,invalidation,critical",
        session=session
    )
    print(f"✓ Saved principle #{result['principle_id']}: {result['message']}")
    
    # Learning 3: Error handling
    print("\n→ Claude discovers critical error handling requirement...")
    result = client.save_trace(
        type="success",
        content="Added graceful cache fallback: if Redis is down, queries hit database directly instead of crashing. Wrapped all cache operations in try-except with circuit breaker to prevent thundering herd.",
        project=project,
        principle="Always provide database fallback when cache is unavailable",
        score=9,
        tags="caching,error-handling,resilience",
        session=session
    )
    print(f"✓ Saved principle #{result['principle_id']}: {result['message']}")
    
    # Learning 4: Performance optimization
    print("\n→ Claude discovers performance optimization...")
    result = client.save_trace(
        type="success",
        content="Batch cache reads for list endpoints: instead of N Redis calls, use MGET to fetch multiple keys in single round trip. Reduced API latency from 250ms to 60ms for category listings.",
        project=project,
        principle="Use batch operations (MGET, HMGET) to reduce round-trip cache calls",
        score=8,
        tags="caching,performance,optimization",
        session=session
    )
    print(f"✓ Saved principle #{result['principle_id']}: {result['message']}")
    
    # ─────────────────────────────────────────────────────────────────────────
    # PHASE 3: REVIEW - Search newly saved principles
    # ─────────────────────────────────────────────────────────────────────────
    
    print_section("PHASE 3: Claude reviews what was learned")
    print("Claude: 'Let me review all the principles I discovered today...'")
    print()
    
    principles = client.get_principles(project=project, limit=20)
    caching_principles = [p for p in principles if 'caching' in (p['tags'] or '').lower()]
    
    print(f"Caching-related principles in {project}:")
    for p in caching_principles[:5]:
        score_bar = "█" * p['impact_score'] + "░" * (10 - p['impact_score'])
        print(f"  [{score_bar}] {p['principle'][:60]}")
        print(f"     Tags: {p['tags']}")
    
    # ─────────────────────────────────────────────────────────────────────────
    # PHASE 4: FUTURE - Next agent benefits from accumulated knowledge
    # ─────────────────────────────────────────────────────────────────────────
    
    print_section("PHASE 4: Future - Copilot works on a related feature")
    print("Copilot: 'I need to add caching to the search API. Let me check what we've learned...'")
    print()
    
    # Copilot searches for caching knowledge
    results = client.search(query="cache invalidation event-driven", project=project, limit=5)
    
    print("Copilot finds directly applicable principles:")
    for p in results['results']['principles']:
        print(f"\n  📌 {p['principle']}")
        print(f"     Impact: {p['impact_score']}/10 | Tags: {p['tags']}")
    
    print("\n→ Copilot immediately applies: 'I see! I should use write-through cache")
    print("  invalidation instead of just TTL. This will prevent stale search results.'")
    
    print("\n✓ Copilot saves weeks of debugging because Claude's learnings are captured!")


def claude_code_quick_integration():
    """
    Show how this would work in a Claude Code session interactively.
    """
    
    print_section("ALTERNATIVE: Interactive Claude Code Session")
    
    print("""
In Claude Code terminal, this is as simple as:

```python
>>> import requests
>>> 
>>> # Before starting work: search for related learnings
>>> resp = requests.get("http://127.0.0.1:5555/search",
...   params={"q": "database optimization", "project": "my-app"})
>>> for p in resp.json()['results']['principles'][:3]:
...   print(f"- {p['principle']}")
- Always use database indexes on foreign keys
- Implement connection pooling for database access
- Use query result caching to reduce lookups

>>> # After fixing the bug...
>>> resp = requests.post("http://127.0.0.1:5555/traces", json={
...   "type": "success",
...   "content": "Fixed N+1 query by switching to LEFT JOIN with GROUP BY",
...   "project": "my-app",
...   "principle": "Analyze query execution plans before optimizing",
...   "score": 8
... })
>>> print(f"✓ Saved trace #{resp.json()['trace_id']}")
✓ Saved trace #1234
```

That's it! The learning is now available to all agents working on that project.
    """)


def show_api_comparison():
    """Compare different agent access methods."""
    
    print_section("COMPARISON: How Different Agents Would Access Forgemem")
    
    print("""
┌─────────────────────────────────────────────────────────────────────────┐
│  CLAUDE CODE (in project terminal)                                      │
├─────────────────────────────────────────────────────────────────────────┤
│  Option A: Direct MCP (native integration)                              │
│  search_memory("caching")  # Built-in Claude Code tool                  │
│  save_trace(...)           # Direct SQLite access                       │
│                                                                         │
│  Option B: HTTP API (more flexible)                                     │
│  requests.get("http://127.0.0.1:5555/search?q=caching")               │
└─────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────┐
│  GITHUB COPILOT (in VS Code, any project)                              │
├─────────────────────────────────────────────────────────────────────────┤
│  Via terminal (shell wrapper):                                          │
│  forgemem save "Fixed bug" "lesson-here" my-project                    │
│                                                                         │
│  Via JavaScript/Node.js:                                                │
│  fetch("http://127.0.0.1:5555/traces", {...})                         │
│                                                                         │
│  Via Makefile:                                                          │
│  make learn                                                             │
│  make search-brain QUERY="caching"                                     │
└─────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────┐
│  GOOGLE GEMINI / CLAUDE API (Remote Calls)                             │
├─────────────────────────────────────────────────────────────────────────┤
│  Custom tool in Gemini/Claude system:                                   │
│  GET /search?q=<query>&project=<project>                               │
│  POST /traces {type, content, project, principle}                      │
│                                                                         │
│  No MCP needed - just HTTP to localhost:5555                           │
└─────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────┐
│  CI/CD PIPELINES (GitHub Actions, etc)                                 │
├─────────────────────────────────────────────────────────────────────────┤
│  Capture learnings from test failures:                                  │
│  curl -X POST http://127.0.0.1:5555/traces \\                          │
│    --data '{"type":"failure",...}'                                      │
│                                                                         │
│  Or use Git hooks to auto-capture from commits                          │
└─────────────────────────────────────────────────────────────────────────┘
    """)


if __name__ == "__main__":
    try:
        # Test connection
        resp = requests.get(f"{BASE_URL}/health", timeout=2)
        if resp.status_code != 200:
            raise ConnectionError("API not healthy")
    except Exception as e:
        print(f"ERROR: Cannot connect to Forgemem API at {BASE_URL}")
        print(f"Make sure the server is running: python3 forgemem_api.py")
        print(f"\nError: {e}")
        import sys
        sys.exit(1)
    
    # Run examples
    claude_dev_session_example()
    claude_code_quick_integration()
    show_api_comparison()
    
    print_section("KEY TAKEAWAY")
    print("""
Any AI agent can access Forgemem from their project terminal:

1. Claude Code → Uses MCP OR curl/requests from terminal
2. Copilot → Uses curl/JavaScript/Makefile from terminal
3. Gemini → Calls HTTP API from custom tool
4. Any language → HTTP calls to localhost:5555

The same knowledge base powers all agents working on your projects!
    """)
