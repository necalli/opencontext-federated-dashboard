from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List

from .storage import Storage


class RunTraceNotFoundError(Exception):
    def __init__(self, run_id: str) -> None:
        super().__init__(f"Run not found: {run_id}")
        self.run_id = run_id


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_int(value: Any, fallback: int) -> int:
    try:
        return int(value)
    except Exception:
        return int(fallback)


class RunTraceService:
    def __init__(self, storage: Storage) -> None:
        self.storage = storage
        self.max_runs = max(100, _to_int(os.getenv("RUN_TRACE_MAX_ENTRIES", "800"), 800))

    def log_run(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        run_id = str(payload.get("run_id") or "").strip() or str(uuid.uuid4())
        created_at = str(payload.get("created_at") or "").strip() or _iso_now()
        completed_at = str(payload.get("completed_at") or "").strip() or _iso_now()

        record = dict(payload)
        record["run_id"] = run_id
        record["created_at"] = created_at
        record["completed_at"] = completed_at
        if "status" not in record:
            record["status"] = "completed"
        if "tool_events" not in record or not isinstance(record.get("tool_events"), list):
            record["tool_events"] = []
        if "errors" not in record or not isinstance(record.get("errors"), list):
            record["errors"] = []
        if "warnings" not in record or not isinstance(record.get("warnings"), list):
            record["warnings"] = []

        runs = self.storage.get_run_traces()
        runs.append(record)
        if len(runs) > self.max_runs:
            runs = runs[-self.max_runs :]
        self.storage.save_run_traces(runs)
        return record

    def list_runs(self, *, limit: int = 50, session_id: str = "") -> List[Dict[str, Any]]:
        max_items = max(1, min(200, int(limit)))
        target_session = str(session_id or "").strip()
        runs = self.storage.get_run_traces()
        ordered = list(reversed(runs))
        if target_session:
            ordered = [row for row in ordered if str(row.get("session_id") or "").strip() == target_session]
        sliced = ordered[:max_items]
        return [self._summary(row) for row in sliced]

    def get_run(self, run_id: str) -> Dict[str, Any]:
        target = str(run_id or "").strip()
        runs = self.storage.get_run_traces()
        for row in reversed(runs):
            if str(row.get("run_id") or "").strip() == target:
                return row
        raise RunTraceNotFoundError(target)

    @staticmethod
    def _summary(row: Dict[str, Any]) -> Dict[str, Any]:
        request = row.get("request") if isinstance(row.get("request"), dict) else {}
        response = row.get("response") if isinstance(row.get("response"), dict) else {}
        message = str(request.get("message") or "").strip()
        runtime = str(response.get("runtime") or "").strip()
        return {
            "run_id": str(row.get("run_id") or "").strip(),
            "created_at": str(row.get("created_at") or "").strip(),
            "completed_at": str(row.get("completed_at") or "").strip(),
            "duration_ms": int(row.get("duration_ms") or 0),
            "status": str(row.get("status") or "").strip() or "completed",
            "session_id": str(row.get("session_id") or "").strip(),
            "endpoint": str(row.get("endpoint") or "").strip(),
            "runtime": runtime,
            "fallback_used": bool(response.get("fallback_used")),
            "message_preview": (message[:200] + "...") if len(message) > 200 else message,
            "tool_event_count": len(row.get("tool_events")) if isinstance(row.get("tool_events"), list) else 0,
            "error_count": len(row.get("errors")) if isinstance(row.get("errors"), list) else 0,
        }
