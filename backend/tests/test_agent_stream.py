import json
import unittest

try:
    import app as app_module
except ModuleNotFoundError:
    app_module = None


class FakeOrchestrator:
    def run_turn(
        self,
        *,
        message: str,
        session_id: str | None,
        prefer_connector: bool,
        runtime_preference: str = "",
        event_sink=None,
    ):
        return {
            "message": f"response for {message}",
            "session_id": session_id or "generated-session",
            "meta": {
                "runtime": "anthropic_agent_sdk",
                "fallback_used": False,
                "fallback_reason": None,
                "session_id": session_id or "generated-session",
                "history_size": 2,
                "server_count": 1,
                "debug": {
                    "agent_sdk": {
                        "tool_events": [
                            {
                                "type": "mcp_tool_use",
                                "tool_name": "ckan__search_datasets",
                                "server_name": "opencontext-main",
                                "tool_use_id": "tool-1",
                            },
                            {
                                "type": "mcp_tool_result",
                                "tool_use_id": "tool-1",
                                "is_error": False,
                                "text_preview": "ok",
                            },
                        ]
                    }
                },
            },
        }


@unittest.skipIf(app_module is None, "Flask runtime not available in this environment")
class AgentStreamEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_orchestrator = app_module.agent_orchestrator
        app_module.agent_orchestrator = FakeOrchestrator()
        self.client = app_module.create_app().test_client()

    def tearDown(self) -> None:
        app_module.agent_orchestrator = self.original_orchestrator

    def test_non_stream_chat_returns_response(self) -> None:
        response = self.client.post(
            "/api/v1/agent/chat",
            json={"message": "hello", "session_id": "session-1", "prefer_connector": True},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["meta"]["runtime"], "anthropic_agent_sdk")
        self.assertEqual(payload["session_id"], "session-1")

    def test_stream_chat_emits_status_progress_delta_and_done(self) -> None:
        response = self.client.post(
            "/api/v1/agent/chat/stream",
            json={"message": "hello", "session_id": "session-1", "prefer_connector": True},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "text/event-stream")
        body = response.get_data(as_text=True)

        self.assertIn("event: status", body)
        self.assertIn("event: tool_progress", body)
        self.assertIn("event: delta", body)
        self.assertIn("event: done", body)

        frames = [chunk for chunk in body.split("\n\n") if chunk.strip()]
        done_frames = [frame for frame in frames if frame.startswith("event: done")]
        self.assertEqual(len(done_frames), 1)
        done_data_line = [line for line in done_frames[0].split("\n") if line.startswith("data: ")][0]
        done_payload = json.loads(done_data_line[6:])
        self.assertEqual(done_payload["meta"]["runtime"], "anthropic_agent_sdk")
        self.assertEqual(done_payload["session_id"], "session-1")

    def test_stream_chat_requires_message(self) -> None:
        response = self.client.post("/api/v1/agent/chat/stream", json={"message": ""})
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
