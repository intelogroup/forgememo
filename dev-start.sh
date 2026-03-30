#!/usr/bin/env bash
set -e

BASTION_ID="ocid1.bastion.oc1.iad.amaaaaaanefylnyaflhg3jfposjjxujhncyi2yn5hcuhppg7gammfiaqkfcq"
BASTION_HOST="host.bastion.us-ashburn-1.oci.oraclecloud.com"
MYSQL_IP="10.0.69.24"
MYSQL_PORT="3306"
LOCAL_PORT="3307"
SSH_KEY="$HOME/.ssh/id_ed25519"
PUB_KEY="$HOME/.ssh/id_ed25519.pub"

echo "==> Creating Bastion session..."
SESSION_JSON=$(oci bastion session create-port-forwarding \
  --bastion-id "$BASTION_ID" \
  --ssh-public-key-file "$PUB_KEY" \
  --target-private-ip "$MYSQL_IP" \
  --target-port "$MYSQL_PORT" \
  --session-ttl 10800)

SESSION_ID=$(echo "$SESSION_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['id'])")
echo "    Session: $SESSION_ID"

echo "==> Waiting for session to become ACTIVE..."
for i in $(seq 1 20); do
  STATE=$(oci bastion session get --session-id "$SESSION_ID" --query "data.\"lifecycle-state\"" --raw-output 2>/dev/null)
  echo "    State: $STATE"
  [ "$STATE" = "ACTIVE" ] && break
  sleep 5
done

sleep 3  # allow OCI to fully provision the session key before SSH

if [ "$STATE" != "ACTIVE" ]; then
  echo "ERROR: Session did not become ACTIVE in time."
  exit 1
fi

echo "==> Starting SSH tunnel on 127.0.0.1:$LOCAL_PORT..."
pkill -f "3307:$MYSQL_IP" 2>/dev/null || true
ssh -i "$SSH_KEY" -N -f \
  -L "$LOCAL_PORT:$MYSQL_IP:$MYSQL_PORT" \
  -p 22 \
  -o StrictHostKeyChecking=no \
  -o ServerAliveInterval=60 \
  "${SESSION_ID}@${BASTION_HOST}"
echo "    Tunnel up."

echo "==> Starting FastAPI server..."
cd "$(dirname "$0")/server"
pkill -f "uvicorn main:app" 2>/dev/null || true
sleep 1
set -a && source .env && set +a
python3 -m uvicorn main:app --port 8000 &
SERVER_PID=$!
sleep 3

if kill -0 $SERVER_PID 2>/dev/null; then
  echo ""
  echo "All systems go:"
  echo "  API:    http://localhost:8000"
  echo "  Webapp: http://localhost:3000  (run 'npm run dev' in webapp/)"
  echo ""
else
  echo "ERROR: Server failed to start. Check /tmp/server.log"
  exit 1
fi
