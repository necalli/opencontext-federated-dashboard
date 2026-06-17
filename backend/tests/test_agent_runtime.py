import unittest
from typing import Any, Dict, List

from services.agent_orchestrator import AgentOrchestrator
from services.agent_runtime import AgentRuntime
from services.anthropic_agent_sdk_runtime import AgentSDKRuntimeError
from services.anthropic_mcp_connector import AnthropicConnectorError
from services.skill_packages import SkillPackage


class FakeAgentSDKRuntime:
    def __init__(self, should_fail: bool = False) -> None:
        self.should_fail = should_fail
        self.calls = 0
        self.last_system_prompt = ""
        self.last_session_id = ""
        self.last_skill_context: Dict[str, Any] = {}

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
        self.last_skill_context = dict(skill_context or {})
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


class StubSkillRegistry:
    def __init__(self, contexts: List[Dict[str, Any]], packages: List[SkillPackage] | None = None) -> None:
        self._contexts = list(contexts)
        self.packages = tuple(packages or [])
        self.calls = 0

    def refresh(self) -> None:
        return None

    def resolve_for_message(self, message: str, *, max_skills: int = 3) -> Dict[str, Any]:
        index = min(self.calls, max(0, len(self._contexts) - 1))
        self.calls += 1
        return dict(self._contexts[index]) if self._contexts else {
            "selected_skill_ids": [],
            "selected_skill_titles": [],
            "selected_skills": [],
            "allowed_tool_patterns": [],
            "allowed_tool_names": [],
            "system_prompt_addendum": "",
        }


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
        registry = StubRegistry(
            self.servers
            + [
                {
                    "id": "srv-2",
                    "name": "disabled-server",
                    "endpoint": "https://example.org/mcp",
                    "enabled": False,
                    "headers": {},
                }
            ]
        )
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
        self.assertEqual(result["meta"]["enabled_server_count"], 1)
        self.assertEqual(result["meta"]["total_server_count"], 2)
        self.assertGreaterEqual(result["meta"]["history_size"], 2)
        self.assertIn("skills", result["meta"]["debug"])
        self.assertEqual(result["meta"]["debug"]["enabled_server_count"], 1)
        self.assertEqual(result["meta"]["debug"]["total_server_count"], 2)
        self.assertIsInstance(result["meta"]["debug"]["skills"].get("selected_skill_ids"), list)

    def test_default_system_prompt_is_not_hardwired_to_onboarding_mode(self) -> None:
        registry = StubRegistry(self.servers)
        orchestrator = AgentOrchestrator(registry=registry)
        self.assertNotIn("execute the MCP onboarding flow", orchestrator.default_system_prompt.lower())

    def test_onboarding_scope_sticky_keeps_tools_for_followup_turn(self) -> None:
        registry = StubRegistry(self.servers)
        orchestrator = AgentOrchestrator(registry=registry)
        orchestrator.onboarding_scope_sticky_turns = 2
        onboarding_package = SkillPackage(
            skill_id="mcp-server-onboarder",
            title="mcp-server-onboarder",
            description="Onboarding workflow",
            instruction="Use onboarding tools",
            trigger_keywords=("onboard",),
            allowed_tool_patterns=("mcp_server_discover", "mcp_server_onboard", "mcp_servers_list"),
            always_on=False,
            enabled=True,
            path="/tmp/skill.md",
        )
        orchestrator.skill_registry = StubSkillRegistry(
            contexts=[
                {
                    "selected_skill_ids": ["mcp-server-onboarder"],
                    "selected_skill_titles": ["mcp-server-onboarder"],
                    "selected_skills": [],
                    "allowed_tool_patterns": ["mcp_server_discover"],
                    "allowed_tool_names": ["mcp_server_discover"],
                    "system_prompt_addendum": "",
                },
                {
                    "selected_skill_ids": [],
                    "selected_skill_titles": [],
                    "selected_skills": [],
                    "allowed_tool_patterns": [],
                    "allowed_tool_names": [],
                    "system_prompt_addendum": "",
                },
            ],
            packages=[onboarding_package],
        )
        sdk = FakeAgentSDKRuntime(should_fail=False)
        connector = FakeConnectorRuntime(should_fail=False)
        deterministic = FakeDeterministicRuntime()
        orchestrator.runtime = AgentRuntime(
            agent_sdk_runtime=sdk,
            connector_runtime=connector,
            deterministic_runtime=deterministic,
        )

        orchestrator.run_turn(
            message="add mcp server",
            session_id="sticky-1",
            prefer_connector=True,
            runtime_preference="",
        )
        second = orchestrator.run_turn(
            message="try another term",
            session_id="sticky-1",
            prefer_connector=True,
            runtime_preference="",
        )

        selected_ids = second["meta"]["debug"]["skills"]["selected_skill_ids"]
        allowed = second["meta"]["debug"]["skills"]["allowed_tool_patterns"]
        self.assertIn("mcp-server-onboarder", selected_ids)
        self.assertIn("mcp_server_discover", allowed)
        self.assertIn("mcp_server_onboard", allowed)

    def test_onboarding_scope_survives_non_followup_interstitial_turn(self) -> None:
        registry = StubRegistry(self.servers)
        orchestrator = AgentOrchestrator(registry=registry)
        orchestrator.onboarding_scope_sticky_turns = 2
        orchestrator.onboarding_scope_strict_continuity = True
        onboarding_package = SkillPackage(
            skill_id="mcp-server-onboarder",
            title="mcp-server-onboarder",
            description="Onboarding workflow",
            instruction="Use onboarding tools",
            trigger_keywords=("onboard",),
            allowed_tool_patterns=("mcp_server_discover", "mcp_server_onboard", "mcp_servers_list"),
            always_on=False,
            enabled=True,
            path="/tmp/skill.md",
        )
        orchestrator.skill_registry = StubSkillRegistry(
            contexts=[
                {
                    "selected_skill_ids": ["mcp-server-onboarder"],
                    "selected_skill_titles": ["mcp-server-onboarder"],
                    "selected_skills": [],
                    "allowed_tool_patterns": ["mcp_server_discover"],
                    "allowed_tool_names": ["mcp_server_discover"],
                    "system_prompt_addendum": "",
                },
                {
                    "selected_skill_ids": [],
                    "selected_skill_titles": [],
                    "selected_skills": [],
                    "allowed_tool_patterns": [],
                    "allowed_tool_names": [],
                    "system_prompt_addendum": "",
                },
                {
                    "selected_skill_ids": [],
                    "selected_skill_titles": [],
                    "selected_skills": [],
                    "allowed_tool_patterns": [],
                    "allowed_tool_names": [],
                    "system_prompt_addendum": "",
                },
            ],
            packages=[onboarding_package],
        )
        sdk = FakeAgentSDKRuntime(should_fail=False)
        connector = FakeConnectorRuntime(should_fail=False)
        deterministic = FakeDeterministicRuntime()
        orchestrator.runtime = AgentRuntime(
            agent_sdk_runtime=sdk,
            connector_runtime=connector,
            deterministic_runtime=deterministic,
        )

        orchestrator.run_turn(
            message="add mcp server",
            session_id="sticky-3",
            prefer_connector=True,
            runtime_preference="",
        )
        orchestrator.run_turn(
            message="what searches did you use?",
            session_id="sticky-3",
            prefer_connector=True,
            runtime_preference="",
        )
        third = orchestrator.run_turn(
            message="let's try another server",
            session_id="sticky-3",
            prefer_connector=True,
            runtime_preference="",
        )

        selected_ids = third["meta"]["debug"]["skills"]["selected_skill_ids"]
        allowed = third["meta"]["debug"]["skills"]["allowed_tool_patterns"]
        self.assertIn("mcp-server-onboarder", selected_ids)
        self.assertIn("mcp_server_discover", allowed)

    def test_onboarding_scope_does_not_hijack_plain_rental_search(self) -> None:
        registry = StubRegistry(self.servers)
        orchestrator = AgentOrchestrator(registry=registry)
        orchestrator.onboarding_scope_sticky_turns = 3
        onboarding_package = SkillPackage(
            skill_id="mcp-server-onboarder",
            title="mcp-server-onboarder",
            description="Onboarding workflow",
            instruction="Use onboarding tools",
            trigger_keywords=("onboard",),
            allowed_tool_patterns=("mcp_server_discover", "mcp_server_onboard", "mcp_servers_list"),
            always_on=False,
            enabled=True,
            path="/tmp/skill.md",
        )
        rental_package = SkillPackage(
            skill_id="rental_dashboard_ops",
            title="Rental Dashboard MCP Operations",
            description="Rental workflow",
            instruction="Use rental tools",
            trigger_keywords=("rental", "airbnb", "listing"),
            allowed_tool_patterns=("search_airbnb_listings", "get_job", "get_search_listings"),
            always_on=False,
            enabled=True,
            path="/tmp/rental.md",
        )
        orchestrator.skill_registry = StubSkillRegistry(
            contexts=[
                {
                    "selected_skill_ids": ["mcp-server-onboarder"],
                    "selected_skill_titles": ["mcp-server-onboarder"],
                    "selected_skills": [],
                    "allowed_tool_patterns": ["mcp_server_discover"],
                    "allowed_tool_names": ["mcp_server_discover"],
                    "system_prompt_addendum": "",
                },
                {
                    "selected_skill_ids": ["rental_dashboard_ops"],
                    "selected_skill_titles": ["Rental Dashboard MCP Operations"],
                    "selected_skills": [
                        {
                            "skill_id": "rental_dashboard_ops",
                            "title": "Rental Dashboard MCP Operations",
                            "description": "Rental workflow",
                            "instruction": "Use rental tools",
                            "tools": ["search_airbnb_listings", "get_job", "get_search_listings"],
                            "path": "/tmp/rental.md",
                        }
                    ],
                    "allowed_tool_patterns": ["search_airbnb_listings", "get_job", "get_search_listings"],
                    "allowed_tool_names": ["search_airbnb_listings", "get_job", "get_search_listings"],
                    "system_prompt_addendum": "",
                },
            ],
            packages=[onboarding_package, rental_package],
        )
        sdk = FakeAgentSDKRuntime(should_fail=False)
        connector = FakeConnectorRuntime(should_fail=False)
        deterministic = FakeDeterministicRuntime()
        orchestrator.runtime = AgentRuntime(
            agent_sdk_runtime=sdk,
            connector_runtime=connector,
            deterministic_runtime=deterministic,
        )

        orchestrator.run_turn(
            message="add mcp server",
            session_id="sticky-rental",
            prefer_connector=True,
            runtime_preference="",
        )
        second = orchestrator.run_turn(
            message="search rental listings in the Adirondacks NY for 2 people",
            session_id="sticky-rental",
            prefer_connector=True,
            runtime_preference="",
        )

        selected_ids = second["meta"]["debug"]["skills"]["selected_skill_ids"]
        allowed = second["meta"]["debug"]["skills"]["allowed_tool_patterns"]
        self.assertIn("rental_dashboard_ops", selected_ids)
        self.assertNotIn("mcp-server-onboarder", selected_ids)
        self.assertIn("search_airbnb_listings", allowed)
        self.assertNotIn("mcp_server_discover", allowed)

    def test_onboarding_scope_sticky_can_be_canceled(self) -> None:
        registry = StubRegistry(self.servers)
        orchestrator = AgentOrchestrator(registry=registry)
        orchestrator.onboarding_scope_sticky_turns = 3
        onboarding_package = SkillPackage(
            skill_id="mcp-server-onboarder",
            title="mcp-server-onboarder",
            description="Onboarding workflow",
            instruction="Use onboarding tools",
            trigger_keywords=("onboard",),
            allowed_tool_patterns=("mcp_server_discover", "mcp_server_onboard", "mcp_servers_list"),
            always_on=False,
            enabled=True,
            path="/tmp/skill.md",
        )
        orchestrator.skill_registry = StubSkillRegistry(
            contexts=[
                {
                    "selected_skill_ids": ["mcp-server-onboarder"],
                    "selected_skill_titles": ["mcp-server-onboarder"],
                    "selected_skills": [],
                    "allowed_tool_patterns": ["mcp_server_discover"],
                    "allowed_tool_names": ["mcp_server_discover"],
                    "system_prompt_addendum": "",
                },
                {
                    "selected_skill_ids": [],
                    "selected_skill_titles": [],
                    "selected_skills": [],
                    "allowed_tool_patterns": [],
                    "allowed_tool_names": [],
                    "system_prompt_addendum": "",
                },
                {
                    "selected_skill_ids": [],
                    "selected_skill_titles": [],
                    "selected_skills": [],
                    "allowed_tool_patterns": [],
                    "allowed_tool_names": [],
                    "system_prompt_addendum": "",
                },
            ],
            packages=[onboarding_package],
        )
        sdk = FakeAgentSDKRuntime(should_fail=False)
        connector = FakeConnectorRuntime(should_fail=False)
        deterministic = FakeDeterministicRuntime()
        orchestrator.runtime = AgentRuntime(
            agent_sdk_runtime=sdk,
            connector_runtime=connector,
            deterministic_runtime=deterministic,
        )

        orchestrator.run_turn(
            message="add mcp server",
            session_id="sticky-2",
            prefer_connector=True,
            runtime_preference="",
        )
        cancel = orchestrator.run_turn(
            message="stop onboarding",
            session_id="sticky-2",
            prefer_connector=True,
            runtime_preference="",
        )
        after = orchestrator.run_turn(
            message="run normal query",
            session_id="sticky-2",
            prefer_connector=True,
            runtime_preference="",
        )

        self.assertNotIn("mcp-server-onboarder", cancel["meta"]["debug"]["skills"]["selected_skill_ids"])
        self.assertNotIn("mcp_server_discover", after["meta"]["debug"]["skills"]["allowed_tool_patterns"])

    def test_onboarding_connectivity_claim_guard_requires_current_turn_evidence(self) -> None:
        class ClaimingSDKRuntime(FakeAgentSDKRuntime):
            def generate(self, **kwargs):  # type: ignore[override]
                return {
                    "text": "All onboarding tools are disconnected right now.",
                    "response_id": "sdk_claim_test",
                    "stop_reason": "end_turn",
                    "usage": {},
                    "server_names": ["boston-open-data"],
                    "tool_events": [],
                    "builtin_tool_events": [],
                    "visualizations": [],
                    "mcp_init_status": [],
                    "sdk_meta": {"session_id": "sdk_claim_test"},
                }

        runtime = AgentRuntime(
            agent_sdk_runtime=ClaimingSDKRuntime(),
            connector_runtime=FakeConnectorRuntime(should_fail=False),
            deterministic_runtime=FakeDeterministicRuntime(),
        )
        result = runtime.run(
            message="What about Reddit MCPs?",
            servers=self.servers,
            history=[],
            session_id="session-claim-1",
            prefer_connector=True,
            system_prompt="test",
            skill_context={
                "selected_skill_ids": ["mcp-server-onboarder"],
                "selected_skill_titles": ["mcp-server-onboarder"],
                "allowed_tool_patterns": ["mcp_server_discover", "mcp_servers_list"],
            },
        )
        self.assertIn("cannot verify mcp connectivity", str(result.get("message") or "").lower())
        warnings = result["meta"]["debug"].get("warnings") or []
        self.assertIn("onboarding_connectivity_claim_without_evidence", warnings)

    def test_onboarding_connectivity_claim_guard_allows_init_status_evidence(self) -> None:
        class ClaimingSDKRuntime(FakeAgentSDKRuntime):
            def generate(self, **kwargs):  # type: ignore[override]
                return {
                    "text": "All onboarding tools are disconnected right now.",
                    "response_id": "sdk_claim_test2",
                    "stop_reason": "end_turn",
                    "usage": {},
                    "server_names": ["boston-open-data"],
                    "tool_events": [],
                    "builtin_tool_events": [],
                    "visualizations": [],
                    "mcp_init_status": [{"name": "opencontext-main", "status": "connected", "error": ""}],
                    "sdk_meta": {"session_id": "sdk_claim_test2"},
                }

        runtime = AgentRuntime(
            agent_sdk_runtime=ClaimingSDKRuntime(),
            connector_runtime=FakeConnectorRuntime(should_fail=False),
            deterministic_runtime=FakeDeterministicRuntime(),
        )
        result = runtime.run(
            message="What about Reddit MCPs?",
            servers=self.servers,
            history=[],
            session_id="session-claim-2",
            prefer_connector=True,
            system_prompt="test",
            skill_context={
                "selected_skill_ids": ["mcp-server-onboarder"],
                "selected_skill_titles": ["mcp-server-onboarder"],
                "allowed_tool_patterns": ["mcp_server_discover", "mcp_servers_list"],
            },
        )
        self.assertIn("disconnected", str(result.get("message") or "").lower())
        agent_debug = result["meta"]["debug"]["agent_sdk"]
        self.assertEqual(len(agent_debug.get("mcp_init_status") or []), 1)
        warnings = result["meta"]["debug"].get("warnings") or []
        self.assertNotIn("onboarding_connectivity_claim_without_evidence", warnings)


if __name__ == "__main__":
    unittest.main()
