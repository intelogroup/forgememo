#!/usr/bin/env python3
"""
Forgemem Daemon — spawns Flask API server with graceful shutdown handling.

Usage:
  python3 forgemem_daemon.py

This process:
- Runs the Flask API on port 5555
- Manages the webhook retry worker thread
- Handles SIGTERM/SIGINT for graceful shutdown
- Logs to forgemem_daemon.log
- Suitable for systemd/launchd management

Register with launchctl:
  launchctl load ~/Library/LaunchAgents/com.forgemem.api.plist
"""

import signal
import sys
import logging
from pathlib import Path

# Set up logging
FORGEMEM_DIR = Path(__file__).parent
LOG_FILE = FORGEMEM_DIR / "forgemem_daemon.log"

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

# Import after logging setup
from forgemem.api import create_app, init_pool, init_db, webhook_retry_worker  # noqa: E402
import threading  # noqa: E402


class GracefulShutdown:
    """Handle graceful shutdown signals."""
    
    def __init__(self):
        self.shutdown = False
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)
    
    def _signal_handler(self, sig, frame):
        logger.info(f"Received signal {sig}, shutting down gracefully...")
        self.shutdown = True


def _check_port(host: str, port: int) -> bool:
    """Return True if port is already in use."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex((host, port)) == 0


def main():
    """Initialize and run the daemon."""
    logger.info("=" * 80)
    logger.info("Forgemem Daemon Starting")
    logger.info("=" * 80)

    if _check_port("127.0.0.1", 5555):
        logger.error("Port 5555 already in use — another instance may be running. Exiting.")
        sys.exit(1)

    try:
        # Initialize database pool and schema
        logger.info("Initializing database pool...")
        init_pool()
        
        logger.info("Initializing database schema...")
        init_db()
        
        # Start webhook retry worker
        logger.info("Starting webhook retry worker...")
        webhook_thread = threading.Thread(target=webhook_retry_worker, daemon=True)
        webhook_thread.start()
        
        # Create and configure Flask app
        logger.info("Creating Flask application...")
        app = create_app()
        
        # Set up graceful shutdown
        GracefulShutdown()
        
        logger.info("Starting Flask API server on 127.0.0.1:5555...")
        logger.info("Health check: curl http://127.0.0.1:5555/health")
        
        # Run Flask (blocks until shutdown)
        app.run(host="127.0.0.1", port=5555, debug=False, threaded=True, use_reloader=False)
        
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)
    
    logger.info("=" * 80)
    logger.info("Forgemem Daemon Stopped")
    logger.info("=" * 80)


if __name__ == "__main__":
    main()
