from __future__ import annotations

import os
import tempfile
import sys
from pathlib import Path


_tmp_log = os.path.join(tempfile.gettempdir(), "forgememo_daemon.log")
os.environ.setdefault("FORGEMEMO_DAEMON_LOG", _tmp_log)

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
