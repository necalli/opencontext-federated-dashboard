import asyncio
import os
import unittest
from unittest.mock import patch

from services.anthropic_agent_sdk_runtime import AnthropicAgentSDKRuntime
from services.server_registry import NotFoundError


class _FakeRegistry:
    def __init__(self) -> None:
        self.rows = []

    def list_servers(self):
        output = []
        for row in self.rows:
            item = dict(row)
            headers = item.get("headers") if isinstance(item.get("headers"), dict) else {}
            item["header_keys"] = sorted([str(key) for key in headers.keys()])
            output.append(item)
        return output

    def list_servers_internal(self):
        return [dict(row) for row in self.rows]

    def create_server(self, payload):
        row = {
            "id": f"srv-{len(self.rows) + 1}",
            "name": str(payload.get("name") or "").strip(),
            "endpoint": str(payload.get("endpoint") or "").strip(),
            "description": str(payload.get("description") or "").strip(),
            "enabled": bool(payload.get("enabled", True)),
            "headers": payload.get("headers") if isinstance(payload.get("headers"), dict) else {},
        }
        self.rows.append(row)
        return self.get_server(row["id"])

    def update_server(self, server_id, payload):
        for idx, row in enumerate(self.rows):
            if str(row.get("id") or "") != str(server_id):
                continue
            row["name"] = str(payload.get("name", row.get("name")) or "").strip()
            row["endpoint"] = str(payload.get("endpoint", row.get("endpoint")) or "").strip()
            row["description"] = str(payload.get("description", row.get("description")) or "").strip()
            row["enabled"] = bool(payload.get("enabled", row.get("enabled", True)))
            if "headers" in payload and isinstance(payload.get("headers"), dict):
                row["headers"] = payload.get("headers")
            self.rows[idx] = row
            return self.get_server(server_id)
        raise NotFoundError("missing")

    def get_server(self, server_id):
        for row in self.rows:
            if str(row.get("id") or "") == str(server_id):
                item = dict(row)
                headers = item.get("headers") if isinstance(item.get("headers"), dict) else {}
                item["header_keys"] = sorted([str(key) for key in headers.keys()])
                return item
        raise NotFoundError("missing")

    def get_server_internal(self, server_id):
        for row in self.rows:
            if str(row.get("id") or "") == str(server_id):
                return dict(row)
        raise NotFoundError("missing")

    def test_connection(self, server_id):
        row = self.get_server_internal(server_id)
        ok = str(row.get("endpoint") or "").strip().startswith("http")
        return {
            "ok": ok,
            "stage": "complete" if ok else "initialize",
            "tool_count": 3 if ok else 0,
            "checks": [],
        }


class _FakeToolRouter:
    class _Catalog:
        def __init__(self, tools, errors):
            self.tools = tools
            self.errors = errors

    def build_catalog(self, servers):
        tools = []
        for row in servers:
            tools.append(
                {
                    "server_id": str(row.get("id") or "").strip(),
                    "server_name": str(row.get("name") or "").strip(),
                    "name": "get_data",
                    "description": "Data access",
                    "input_schema": {"type": "object"},
                }
            )
        return self._Catalog(tools, [])


class _FakeStorage:
    def __init__(self, initial_map=None) -> None:
        self.map = dict(initial_map or {})
        self.saved = []

    def get_agent_sdk_session_map(self):
        return dict(self.map)

    def save_agent_sdk_session_map(self, session_map):
        self.map = dict(session_map or {})
        self.saved.append(dict(self.map))


class AgentSDKRuntimeOnboardingTests(unittest.TestCase):
    def test_onboarding_tool_names_allowed_when_enabled(self) -> None:
        with patch.dict(os.environ, {"AGENT_SDK_MCP_ONBOARDING_ENABLED": "true"}, clear=False):
            runtime = AnthropicAgentSDKRuntime(server_registry=_FakeRegistry(), tool_router=_FakeToolRouter())
            names = runtime._allowed_onboarding_tool_names([])
        self.assertIn("mcp_server_discover", names)
        self.assertIn("mcp_server_onboard", names)
        self.assertIn("mcp_server_upsert", names)
        self.assertIn("mcp_stdio_bridge_plan", names)
        self.assertIn("mcp_stdio_bridge_start", names)
        self.assertIn("mcp_stdio_bridge_status", names)
        self.assertIn("mcp_stdio_bridge_stop", names)

    def test_onboarding_tool_names_can_be_filtered(self) -> None:
        with patch.dict(os.environ, {"AGENT_SDK_MCP_ONBOARDING_ENABLED": "true"}, clear=False):
            runtime = AnthropicAgentSDKRuntime(server_registry=_FakeRegistry(), tool_router=_FakeToolRouter())
            names = runtime._allowed_onboarding_tool_names(["mcp_server_onboard"])
        self.assertEqual(names, ["mcp_server_onboard"])

    def test_upsert_then_test_then_catalog_succeeds(self) -> None:
        with patch.dict(os.environ, {"AGENT_SDK_MCP_ONBOARDING_ENABLED": "true"}, clear=False):
            registry = _FakeRegistry()
            runtime = AnthropicAgentSDKRuntime(server_registry=registry, tool_router=_FakeToolRouter())
            upserted = runtime._upsert_server_from_payload(
                {
                    "name": "metro-opendata",
                    "endpoint": "https://metro.example.dev/mcp",
                    "description": "Metro datasets",
                    "enabled": True,
                    "headers": {},
                }
            )
            server = upserted.get("server") if isinstance(upserted.get("server"), dict) else {}
            server_id = str(server.get("id") or "").strip()
            test_result = registry.test_connection(server_id)
            catalog = runtime.tool_router.build_catalog([registry.get_server_internal(server_id)])
        self.assertEqual(upserted.get("action"), "created")
        self.assertTrue(bool(test_result.get("ok")))
        self.assertGreater(len(catalog.tools), 0)

    def test_normalize_headers_reports_missing_headers_env(self) -> None:
        with patch.dict(os.environ, {"AGENT_SDK_MCP_ONBOARDING_ENABLED": "true"}, clear=False):
            os.environ.pop("MISSING_TOKEN", None)
            runtime = AnthropicAgentSDKRuntime(server_registry=_FakeRegistry(), tool_router=_FakeToolRouter())
            headers, missing = runtime._normalize_headers(
                {},
                {"Authorization": "MISSING_TOKEN"},
            )
        self.assertEqual(headers, {})
        self.assertEqual(missing, ["MISSING_TOKEN"])

    def test_score_discovery_candidates_prefers_official_registry(self) -> None:
        with patch.dict(os.environ, {"AGENT_SDK_MCP_ONBOARDING_ENABLED": "true"}, clear=False):
            runtime = AnthropicAgentSDKRuntime(server_registry=_FakeRegistry(), tool_router=_FakeToolRouter())
            scored = runtime._score_discovery_candidates(
                query="nyc public housing",
                candidates=[
                    {
                        "source": "mcpmarket",
                        "name": "NYC Housing MCP",
                        "description": "NYC housing datasets",
                        "mcp_url": "https://community.example.dev/mcp",
                        "tags": ["nyc", "housing"],
                        "auth_requirement": "unknown",
                        "verification": {"score": 55},
                    },
                    {
                        "source": "official_registry",
                        "name": "NYC Housing MCP",
                        "description": "NYC housing datasets",
                        "mcp_url": "https://official.example.dev/mcp",
                        "tags": ["nyc", "housing"],
                        "auth_requirement": "unknown",
                        "verification": {"score": 55},
                    },
                ],
            )
        self.assertEqual(str(scored[0].get("source") or ""), "official_registry")

    def test_auth_inference_and_vetting_fields_present(self) -> None:
        with patch.dict(os.environ, {"AGENT_SDK_MCP_ONBOARDING_ENABLED": "true"}, clear=False):
            runtime = AnthropicAgentSDKRuntime(server_registry=_FakeRegistry(), tool_router=_FakeToolRouter())
            normalized = runtime._normalize_discovery_item(
                source="official_registry",
                item={
                    "name": "Context7",
                    "description": "Public docs MCP server. No API key required.",
                    "endpoint": "https://mcp.context7.com/mcp",
                    "homepage": "https://github.com/upstash/context7",
                },
            )
        self.assertEqual(str(normalized.get("auth_requirement") or ""), "no_auth_required")
        verification = normalized.get("verification") if isinstance(normalized.get("verification"), dict) else {}
        self.assertGreater(int(verification.get("score") or 0), 0)

    def test_extract_mcp_url_supports_streamable_http_transport_shape(self) -> None:
        with patch.dict(os.environ, {"AGENT_SDK_MCP_ONBOARDING_ENABLED": "true"}, clear=False):
            runtime = AnthropicAgentSDKRuntime(server_registry=_FakeRegistry(), tool_router=_FakeToolRouter())
            endpoint = runtime._extract_mcp_url(
                {
                    "name": "example-server",
                    "transports": [
                        {"type": "stdio"},
                        {"type": "streamable-http", "url": "https://example.com/mcp"},
                    ],
                }
            )
        self.assertEqual(endpoint, "https://example.com/mcp")

    def test_normalize_discovery_item_marks_stdio_only_when_no_http_endpoint(self) -> None:
        with patch.dict(os.environ, {"AGENT_SDK_MCP_ONBOARDING_ENABLED": "true"}, clear=False):
            runtime = AnthropicAgentSDKRuntime(server_registry=_FakeRegistry(), tool_router=_FakeToolRouter())
            normalized = runtime._normalize_discovery_item(
                source="official_registry",
                item={
                    "name": "duckdb-mcp",
                    "description": "DuckDB MCP server",
                    "transports": [{"type": "stdio"}],
                    "command": "npx",
                    "args": ["-y", "@motherduck/mcp-server-duckdb"],
                },
            )
        self.assertEqual(str(normalized.get("transport_mode") or ""), "stdio_only")
        launch = normalized.get("stdio_launch") if isinstance(normalized.get("stdio_launch"), dict) else {}
        self.assertEqual(str(launch.get("command") or ""), "npx")

    def test_build_stdio_bridge_plan_creates_local_endpoint_and_onboard_template(self) -> None:
        with patch.dict(os.environ, {"AGENT_SDK_MCP_ONBOARDING_ENABLED": "true"}, clear=False):
            runtime = AnthropicAgentSDKRuntime(server_registry=_FakeRegistry(), tool_router=_FakeToolRouter())
            plan = runtime._build_stdio_bridge_plan(
                server_name="duckdb-mcp",
                command="npx",
                args=["-y", "@motherduck/mcp-server-duckdb"],
                bridge_port=8330,
            )
        self.assertEqual(str(plan.get("mode") or ""), "stdio_bridge_required")
        self.assertEqual(str(plan.get("local_endpoint") or ""), "http://127.0.0.1:8330/mcp")
        payload = plan.get("onboard_payload_template") if isinstance(plan.get("onboard_payload_template"), dict) else {}
        self.assertEqual(str(payload.get("endpoint") or ""), "http://127.0.0.1:8330/mcp")

    def test_extract_official_registry_records_handles_list_and_names(self) -> None:
        with patch.dict(os.environ, {"AGENT_SDK_MCP_ONBOARDING_ENABLED": "true"}, clear=False):
            runtime = AnthropicAgentSDKRuntime(server_registry=_FakeRegistry(), tool_router=_FakeToolRouter())
            records, names = runtime._extract_official_registry_records(
                {
                    "servers": [
                        {"name": "alpha", "description": "A"},
                        "beta",
                        {"name": "gamma"},
                    ]
                }
            )
        self.assertEqual(len(records), 2)
        self.assertIn("alpha", names)
        self.assertIn("beta", names)
        self.assertIn("gamma", names)

    def test_extract_official_registry_records_handles_nested_server_object_name(self) -> None:
        with patch.dict(os.environ, {"AGENT_SDK_MCP_ONBOARDING_ENABLED": "true"}, clear=False):
            runtime = AnthropicAgentSDKRuntime(server_registry=_FakeRegistry(), tool_router=_FakeToolRouter())
            records, names = runtime._extract_official_registry_records(
                {
                    "servers": [
                        {
                            "server": {
                                "name": "@acme/example-mcp",
                                "description": "Example",
                            }
                        }
                    ]
                }
            )
        self.assertEqual(len(records), 1)
        self.assertEqual(names, ["@acme/example-mcp"])

    def test_instrumented_onboarding_tool_emits_tool_events(self) -> None:
        with patch.dict(os.environ, {"AGENT_SDK_MCP_ONBOARDING_ENABLED": "true"}, clear=False):
            runtime = AnthropicAgentSDKRuntime(server_registry=_FakeRegistry(), tool_router=_FakeToolRouter())
            tool_events = []
            raw_tool = runtime._build_mcp_servers_list_tool()
            instrumented = runtime._instrument_onboarding_tool(
                tool=raw_tool,
                tool_events=tool_events,
                event_sink=None,
            )
            result = asyncio.run(instrumented.handler({"enabled_only": False}))
        self.assertIsInstance(result, dict)
        self.assertEqual(len(tool_events), 2)
        self.assertEqual(str(tool_events[0].get("type") or ""), "mcp_tool_use")
        self.assertEqual(str(tool_events[0].get("tool_name") or ""), "mcp_servers_list")
        self.assertEqual(str(tool_events[1].get("type") or ""), "mcp_tool_result")
        self.assertEqual(str(tool_events[1].get("tool_name") or ""), "mcp_servers_list")

    def test_extract_mcp_init_status_from_system_event_dict(self) -> None:
        with patch.dict(os.environ, {"AGENT_SDK_MCP_ONBOARDING_ENABLED": "true"}, clear=False):
            runtime = AnthropicAgentSDKRuntime(server_registry=_FakeRegistry(), tool_router=_FakeToolRouter())
            rows = runtime._extract_mcp_init_status(
                {
                    "type": "system",
                    "subtype": "init",
                    "mcp_servers": [
                        {"name": "opencontext-main", "status": "connected"},
                        {"name": "nys-opengov", "status": "failed", "error": {"message": "timeout"}},
                    ],
                }
            )
        self.assertEqual(len(rows), 2)
        self.assertEqual(str(rows[0].get("name") or ""), "opencontext-main")
        self.assertEqual(str(rows[1].get("error") or ""), "timeout")

    def test_extract_mcp_init_status_ignores_non_system_event(self) -> None:
        with patch.dict(os.environ, {"AGENT_SDK_MCP_ONBOARDING_ENABLED": "true"}, clear=False):
            runtime = AnthropicAgentSDKRuntime(server_registry=_FakeRegistry(), tool_router=_FakeToolRouter())
            rows = runtime._extract_mcp_init_status(
                {
                    "type": "assistant",
                    "subtype": "message",
                    "mcp_servers": [{"name": "opencontext-main", "status": "connected"}],
                }
            )
        self.assertEqual(rows, [])

    def test_session_map_loads_from_storage_when_enabled(self) -> None:
        with patch.dict(
            os.environ,
            {
                "AGENT_SDK_MCP_ONBOARDING_ENABLED": "true",
                "AGENT_SDK_SESSION_MAP_PERSIST_ENABLED": "true",
            },
            clear=False,
        ):
            storage = _FakeStorage({"app-1": "sdk-1"})
            runtime = AnthropicAgentSDKRuntime(
                server_registry=_FakeRegistry(),
                tool_router=_FakeToolRouter(),
                storage=storage,
            )
        self.assertEqual(runtime._session_map.get("app-1"), "sdk-1")

    def test_session_map_persists_updates_and_prunes_by_cap(self) -> None:
        with patch.dict(
            os.environ,
            {
                "AGENT_SDK_MCP_ONBOARDING_ENABLED": "true",
                "AGENT_SDK_SESSION_MAP_PERSIST_ENABLED": "true",
                "AGENT_SDK_SESSION_MAP_MAX": "2",
            },
            clear=False,
        ):
            storage = _FakeStorage()
            runtime = AnthropicAgentSDKRuntime(
                server_registry=_FakeRegistry(),
                tool_router=_FakeToolRouter(),
                storage=storage,
            )
            runtime._set_sdk_session_mapping(app_session_id="app-1", sdk_session_id="sdk-1")
            runtime._set_sdk_session_mapping(app_session_id="app-2", sdk_session_id="sdk-2")
            runtime._set_sdk_session_mapping(app_session_id="app-3", sdk_session_id="sdk-3")
        self.assertNotIn("app-1", runtime._session_map)
        self.assertEqual(runtime._session_map.get("app-2"), "sdk-2")
        self.assertEqual(runtime._session_map.get("app-3"), "sdk-3")
        self.assertGreaterEqual(len(storage.saved), 1)
        self.assertEqual(storage.saved[-1], runtime._session_map)

    def test_session_map_persistence_can_be_disabled(self) -> None:
        with patch.dict(
            os.environ,
            {
                "AGENT_SDK_MCP_ONBOARDING_ENABLED": "true",
                "AGENT_SDK_SESSION_MAP_PERSIST_ENABLED": "false",
            },
            clear=False,
        ):
            storage = _FakeStorage({"app-1": "sdk-1"})
            runtime = AnthropicAgentSDKRuntime(
                server_registry=_FakeRegistry(),
                tool_router=_FakeToolRouter(),
                storage=storage,
            )
            runtime._set_sdk_session_mapping(app_session_id="app-2", sdk_session_id="sdk-2")
        self.assertEqual(runtime._session_map.get("app-1"), None)
        self.assertEqual(runtime._session_map.get("app-2"), "sdk-2")
        self.assertEqual(storage.saved, [])


if __name__ == "__main__":
    unittest.main()
