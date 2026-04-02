#!/usr/bin/env python3
"""
Forgememo background worker — distills raw events into distilled_summaries.
"""

from __future__ import annotations

import json
import logging
import time

from forgememo import inference
from forgememo.storage import get_conn

logger = logging.getLogger(__name__)

MAX_DISTILL_ATTEMPTS = 3
BATCH_SIZE = 10
ALLOWED_CONCEPTS = {
    "security",
    "pattern",
    "gotcha",
    "performance",
    "trade-off",
    "how-it-works",
}


class Worker:
    def __init__(self, sleep_seconds: int = 2):
        self.sleep_seconds = sleep_seconds

    def process_one(self) -> int | None:
        conn = get_conn()
        event = conn.execute(
            "SELECT * FROM events WHERE distilled=0 AND distill_attempts < ? "
            "ORDER BY id LIMIT 1",
            (MAX_DISTILL_ATTEMPTS,),
        ).fetchone()
        if not event:
            conn.close()
            return None
        event_dict = dict(event)

        try:
            summary = self.distill_event(event_dict)
        except Exception as exc:
            attempt = event_dict["distill_attempts"] + 1
            logger.warning(
                "Distillation failed for event %s (attempt %d/%d): %s",
                event_dict["id"],
                attempt,
                MAX_DISTILL_ATTEMPTS,
                exc,
            )
            if attempt >= MAX_DISTILL_ATTEMPTS:
                logger.warning(
                    "Event %s permanently skipped after %d failed attempts. "
                    "Check provider config: run `forgememo config -i`.",
                    event_dict["id"],
                    MAX_DISTILL_ATTEMPTS,
                )
            conn.execute(
                "UPDATE events SET distill_attempts = distill_attempts + 1 WHERE id=?",
                (event_dict["id"],),
            )
            conn.commit()
            conn.close()
            return None

        try:
            conn.execute("BEGIN")
            conn.execute(
                "INSERT INTO distilled_summaries "
                "(source_event_id, session_id, project_id, source_tool, type, title, "
                "narrative, facts, files_read, files_modified, concepts, impact_score) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    event_dict["id"],
                    event_dict["session_id"],
                    event_dict["project_id"],
                    event_dict["source_tool"],
                    summary["type"],
                    summary["title"],
                    summary["narrative"],
                    json.dumps(summary.get("facts", [])),
                    json.dumps(summary.get("files_read", [])),
                    json.dumps(summary.get("files_modified", [])),
                    json.dumps(summary.get("concepts", [])),
                    summary.get("impact_score", 5),
                ),
            )
            conn.execute(
                "UPDATE events SET distilled=1 WHERE id=?",
                (event_dict["id"],),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()
        return event_dict["id"]

    def distill_event(self, event: dict) -> dict:
        payload = event.get("payload", "")
        # Short-circuit: scanner pre-extracts distillation data to avoid double API calls
        try:
            payload_data = json.loads(payload) if isinstance(payload, str) else payload
        except Exception:
            payload_data = {}
        if isinstance(payload_data, dict) and payload_data.get("_principle"):
            concepts = [t for t in payload_data.get("_tags", []) if t in ALLOWED_CONCEPTS]
            return {
                "type": payload_data.get("_type", "note"),
                "title": str(payload_data["_principle"])[:100],
                "narrative": payload_data.get("content", ""),
                "facts": [],
                "files_read": [],
                "files_modified": [],
                "concepts": concepts,
                "impact_score": int(payload_data.get("_impact_score", 5)),
            }

        prompt = f"""
Analyze this tool event and extract a distilled learning.
Return JSON only:
{{
  "type": "bugfix|feature|decision|refactor|discovery|note",
  "title": "1-line summary",
  "narrative": "2-3 sentence story",
  "facts": ["key insight 1", "key insight 2"],
  "files_read": [],
  "files_modified": [],
  "concepts": ["security","pattern","gotcha","performance","trade-off","how-it-works"],
  "impact_score": 1-10
}}

Event:
{json.dumps(payload)[:2000]}
"""
        raw = json.loads(inference.call(prompt, max_tokens=500))
        raw["concepts"] = [c for c in raw.get("concepts", []) if c in ALLOWED_CONCEPTS]
        return raw

    def process_batch(self) -> int:
        """Process up to BATCH_SIZE events. Returns count processed."""
        processed = 0
        for _ in range(BATCH_SIZE):
            if self.process_one() is None:
                break
            processed += 1
        return processed

    def run_forever(self):
        while True:
            if self.process_batch() == 0:
                time.sleep(self.sleep_seconds)


def main():
    worker = Worker()
    worker.run_forever()


if __name__ == "__main__":
    main()
