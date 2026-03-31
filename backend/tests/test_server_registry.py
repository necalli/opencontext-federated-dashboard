import os
import tempfile
import unittest
from pathlib import Path

from services.server_registry import MCPConnectionError, ServerRegistryService
from services.storage import Storage


class ServerRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["MCP_READ_TIMEOUT_MS"] = "400"
        os.environ["MCP_MAX_RETRIES"] = "0"
        for key in [
            "MCP_DEFAULT_SERVER_AUTO_REGISTER",
            "MCP_DEFAULT_SERVER_ENDPOINT",
            "MCP_DEFAULT_SERVER_NAME",
            "MCP_DEFAULT_SERVER_DESCRIPTION",
            "MCP_DEFAULT_SERVER_ENABLED",
            "MCP_DEFAULT_SERVER_HEADERS_JSON",
            "MCP_DEFAULT_SERVERS_JSON",
        ]:
            os.environ.pop(key, None)
        self.tempdir = tempfile.TemporaryDirectory()
        self.storage = Storage(root_dir=Path(self.tempdir.name))
        self.registry = ServerRegistryService(self.storage)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_create_update_delete_server(self) -> None:
        created = self.registry.create_server(
            {
                "name": "Local OpenContext",
                "endpoint": "http://localhost:8000",
                "description": "test",
                "enabled": True,
            }
        )

        self.assertEqual(created["name"], "Local OpenContext")
        self.assertTrue(created["endpoint"].endswith("/mcp"))

        updated = self.registry.update_server(
            created["id"],
            {
                "name": "Local OpenContext 2",
                "enabled": False,
            },
        )
        self.assertEqual(updated["name"], "Local OpenContext 2")
        self.assertFalse(updated["enabled"])

        listed = self.registry.list_servers()
        self.assertEqual(len(listed), 1)

        self.registry.delete_server(created["id"])
        self.assertEqual(self.registry.list_servers(), [])

    def test_public_records_redact_headers_but_internal_keeps_them(self) -> None:
        created = self.registry.create_server(
            {
                "name": "Secured Endpoint",
                "endpoint": "https://secured.example/mcp",
                "headers": {
                    "Authorization": "Bearer top-secret-token",
                    "X-Api-Key": "abc123",
                },
            }
        )

        self.assertNotIn("headers", created)
        self.assertTrue(created.get("has_headers"))
        self.assertEqual(created.get("header_keys"), ["Authorization", "X-Api-Key"])

        listed = self.registry.list_servers()
        self.assertEqual(len(listed), 1)
        self.assertNotIn("headers", listed[0])
        self.assertTrue(listed[0].get("has_headers"))

        internal_rows = self.registry.list_servers_internal()
        self.assertEqual(len(internal_rows), 1)
        self.assertIn("headers", internal_rows[0])
        self.assertEqual(internal_rows[0]["headers"].get("Authorization"), "Bearer top-secret-token")
    def test_failed_connection_returns_normalized_diagnostics(self) -> None:
        created = self.registry.create_server(
            {
                "name": "Broken Endpoint",
                "endpoint": "http://127.0.0.1:9/mcp",
            }
        )

        result = self.registry.test_connection(created["id"])
        self.assertFalse(result["ok"])
        self.assertIn("stage", result)
        self.assertIn("error", result)
        self.assertIn("code", result["error"])
        self.assertIn("message", result["error"])

    def test_ensure_default_server_creates_from_env(self) -> None:
        os.environ["MCP_DEFAULT_SERVER_AUTO_REGISTER"] = "true"
        os.environ["MCP_DEFAULT_SERVER_ENDPOINT"] = "https://example.ngrok.dev/mcp"
        os.environ["MCP_DEFAULT_SERVER_NAME"] = "opencontext-main"
        os.environ["MCP_DEFAULT_SERVER_DESCRIPTION"] = "Default MCP"
        os.environ["MCP_DEFAULT_SERVER_ENABLED"] = "true"

        created = self.registry.ensure_default_server_from_env()
        self.assertIsNotNone(created)
        rows = self.registry.list_servers()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "opencontext-main")
        self.assertEqual(rows[0]["description"], "Default MCP")
        self.assertEqual(rows[0]["endpoint"], "https://example.ngrok.dev/mcp")

    def test_ensure_default_server_updates_existing_name(self) -> None:
        first = self.registry.create_server(
            {
                "name": "opencontext-main",
                "endpoint": "https://old.ngrok.dev/mcp",
                "description": "old",
            }
        )

        os.environ["MCP_DEFAULT_SERVER_AUTO_REGISTER"] = "true"
        os.environ["MCP_DEFAULT_SERVER_ENDPOINT"] = "https://new.ngrok.dev/mcp"
        os.environ["MCP_DEFAULT_SERVER_NAME"] = "opencontext-main"
        os.environ["MCP_DEFAULT_SERVER_DESCRIPTION"] = "new default"
        os.environ["MCP_DEFAULT_SERVER_ENABLED"] = "true"
        os.environ["MCP_DEFAULT_SERVER_HEADERS_JSON"] = '{"Authorization":"Bearer test-token"}'

        updated = self.registry.ensure_default_server_from_env()
        self.assertIsNotNone(updated)
        self.assertEqual(updated["id"], first["id"])
        self.assertEqual(updated["endpoint"], "https://new.ngrok.dev/mcp")
        self.assertEqual(updated["description"], "new default")
        rows = self.registry.list_servers()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["header_keys"], ["Authorization"])

    def test_ensure_default_servers_json_creates_multiple(self) -> None:
        os.environ["MCP_DEFAULT_SERVER_AUTO_REGISTER"] = "true"
        os.environ["MCP_DEFAULT_SERVERS_JSON"] = (
            '[{"name":"opencontext-main","endpoint":"https://opencontext.ngrok.dev/mcp",'
            '"description":"OpenContext default","enabled":true},'
            '{"name":"nyc-opengov","endpoint":"https://opengov.ngrok.dev/mcp",'
            '"description":"OpenGov NYC","enabled":true}]'
        )

        created = self.registry.ensure_default_servers_from_env()
        self.assertEqual(len(created), 2)
        rows = self.registry.list_servers()
        self.assertEqual(len(rows), 2)
        names = sorted([str(row.get("name") or "") for row in rows])
        self.assertEqual(names, ["nyc-opengov", "opencontext-main"])

    def test_ensure_default_servers_json_updates_existing_endpoint(self) -> None:
        first = self.registry.create_server(
            {
                "name": "nyc-opengov",
                "endpoint": "https://old-opengov.ngrok.dev/mcp",
                "description": "old",
            }
        )

        os.environ["MCP_DEFAULT_SERVER_AUTO_REGISTER"] = "true"
        os.environ["MCP_DEFAULT_SERVERS_JSON"] = (
            '[{"name":"nyc-opengov","endpoint":"https://new-opengov.ngrok.dev/mcp",'
            '"description":"new","enabled":true}]'
        )

        created = self.registry.ensure_default_servers_from_env()
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0]["id"], first["id"])
        self.assertEqual(created[0]["endpoint"], "https://new-opengov.ngrok.dev/mcp")

    def test_jsonrpc_call_parses_sse_payload(self) -> None:
        raw = (
            b'event: message\n'
            b'data: {"jsonrpc":"2.0","id":"1","result":{"tools":[]}}\n\n'
        )

        def fake_post_json(endpoint, payload, headers, stage, session_state=None):  # type: ignore[no-untyped-def]
            return raw, 17

        self.registry._post_json = fake_post_json  # type: ignore[assignment]
        result, latency = self.registry._jsonrpc_call(
            endpoint="https://example.test/mcp",
            method="tools/list",
            params={},
            stage="tools/list",
            headers={},
            session_state={},
        )
        self.assertEqual(latency, 17)
        self.assertEqual(result.get("tools"), [])

    def test_test_connection_allows_optional_ping_failure(self) -> None:
        created = self.registry.create_server(
            {
                "name": "SSE Endpoint",
                "endpoint": "https://example.test/mcp",
                "enabled": True,
            }
        )

        def fake_jsonrpc_call(*, endpoint, method, params, stage, headers, session_state=None):  # type: ignore[no-untyped-def]
            if stage == "ping":
                raise MCPConnectionError(
                    "http_error",
                    "Server returned HTTP 400",
                    stage="ping",
                    details={"status": 400, "body": "initialize required"},
                )
            if stage == "initialize":
                return {"serverInfo": {"name": "bridge", "version": "1.0.0"}}, 11
            if stage == "tools/list":
                return {"tools": [{"name": "get_data"}]}, 12
            raise AssertionError(f"unexpected stage {stage}")

        def fake_jsonrpc_notification(*, endpoint, method, params, stage, headers, session_state=None):  # type: ignore[no-untyped-def]
            return {}, 3

        self.registry._jsonrpc_call = fake_jsonrpc_call  # type: ignore[assignment]
        self.registry._jsonrpc_notification = fake_jsonrpc_notification  # type: ignore[assignment]
        result = self.registry.test_connection(created["id"])
        self.assertTrue(result.get("ok"))
        self.assertTrue(result.get("ping_optional_used"))


if __name__ == "__main__":
    unittest.main()

