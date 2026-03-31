import tempfile
import unittest
from pathlib import Path

from services.run_traces import RunTraceNotFoundError, RunTraceService
from services.storage import Storage


class RunTraceServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage = Storage(root_dir=Path(self.temp_dir.name))
        self.service = RunTraceService(storage=self.storage)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_log_and_fetch_run(self) -> None:
        created = self.service.log_run(
            {
                "status": "completed",
                "endpoint": "/api/v1/agent/chat",
                "session_id": "session-a",
                "duration_ms": 123,
                "request": {"message": "hello"},
                "response": {"runtime": "anthropic_agent_sdk", "fallback_used": False},
                "tool_events": [{"phase": "tool_use", "tool_name": "ckan__search_datasets"}],
                "errors": [],
                "warnings": [],
            }
        )
        self.assertTrue(created.get("run_id"))
        fetched = self.service.get_run(created["run_id"])
        self.assertEqual(fetched["run_id"], created["run_id"])
        self.assertEqual(fetched["response"]["runtime"], "anthropic_agent_sdk")

    def test_list_runs_returns_latest_first(self) -> None:
        first = self.service.log_run(
            {
                "session_id": "session-a",
                "request": {"message": "first"},
                "response": {"runtime": "deterministic_mcp"},
            }
        )
        second = self.service.log_run(
            {
                "session_id": "session-b",
                "request": {"message": "second"},
                "response": {"runtime": "anthropic_agent_sdk"},
            }
        )
        rows = self.service.list_runs(limit=10)
        self.assertGreaterEqual(len(rows), 2)
        self.assertEqual(rows[0]["run_id"], second["run_id"])
        self.assertEqual(rows[1]["run_id"], first["run_id"])

    def test_list_runs_supports_session_filter(self) -> None:
        self.service.log_run(
            {"session_id": "session-a", "request": {"message": "a"}, "response": {"runtime": "x"}}
        )
        self.service.log_run(
            {"session_id": "session-b", "request": {"message": "b"}, "response": {"runtime": "y"}}
        )
        rows = self.service.list_runs(limit=10, session_id="session-a")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["session_id"], "session-a")

    def test_get_run_raises_for_missing_id(self) -> None:
        with self.assertRaises(RunTraceNotFoundError):
            self.service.get_run("missing-run")


if __name__ == "__main__":
    unittest.main()
