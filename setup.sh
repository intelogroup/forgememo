#!/usr/bin/env bash
# Forgemem setup — run once from ~/Developer/Forgemem/
set -e

FORGEMEM_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== Forgemem Setup ==="
echo "Dir: $FORGEMEM_DIR"

# 1. Install Python package
echo ""
echo "Installing Forgemem..."
pip3 install --quiet forgemem 2>&1 | tail -3

# 2. Init DB
echo ""
echo "Initializing database..."
forgemem init

# 3. Quick smoke test
echo ""
echo "Smoke test..."
forgemem store \
    --type success \
    --content "Forgemem setup completed successfully." \
    --project forgemem \
    --principle "Setup smoke test passed — DB, FTS5, and CLI all functional." \
    --score 3

forgemem search "setup"
forgemem status

echo ""
echo "=== Done ==="
echo "MCP: restart Claude Code to load the forgemem MCP server"
