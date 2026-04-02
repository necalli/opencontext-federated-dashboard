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


class AgentSDKRuntimeOnboardingTests(unittest.TestCase):
    def test_onboarding_tool_names_allowed_when_enabled(self) -> None:
        with patch.dict(os.environ, {"AGENT_SDK_MCP_ONBOARDING_ENABLED": "true"}, clear=False):
            runtime = AnthropicAgentSDKRuntime(server_registry=_FakeRegistry(), tool_router=_FakeToolRouter())
            names = runtime._allowed_onboarding_tool_names([])
        self.assertIn("mcp_server_discover", names)
        self.assertIn("mcp_server_onboard", names)
        self.assertIn("mcp_server_upsert", names)

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
                        "source": "mcp_so",
                        "name": "NYC Housing MCP",
                        "description": "NYC housing datasets",
                        "mcp_url": "https://community.example.dev/mcp",
                        "tags": ["nyc", "housing"],
                    },
                    {
                        "source": "official_registry",
                        "name": "NYC Housing MCP",
                        "description": "NYC housing datasets",
                        "mcp_url": "https://official.example.dev/mcp",
                        "tags": ["nyc", "housing"],
                    },
                ],
            )
        self.assertEqual(str(scored[0].get("source") or ""), "official_registry")


if __name__ == "__main__":
    unittest.main()
