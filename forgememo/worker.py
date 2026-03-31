#!/usr/bin/env python3
"""
Forgememo background worker — distills raw events into distilled_summaries.
"""

from __future__ import annotations

import json
import time

from forgememo import inference
from forgememo.storage import get_conn

MAX_DISTILL_ATTEMPTS = 3
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
        except Exception:
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

    def run_forever(self):
        while True:
            processed = self.process_one()
            if processed is None:
                time.sleep(self.sleep_seconds)


def main():
    worker = Worker()
    worker.run_forever()


if __name__ == "__main__":
    main()
