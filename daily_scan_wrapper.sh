#!/usr/bin/env bash
# Wrapper that loads env vars before running the daily scan.
# LaunchAgents don't inherit shell profiles, so we source them here.
set -e

FORGEMEM_DIR="$HOME/Developer/Forgemem"

# 1. Try .env in Forgemem dir (dotenv file — preferred)
if [ -f "$FORGEMEM_DIR/.env" ]; then
    # shellcheck disable=SC1090
    set -a; source "$FORGEMEM_DIR/.env"; set +a
fi

# 2. Try dedicated secrets file
if [ -z "$ANTHROPIC_API_KEY" ] && [ -f "$HOME/.forgemem_env" ]; then
    # shellcheck disable=SC1090
    source "$HOME/.forgemem_env"
fi

# 3. Fallback: extract from ~/.zshrc
if [ -z "$ANTHROPIC_API_KEY" ] && [ -f "$HOME/.zshrc" ]; then
    KEY=$(grep -E '^export ANTHROPIC_API_KEY=' "$HOME/.zshrc" | tail -1 | sed 's/export ANTHROPIC_API_KEY=//' | tr -d '"'"'"' ')
    if [ -n "$KEY" ]; then
        export ANTHROPIC_API_KEY="$KEY"
    fi
fi

# Hard fail — never run silently with no key
if [ -z "$ANTHROPIC_API_KEY" ]; then
    echo "ERROR: ANTHROPIC_API_KEY not found in .env, ~/.forgemem_env, or ~/.zshrc" >&2
    exit 1
fi

exec python3 "$FORGEMEM_DIR/daily_scan.py"
