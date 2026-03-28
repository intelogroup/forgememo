#!/bin/bash
# Setup Forgemem HTTP API
# Run this once to install dependencies and set up the service

set -e

FORGEMEM_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON=${PYTHON:-python3}

echo "=========================================="
echo "  Forgemem HTTP API Setup"
echo "=========================================="
echo ""

# 1. Install Python dependencies
echo "1. Installing Python dependencies..."
$PYTHON -m pip install flask requests -q
echo "   ✓ Dependencies installed"
echo ""

# 2. Initialize database
echo "2. Initializing database..."
$PYTHON << 'EOF'
import sys
sys.path.insert(0, '/Users/kalinovdameus/Developer/Forgemem')
import forgemem_api
forgemem_api.init_pool()
forgemem_api.init_db()
print("   ✓ Database initialized/migrated")
EOF
echo ""

# 3. Test API locally
echo "3. Testing API locally (5 seconds)..."
timeout 5 $PYTHON "$FORGEMEM_DIR/forgemem_api.py" > /dev/null 2>&1 &
PID=$!
sleep 2

if curl -s http://127.0.0.1:5555/health > /dev/null 2>&1; then
    echo "   ✓ API health check passed"
else
    echo "   ✗ API health check failed"
    kill $PID 2>/dev/null || true
    exit 1
fi

kill $PID 2>/dev/null || true
wait $PID 2>/dev/null || true
echo ""

# 4. Set up LaunchAgent (macOS only)
if [[ "$OSTYPE" == "darwin"* ]]; then
    echo "4. Setting up LaunchAgent for auto-start..."
    
    LAUNCH_AGENT="$HOME/Library/LaunchAgents/com.forgemem.api.plist"
    
    if [ -f "$LAUNCH_AGENT" ]; then
        echo "   LaunchAgent already installed at $LAUNCH_AGENT"
    else
        echo "   ✗ LaunchAgent file not found"
        echo "   Please create it manually: ~/Library/LaunchAgents/com.forgemem.api.plist"
    fi
    echo ""
    
    echo "5. To start the service:"
    echo "   launchctl load ~/Library/LaunchAgents/com.forgemem.api.plist"
    echo "   launchctl start com.forgemem.api"
    echo ""
else
    echo "4. LaunchAgent setup (macOS only - skipped)"
    echo ""
    echo "5. To run as a service on other systems:"
    echo "   - systemd: create /etc/systemd/system/forgemem-api.service"
    echo "   - supervisor: add forgemem_daemon.py to supervisord.conf"
    echo "   - pm2: pm2 start forgemem_daemon.py --name forgemem-api"
    echo ""
fi

echo "=========================================="
echo "  Setup Complete!"
echo "=========================================="
echo ""
echo "Next steps:"
echo "  1. Run the server (development):"
echo "     python3 forgemem_api.py"
echo ""
echo "  2. Test the API:"
echo "     python3 test_api.py"
echo ""
echo "  3. Query the API:"
echo "     curl http://127.0.0.1:5555/health"
echo "     curl http://127.0.0.1:5555/stats"
echo ""
echo "  4. See API documentation:"
echo "     cat API.md"
echo ""
