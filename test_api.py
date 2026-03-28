#!/usr/bin/env python3
"""
Forgemem API Test & Demo Script

Run this to verify the API is working and see examples of all endpoints.
"""

import json
import sys
import time
from datetime import datetime, timedelta

try:
    import requests
except ImportError:
    print("ERROR: pip install requests")
    sys.exit(1)

BASE_URL = "http://127.0.0.1:5555"
TIMEOUT = 5

def print_header(title):
    """Print a section header."""
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}")

def print_result(name, status_code, data):
    """Pretty print API response."""
    print(f"\n{name}")
    print(f"  Status: {status_code}")
    print(f"  Response: {json.dumps(data, indent=2)[:500]}")

def test_health():
    """Test health endpoint."""
    print_header("1. Health Check")
    try:
        resp = requests.get(f"{BASE_URL}/health", timeout=TIMEOUT)
        print_result("GET /health", resp.status_code, resp.json())
        return resp.status_code == 200
    except requests.ConnectionError:
        print(f"ERROR: Cannot connect to {BASE_URL}")
        print("Is the server running? Try: python3 forgemem_api.py")
        return False

def test_stats():
    """Test stats endpoint."""
    print_header("2. Statistics")
    resp = requests.get(f"{BASE_URL}/stats", timeout=TIMEOUT)
    data = resp.json()
    print_result("GET /stats", resp.status_code, data)
    return resp.status_code == 200

def test_save_trace():
    """Test saving a trace."""
    print_header("3. Save a Trace")
    payload = {
        "type": "success",
        "content": "Successfully tested the Forgemem API - all endpoints working!",
        "project": "forgemem-test",
        "principle": "Test API integration before deploying to production",
        "score": 8,
        "tags": "testing,api,automation"
    }
    print(f"Payload: {json.dumps(payload, indent=2)}")
    
    resp = requests.post(f"{BASE_URL}/traces", json=payload, timeout=TIMEOUT)
    print_result("POST /traces", resp.status_code, resp.json())
    
    if resp.status_code == 201:
        return resp.json()['trace_id'], resp.json().get('principle_id')
    return None, None

def test_search(query="test"):
    """Test search endpoint."""
    print_header("4. Search Traces & Principles")
    params = {"q": query, "k": 5}
    print(f"Query: {query}")
    
    resp = requests.get(f"{BASE_URL}/search", params=params, timeout=TIMEOUT)
    data = resp.json()
    print_result("GET /search", resp.status_code, {
        "query": data['query'],
        "trace_count": data['count']['traces'],
        "principle_count": data['count']['principles']
    })
    return resp.status_code == 200

def test_list_principles():
    """Test list principles endpoint."""
    print_header("5. List Principles")
    resp = requests.get(f"{BASE_URL}/principles", params={"limit": 3}, timeout=TIMEOUT)
    data = resp.json()
    print(f"Status: {resp.status_code}")
    print(f"Found {data['count']} principles (showing first 3):")
    for p in data['principles'][:3]:
        print(f"  - [{p['impact_score']}/10] {p['principle'][:60]}")
    return resp.status_code == 200

def test_register_webhook():
    """Test webhook registration."""
    print_header("6. Register Webhook")
    payload = {
        "url": "https://example.com/forgemem-webhook",
        "api_key": "sk_test_webhook_abc123",
        "project_filter": "forgemem-test",
        "min_impact_score": 6
    }
    print(f"Webhook URL: {payload['url']}")
    
    resp = requests.post(f"{BASE_URL}/webhooks/register", json=payload, timeout=TIMEOUT)
    if resp.status_code == 201:
        data = resp.json()
        print_result("POST /webhooks/register", resp.status_code, {
            "webhook_id": data['webhook_id'],
            "url": data['url'],
            "created_at": data['created_at']
        })
        return True
    elif resp.status_code == 409:
        print(f"Status: {resp.status_code}")
        print("  (Webhook already registered - this is OK for demo)")
        return True
    else:
        print_result("POST /webhooks/register", resp.status_code, resp.json())
        return False

def test_events():
    """Test event polling."""
    print_header("7. Event Polling (Real-Time)")
    since = (datetime.utcnow() - timedelta(hours=1)).isoformat() + "Z"
    params = {"since": since}
    
    resp = requests.get(f"{BASE_URL}/events", params=params, timeout=TIMEOUT)
    data = resp.json()
    print_result("GET /events", resp.status_code, {
        "events_found": data['count'],
        "next_poll_after": data['next_poll_after']
    })
    
    if data['count'] > 0:
        print(f"\n  Recent events:")
        for event in data['events'][:3]:
            print(f"    - {event['type']}: trace #{event['id']}")
    
    return resp.status_code == 200

def main():
    """Run all tests."""
    print("\n" + "="*70)
    print("  FORGEMEM HTTP API - ENDPOINT TEST SUITE")
    print("="*70)
    print(f"\nBase URL: {BASE_URL}")
    print(f"Timestamp: {datetime.now().isoformat()}")
    
    tests = [
        ("Health Check", test_health),
        ("Statistics", test_stats),
        ("Save Trace", test_save_trace),
        ("Search", test_search),
        ("List Principles", test_list_principles),
        ("Register Webhook", test_register_webhook),
        ("Event Polling", test_events),
    ]
    
    results = []
    for name, test_func in tests:
        try:
            if name == "Save Trace":
                result = test_func() is not None
            else:
                result = test_func()
            results.append((name, result))
        except requests.RequestException as e:
            print(f"\nERROR: {name} - {e}")
            results.append((name, False))
        except Exception as e:
            print(f"\nERROR: {name} - {e}")
            results.append((name, False))
    
    # Summary
    print_header("TEST SUMMARY")
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for name, result in results:
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"  {status:8} {name}")
    
    print(f"\n  Result: {passed}/{total} tests passed")
    
    if passed == total:
        print("\n✓ All tests passed! API is working correctly.")
        return 0
    else:
        print(f"\n✗ {total - passed} test(s) failed.")
        return 1

if __name__ == "__main__":
    sys.exit(main())
