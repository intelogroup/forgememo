#!/usr/bin/env bash
# Forgemem setup — run once from ~/Developer/Forgemem/
set -e

FORGEMEM_DIR="$(cd "$(dirname "$0")" && pwd)"
DB_PATH="$FORGEMEM_DIR/forgemem_memory.db"
SETTINGS="$HOME/.claude/settings.json"

echo "=== Forgemem Setup ==="
echo "Dir: $FORGEMEM_DIR"

# 1. Install Python deps
echo ""
echo "Installing Python dependencies..."
pip3 install --quiet fastmcp anthropic 2>&1 | tail -3

# 2. Init DB
echo ""
echo "Initializing database..."
python3 "$FORGEMEM_DIR/forgemem.py" init

# 3. Add shell alias + env var (idempotent)
ZSHRC="$HOME/.zshrc"
if ! grep -q "FORGEMEM_ROOT" "$ZSHRC" 2>/dev/null; then
    echo "" >> "$ZSHRC"
    echo "# Forgemem — AI agent long-term memory" >> "$ZSHRC"
    echo "export FORGEMEM_ROOT=\"$FORGEMEM_DIR\"" >> "$ZSHRC"
    echo "alias bm=\"python3 $FORGEMEM_DIR/forgemem.py\"" >> "$ZSHRC"
    echo "  Added alias bm and FORGEMEM_ROOT to ~/.zshrc"
else
    echo "  ~/.zshrc already configured (skipping)"
fi

# 4. Register MCP server in ~/.claude/settings.json
echo ""
echo "Registering MCP server..."
if [ ! -f "$SETTINGS" ]; then
    echo '{}' > "$SETTINGS"
fi

python3 - <<PYEOF
import json, sys
from pathlib import Path

settings_path = Path("$SETTINGS")
forgemem_dir = "$FORGEMEM_DIR"

try:
    settings = json.loads(settings_path.read_text())
except (json.JSONDecodeError, FileNotFoundError):
    settings = {}

settings.setdefault("mcpServers", {})
settings["mcpServers"]["forgemem"] = {
    "command": "python3",
    "args": [f"{forgemem_dir}/mcp_server.py"]
}

settings_path.write_text(json.dumps(settings, indent=2))
print("  Registered forgemem MCP server in", settings_path)
PYEOF

# 5. Quick smoke test
echo ""
echo "Smoke test..."
python3 "$FORGEMEM_DIR/forgemem.py" save \
    --type success \
    --content "Forgemem setup completed successfully." \
    --project forgemem \
    --principle "Setup smoke test passed — DB, FTS5, and CLI all functional." \
    --score 3

python3 "$FORGEMEM_DIR/forgemem.py" retrieve "setup" --k 1
python3 "$FORGEMEM_DIR/forgemem.py" stats

echo ""
echo "=== Done ==="
echo "Run: source ~/.zshrc   (to activate 'bm' alias)"
echo "Then: bm retrieve 'your query'"
echo "MCP: restart Claude Code to load the forgemem MCP server"
