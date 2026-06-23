import unittest

from services.opencontext_mcp_client import MCPClientError

try:
    import app as app_module
except ModuleNotFoundError:
    app_module = None


class _Result:
    def __init__(self, method: str, result, latency_ms: int = 12) -> None:
        self.method = method
        self.result = result
        self.latency_ms = latency_ms


class FakeRegistry:
    def __init__(self) -> None:
        self._servers = [
            {
                "id": "srv-1",
                "name": "opencontext-main",
                "endpoint": "https://example-main.test/mcp",
                "description": "main",
                "enabled": True,
                "headers": {},
            },
            {
                "id": "srv-2",
                "name": "opencontext-aux",
                "endpoint": "https://example-aux.test/mcp",
                "description": "aux",
                "enabled": True,
                "headers": {},
            },
        ]

    def list_servers(self):
        return list(self._servers)

    def get_server(self, server_id: str):
        for row in self._servers:
            if row.get("id") == server_id:
                return dict(row)
        raise app_module.NotFoundError("missing server")


class FakeOpenContextClient:
    fail_endpoints = set()

    def __init__(self, endpoint: str, *, headers=None, **kwargs) -> None:
        self.endpoint = endpoint
        self.headers = headers or {}

    def initialize(self):
        if self.endpoint in self.fail_endpoints:
            raise MCPClientError(
                "network_error",
                "simulated failure",
                method="initialize",
                endpoint=self.endpoint,
                retriable=True,
            )
        return _Result(
            "initialize",
            {"serverInfo": {"name": "opencontext", "version": "1.0.0"}},
            latency_ms=11,
        )

    def tools_list(self):
        if self.endpoint in self.fail_endpoints:
            raise MCPClientError(
                "network_error",
                "simulated failure",
                method="tools/list",
                endpoint=self.endpoint,
                retriable=True,
            )
        if "aux" in self.endpoint:
            tools = [
                {"name": "aux__list", "description": "Aux list", "inputSchema": {"type": "object"}},
                {
                    "name": "ckan__search_datasets",
                    "description": "Search (aux)",
                    "inputSchema": {"type": "object"},
                },
                {"name": "ckan__execute_sql", "description": "SQL", "inputSchema": {"type": "object"}},
            ]
        else:
            tools = [
                {"name": "ckan__search_datasets", "description": "Search", "inputSchema": {"type": "object"}},
                {"name": "ckan__get_schema", "description": "Schema", "inputSchema": {"type": "object"}},
                {"name": "ckan__execute_sql", "description": "SQL", "inputSchema": {"type": "object"}},
            ]
        return _Result("tools/list", {"tools": tools}, latency_ms=15)

    def tools_call(self, name: str, arguments):
        if self.endpoint in self.fail_endpoints:
            raise MCPClientError(
                "http_error",
                "simulated call failure",
                method="tools/call",
                endpoint=self.endpoint,
                status_code=502,
                retriable=True,
            )
        return _Result(
            "tools/call",
            {
                "content": [
                    {
                        "type": "text",
                        "text": f"called {name}",
                    }
                ],
                "echo": arguments,
            },
            latency_ms=33,
        )


@unittest.skipIf(app_module is None, "Flask runtime not available in this environment")
class ToolApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_registry = app_module.server_registry
        self.original_client = app_module.OpenContextMCPClient
        app_module.server_registry = FakeRegistry()
        app_module.OpenContextMCPClient = FakeOpenContextClient
        FakeOpenContextClient.fail_endpoints = set()
        self.client = app_module.create_app().test_client()

    def tearDown(self) -> None:
        app_module.server_registry = self.original_registry
        app_module.OpenContextMCPClient = self.original_client

    def test_tools_list_can_filter_by_server(self) -> None:
        response = self.client.get("/api/v1/mcp/tools/list?server_id=srv-1")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["tool_count"], 3)
        self.assertEqual(len(payload["tools"]), 3)
        self.assertTrue(all(row["server_id"] == "srv-1" for row in payload["tools"]))
        self.assertEqual(payload["filters"]["server_id"], "srv-1")

    def test_tools_call_returns_structured_result(self) -> None:
        response = self.client.post(
            "/api/v1/mcp/tools/call",
            json={
                "server_id": "srv-1",
                "tool_name": "ckan__search_datasets",
                "arguments": {"query": "safety", "limit": 3},
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["server"]["id"], "srv-1")
        self.assertEqual(payload["tool_name"], "ckan__search_datasets")
        self.assertIn("result", payload)
        self.assertIn("latency_ms", payload)
        self.assertIn("tools_call", payload["latency_ms"])
        self.assertEqual(payload["routing"]["mode"], "explicit_server")

    def test_tools_call_can_route_without_server_id(self) -> None:
        response = self.client.post(
            "/api/v1/mcp/tools/call",
            json={
                "tool_name": "ckan__search_datasets",
                "arguments": {"query": "finance", "limit": 2},
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["tool_name"], "ckan__search_datasets")
        self.assertEqual(payload["routing"]["mode"], "capability_match")

    def test_tools_call_can_failover_to_secondary_server(self) -> None:
        FakeOpenContextClient.fail_endpoints = {"https://example-main.test/mcp"}
        response = self.client.post(
            "/api/v1/mcp/tools/call",
            json={
                "tool_name": "ckan__search_datasets",
                "arguments": {"query": "safety", "limit": 1},
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["server"]["id"], "srv-2")
        self.assertEqual(payload["routing"]["mode"], "capability_match")

    def test_tools_call_validation_error_for_non_object_arguments(self) -> None:
        response = self.client.post(
            "/api/v1/mcp/tools/call",
            json={
                "server_id": "srv-1",
                "tool_name": "ckan__search_datasets",
                "arguments": ["bad", "shape"],
            },
        )
        self.assertEqual(response.status_code, 400)
        payload = response.get_json()
        self.assertEqual(payload["error"]["code"], "validation_error")

    def test_tools_list_surfaces_actionable_errors(self) -> None:
        FakeOpenContextClient.fail_endpoints = {"https://example-main.test/mcp", "https://example-aux.test/mcp"}
        response = self.client.get("/api/v1/mcp/tools/list")
        self.assertEqual(response.status_code, 502)
        payload = response.get_json()
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["tool_count"], 0)
        self.assertGreaterEqual(len(payload["errors"]), 1)
        self.assertEqual(payload["errors"][0]["error"]["code"], "network_error")

    def test_tools_call_returns_actionable_error_when_tool_unavailable(self) -> None:
        response = self.client.post(
            "/api/v1/mcp/tools/call",
            json={
                "tool_name": "does_not_exist",
                "arguments": {},
            },
        )
        self.assertEqual(response.status_code, 404)
        payload = response.get_json()
        self.assertEqual(payload["error"]["code"], "tool_not_routable")

    def test_execute_sql_requires_advanced_mode(self) -> None:
        response = self.client.post(
            "/api/v1/mcp/tools/call",
            json={
                "tool_name": "ckan__execute_sql",
                "arguments": {"query": "SELECT * FROM table_x LIMIT 10"},
            },
        )
        self.assertEqual(response.status_code, 400)
        payload = response.get_json()
        self.assertEqual(payload["error"]["code"], "advanced_mode_required")

    def test_execute_sql_rejects_missing_limit(self) -> None:
        response = self.client.post(
            "/api/v1/mcp/tools/call",
            json={
                "tool_name": "ckan__execute_sql",
                "advanced_mode": True,
                "arguments": {"query": "SELECT * FROM table_x"},
            },
        )
        self.assertEqual(response.status_code, 400)
        payload = response.get_json()
        self.assertEqual(payload["error"]["code"], "sql_limit_required")


if __name__ == "__main__":
    unittest.main()
