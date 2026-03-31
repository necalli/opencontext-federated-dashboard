import unittest

from services.opencontext_mcp_client import MCPClientError
from services.tool_router import ToolRouter, ToolRouterError


class _Result:
    def __init__(self, result) -> None:
        self.result = result
        self.latency_ms = 10


class FakeClient:
    fail_endpoints = set()

    def __init__(self, endpoint: str, *, headers=None, **kwargs) -> None:
        self.endpoint = endpoint
        self.headers = headers or {}

    def initialize(self):
        if self.endpoint in self.fail_endpoints:
            raise MCPClientError(
                "network_error",
                "simulated init fail",
                method="initialize",
                endpoint=self.endpoint,
                retriable=True,
            )
        return _Result({"serverInfo": {"name": "fake"}})

    def tools_list(self):
        if self.endpoint in self.fail_endpoints:
            raise MCPClientError(
                "network_error",
                "simulated list fail",
                method="tools/list",
                endpoint=self.endpoint,
                retriable=True,
            )
        if "alpha" in self.endpoint:
            tools = [{"name": "ckan__search_datasets"}]
        else:
            tools = [{"name": "ckan__search_datasets"}, {"name": "ckan__aggregate_data"}]
        return _Result({"tools": tools})


class ToolRouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.router = ToolRouter(client_factory=FakeClient)
        FakeClient.fail_endpoints = set()
        self.servers = [
            {"id": "s1", "name": "alpha", "endpoint": "https://alpha.test/mcp", "enabled": True, "headers": {}},
            {"id": "s2", "name": "beta", "endpoint": "https://beta.test/mcp", "enabled": True, "headers": {}},
        ]

    def test_build_catalog_tracks_tool_server_map(self) -> None:
        catalog = self.router.build_catalog(self.servers)
        self.assertGreaterEqual(len(catalog.tools), 2)
        self.assertIn("ckan__search_datasets", catalog.tool_server_ids)
        self.assertEqual(catalog.tool_server_ids["ckan__search_datasets"], ["s1", "s2"])

    def test_route_candidates_by_tool(self) -> None:
        candidates, catalog = self.router.route_candidates(
            tool_name="ckan__aggregate_data",
            servers=self.servers,
            preferred_server_id="",
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["id"], "s2")
        self.assertEqual(len(catalog.errors), 0)

    def test_route_candidates_respects_preferred_server(self) -> None:
        candidates, _ = self.router.route_candidates(
            tool_name="ckan__search_datasets",
            servers=self.servers,
            preferred_server_id="s1",
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["id"], "s1")

    def test_route_candidates_raises_when_tool_not_routable(self) -> None:
        with self.assertRaises(ToolRouterError) as ctx:
            self.router.route_candidates(
                tool_name="unknown_tool",
                servers=self.servers,
                preferred_server_id="",
            )
        self.assertEqual(ctx.exception.code, "tool_not_routable")

    def test_catalog_surfaces_server_errors(self) -> None:
        FakeClient.fail_endpoints = {"https://alpha.test/mcp"}
        catalog = self.router.build_catalog(self.servers)
        self.assertEqual(len(catalog.errors), 1)
        self.assertEqual(catalog.errors[0]["server_id"], "s1")

    def test_validate_execute_sql_arguments_accepts_safe_limited_query(self) -> None:
        result = self.router.validate_execute_sql_arguments(
            {"query": "SELECT * FROM my_table ORDER BY created_at DESC LIMIT 250"},
            max_rows=1000,
        )
        self.assertEqual(result["limit"], 250)
        self.assertEqual(result["max_rows"], 1000)
        self.assertIn("SELECT", result["query_preview"].upper())

    def test_validate_execute_sql_arguments_requires_limit(self) -> None:
        with self.assertRaises(ToolRouterError) as ctx:
            self.router.validate_execute_sql_arguments(
                {"query": "SELECT * FROM my_table"},
                max_rows=1000,
            )
        self.assertEqual(ctx.exception.code, "sql_limit_required")

    def test_validate_execute_sql_arguments_rejects_limit_over_max(self) -> None:
        with self.assertRaises(ToolRouterError) as ctx:
            self.router.validate_execute_sql_arguments(
                {"query": "SELECT * FROM my_table LIMIT 50000"},
                max_rows=1000,
            )
        self.assertEqual(ctx.exception.code, "sql_limit_exceeded")


if __name__ == "__main__":
    unittest.main()
