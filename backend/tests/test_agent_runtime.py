import unittest
from typing import Any, Dict, List

from services.agent_orchestrator import AgentOrchestrator
from services.agent_runtime import AgentRuntime
from services.anthropic_agent_sdk_runtime import AgentSDKRuntimeError
from services.anthropic_mcp_connector import AnthropicConnectorError


class FakeAgentSDKRuntime:
    def __init__(self, should_fail: bool = False) -> None:
        self.should_fail = should_fail
        self.calls = 0
        self.last_system_prompt = ""
        self.last_session_id = ""

    def generate(
        self,
        *,
        message: str,
        mcp_servers: List[Dict[str, Any]],
        history: List[Dict[str, Any]],
        session_id: str = "",
        system_prompt: str,
        skill_context: Dict[str, Any] | None = None,
        event_sink: Any | None = None,
    ) -> Dict[str, Any]:
        self.calls += 1
        self.last_system_prompt = system_prompt
        self.last_session_id = session_id
        if self.should_fail:
            raise AgentSDKRuntimeError(
                "agent_sdk_unavailable",
                "simulated sdk failure",
                details={"reason": "simulated"},
                retriable=False,
            )
        return {
            "text": f"sdk response: {message}",
            "response_id": "sdk_session_test",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 22, "output_tokens": 9},
            "server_names": [row.get("name") for row in mcp_servers],
            "tool_events": [
                {
                    "type": "mcp_tool_use",
                    "tool_name": "ckan__search_datasets",
                    "tool_use_id": "sdk-tool-1",
                    "server_name": "boston-open-data",
                }
            ],
            "visualizations": [
                {
                    "id": "viz_test_1",
                    "title": "Test chart",
                    "chart_type": "bar",
                    "records": [{"label": "A", "value": 1}],
                }
            ],
            "sdk_meta": {"session_id": "sdk_session_test"},
        }


class FakeConnectorRuntime:
    def __init__(self, should_fail: bool = False) -> None:
        self.should_fail = should_fail
        self.calls = 0
        self.last_system_prompt = ""

    def generate(
        self,
        *,
        message: str,
        mcp_servers: List[Dict[str, Any]],
        history: List[Dict[str, Any]],
        system_prompt: str,
    ) -> Dict[str, Any]:
        self.calls += 1
        self.last_system_prompt = system_prompt
        if self.should_fail:
            raise AnthropicConnectorError(
                "network_error",
                "connector unavailable",
                details={"reason": "simulated"},
                retriable=True,
            )

        return {
            "text": f"connector response: {message}",
            "response_id": "msg_test",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "server_names": [row.get("name") for row in mcp_servers],
        }


class FakeDeterministicRuntime:
    def __init__(self) -> None:
        self.calls = 0

    def run(
        self,
        *,
        message: str,
        servers: List[Dict[str, Any]],
        allowed_tool_patterns: List[str] | None = None,
    ) -> Dict[str, Any]:
        self.calls += 1
        return {
            "text": f"fallback response: {message}",
            "server_results": [
                {
                    "name": row.get("name"),
                    "ok": True,
                    "skill_scope_applied": bool(allowed_tool_patterns or []),
                }
                for row in servers
            ],
        }


class StubRegistry:
    def __init__(self, servers: List[Dict[str, Any]]) -> None:
        self._servers = servers

    def list_servers(self) -> List[Dict[str, Any]]:
        return list(self._servers)


class AgentRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.servers = [
            {
                "id": "srv-1",
                "name": "boston-open-data",
                "endpoint": "https://example.com/mcp",
                "enabled": True,
                "headers": {},
            }
        ]

    def test_sdk_runtime_used_when_available(self) -> None:
        sdk = FakeAgentSDKRuntime(should_fail=False)
        connector = FakeConnectorRuntime(should_fail=False)
        deterministic = FakeDeterministicRuntime()
        runtime = AgentRuntime(
            agent_sdk_runtime=sdk,
            connector_runtime=connector,
            deterministic_runtime=deterministic,
        )

        result = runtime.run(
            message="list tools",
            servers=self.servers,
            history=[],
            session_id="session-pass-1",
            prefer_connector=True,
            system_prompt="test",
        )

        self.assertEqual(result["meta"]["runtime"], "anthropic_agent_sdk")
        self.assertFalse(result["meta"]["fallback_used"])
        self.assertIsNone(result["meta"]["fallback_reason"])
        self.assertIn("agent_sdk", result["meta"]["debug"])
        self.assertEqual(len(result["meta"]["debug"]["agent_sdk"]["visualizations"]), 1)
        self.assertIn("skills", result["meta"]["debug"])
        self.assertEqual(sdk.calls, 1)
        self.assertEqual(sdk.last_session_id, "session-pass-1")
        self.assertEqual(connector.calls, 0)
        self.assertEqual(deterministic.calls, 0)

    def test_connector_runtime_used_when_sdk_fails(self) -> None:
        sdk = FakeAgentSDKRuntime(should_fail=True)
        connector = FakeConnectorRuntime(should_fail=False)
        deterministic = FakeDeterministicRuntime()
        runtime = AgentRuntime(
            agent_sdk_runtime=sdk,
            connector_runtime=connector,
            deterministic_runtime=deterministic,
        )

        result = runtime.run(
            message="list tools",
            servers=self.servers,
            history=[],
            session_id="session-pass-2",
            prefer_connector=True,
            system_prompt="test",
        )

        self.assertEqual(result["meta"]["runtime"], "anthropic_mcp_connector")
        self.assertTrue(result["meta"]["fallback_used"])
        self.assertEqual(result["meta"]["fallback_reason"]["code"], "agent_sdk_unavailable")
        self.assertEqual(sdk.calls, 1)
        self.assertEqual(connector.calls, 1)
        self.assertEqual(deterministic.calls, 0)

    def test_skill_context_appends_system_prompt_and_debug_ids(self) -> None:
        sdk = FakeAgentSDKRuntime(should_fail=False)
        connector = FakeConnectorRuntime(should_fail=False)
        deterministic = FakeDeterministicRuntime()
        runtime = AgentRuntime(
            agent_sdk_runtime=sdk,
            connector_runtime=connector,
            deterministic_runtime=deterministic,
        )

        result = runtime.run(
            message="search datasets",
            servers=self.servers,
            history=[],
            session_id="session-pass-3",
            prefer_connector=True,
            system_prompt="base prompt",
            skill_context={
                "selected_skill_ids": ["dataset_discovery"],
                "selected_skill_titles": ["Dataset Discovery"],
                "allowed_tool_patterns": ["ckan__search_datasets"],
                "system_prompt_addendum": "skill guidance line",
            },
        )

        self.assertEqual(result["meta"]["runtime"], "anthropic_agent_sdk")
        self.assertIn("skill guidance line", sdk.last_system_prompt)
        self.assertEqual(
            result["meta"]["debug"]["skills"]["selected_skill_ids"],
            ["dataset_discovery"],
        )

    def test_fallback_runtime_used_when_connector_fails(self) -> None:
        sdk = FakeAgentSDKRuntime(should_fail=True)
        connector = FakeConnectorRuntime(should_fail=True)
        deterministic = FakeDeterministicRuntime()
        runtime = AgentRuntime(
            agent_sdk_runtime=sdk,
            connector_runtime=connector,
            deterministic_runtime=deterministic,
        )

        result = runtime.run(
            message="search datasets",
            servers=self.servers,
            history=[],
            session_id="session-pass-4",
            prefer_connector=True,
            system_prompt="test",
        )

        self.assertEqual(result["meta"]["runtime"], "deterministic_mcp_fallback")
        self.assertTrue(result["meta"]["fallback_used"])
        self.assertEqual(result["meta"]["fallback_reason"]["code"], "agent_sdk_unavailable")
        self.assertEqual(result["meta"]["debug"]["errors"][0]["code"], "agent_sdk_unavailable")
        self.assertEqual(result["meta"]["debug"]["errors"][1]["code"], "network_error")
        self.assertEqual(sdk.calls, 1)
        self.assertEqual(connector.calls, 1)
        self.assertEqual(deterministic.calls, 1)

    def test_orchestrator_includes_runtime_debug_metadata(self) -> None:
        registry = StubRegistry(self.servers)
        orchestrator = AgentOrchestrator(registry=registry)
        sdk = FakeAgentSDKRuntime(should_fail=True)
        connector = FakeConnectorRuntime(should_fail=True)
        deterministic = FakeDeterministicRuntime()
        orchestrator.runtime = AgentRuntime(
            agent_sdk_runtime=sdk,
            connector_runtime=connector,
            deterministic_runtime=deterministic,
        )

        result = orchestrator.run_turn(
            message="run diagnostic",
            session_id="session-123",
            prefer_connector=True,
            runtime_preference="",
        )

        self.assertEqual(result["session_id"], "session-123")
        self.assertEqual(result["meta"]["runtime"], "deterministic_mcp_fallback")
        self.assertTrue(result["meta"]["fallback_used"])
        self.assertEqual(result["meta"]["fallback_reason"]["code"], "agent_sdk_unavailable")
        self.assertEqual(result["meta"]["server_count"], 1)
        self.assertGreaterEqual(result["meta"]["history_size"], 2)
        self.assertIn("skills", result["meta"]["debug"])
        self.assertIsInstance(result["meta"]["debug"]["skills"].get("selected_skill_ids"), list)


if __name__ == "__main__":
    unittest.main()
