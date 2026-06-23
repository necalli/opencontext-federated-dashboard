import os
import unittest
from unittest.mock import patch

from services.anthropic_agent_sdk_runtime import AnthropicAgentSDKRuntime


class AgentSDKRuntimeAliasingTests(unittest.TestCase):
    def _sample_tools(self):
        return [
            {
                "server_id": "srv-nyc",
                "server_name": "nyc-opengov",
                "name": "get_data",
                "description": "NYC data access",
                "input_schema": {"type": "object", "properties": {}},
            },
            {
                "server_id": "srv-nys",
                "server_name": "nys-opengov",
                "name": "get_data",
                "description": "NYS data access",
                "input_schema": {"type": "object", "properties": {}},
            },
            {
                "server_id": "srv-bos",
                "server_name": "opencontext-main",
                "name": "ckan__search_datasets",
                "description": "Boston search",
                "input_schema": {"type": "object", "properties": {}},
            },
        ]

    def _sample_servers(self):
        return [
            {"id": "srv-nyc", "name": "nyc-opengov", "endpoint": "http://127.0.0.1:8200/opengov/mcp", "enabled": True},
            {"id": "srv-nys", "name": "nys-opengov", "endpoint": "http://127.0.0.1:8200/opengov-nys/mcp", "enabled": True},
            {"id": "srv-bos", "name": "opencontext-main", "endpoint": "http://127.0.0.1:8200/opencontext/mcp", "enabled": True},
        ]

    def test_duplicate_tool_aliasing_enabled_by_default(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AGENT_SDK_DUPLICATE_TOOL_ALIAS_ENABLED", None)
            runtime = AnthropicAgentSDKRuntime()
            catalog = runtime._catalog_tools_for_sdk(self._sample_tools(), [])

        sdk_names = sorted(str(row.get("sdk_name") or "") for row in catalog)
        self.assertIn("get_data__nyc_opengov", sdk_names)
        self.assertIn("get_data__nys_opengov", sdk_names)
        self.assertIn("ckan__search_datasets", sdk_names)
        self.assertEqual(len(sdk_names), 3)

    def test_duplicate_tool_aliasing_can_be_disabled(self) -> None:
        with patch.dict(os.environ, {"AGENT_SDK_DUPLICATE_TOOL_ALIAS_ENABLED": "false"}, clear=False):
            runtime = AnthropicAgentSDKRuntime()
            catalog = runtime._catalog_tools_for_sdk(self._sample_tools(), [])

        sdk_names = sorted(str(row.get("sdk_name") or "") for row in catalog)
        self.assertEqual(sdk_names, ["ckan__search_datasets", "get_data"])
        get_data_rows = [row for row in catalog if str(row.get("internal_tool_name") or "") == "get_data"]
        self.assertEqual(len(get_data_rows), 1)

    def test_subagent_allowlisting_expands_get_data_to_both_aliases(self) -> None:
        with patch.dict(os.environ, {"AGENT_SDK_DUPLICATE_TOOL_ALIAS_ENABLED": "true"}, clear=False):
            runtime = AnthropicAgentSDKRuntime()
            catalog = runtime._catalog_tools_for_sdk(self._sample_tools(), [])
            _, _, internal_to_allowed = runtime._build_wrapped_tools(
                available_tools=catalog,
                active_servers=self._sample_servers(),
                tool_events=[],
            )

        subagents = runtime._build_subagents(
            selected_skills=[
                {
                    "skill_id": "civic_research",
                    "title": "Civic Research",
                    "description": "Research specialist",
                    "instruction": "Use civic datasets.",
                    "tools": ["get_data"],
                }
            ],
            internal_to_allowed=internal_to_allowed,
        )

        self.assertIn("civic-research", subagents)
        agent_def = subagents["civic-research"]
        tools = (
            list(getattr(agent_def, "tools", []))
            if not isinstance(agent_def, dict)
            else list(agent_def.get("tools") or [])
        )
        self.assertIn("mcp__opencontext__get_data__nyc_opengov", tools)
        self.assertIn("mcp__opencontext__get_data__nys_opengov", tools)


if __name__ == "__main__":
    unittest.main()
