import json
import threading
import time
import unittest
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from services.opencontext_mcp_client import MCPClientError, OpenContextMCPClient


@dataclass
class StubState:
    mode: str = "success"
    call_count: int = 0
    session_id: str = ""
    initialized_notified: bool = False


class MCPStubHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        state = self.server.state
        state.call_count += 1

        if state.mode == "malformed":
            payload = b"not-json"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        if state.mode == "http_500":
            payload = b'{"error":"server error"}'
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        if state.mode == "retry_once_then_success" and state.call_count == 1:
            payload = b'{"error":"bad gateway"}'
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        content_len = int(self.headers.get("Content-Length", "0") or 0)
        body = self.rfile.read(content_len)
        request_json = json.loads(body.decode("utf-8"))
        method = request_json.get("method")

        if method == "ping":
            result = {"status": "ok"}
        elif method == "initialize":
            result = {
                "protocolVersion": "2025-03-26",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "opencontext", "version": "1.0.0"},
            }
        elif method == "tools/list":
            if state.mode == "requires_initialized":
                session_id = str(self.headers.get("mcp-session-id") or "").strip()
                if not session_id or session_id != state.session_id or not state.initialized_notified:
                    payload = b'{"jsonrpc":"2.0","id":"1","error":{"message":"initialize/initialized required"}}'
                    self.send_response(400)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                    return
            result = {
                "tools": [
                    {
                        "name": "ckan__search_datasets",
                        "description": "Search CKAN datasets",
                        "inputSchema": {"type": "object"},
                    }
                ]
            }
        elif method == "tools/call":
            params = request_json.get("params") if isinstance(request_json.get("params"), dict) else {}
            result = {"content": [{"type": "text", "text": f"called {params.get('name', '')}"}]}
        elif method == "notifications/initialized":
            if state.mode == "requires_initialized":
                session_id = str(self.headers.get("mcp-session-id") or "").strip()
                if not session_id or session_id != state.session_id:
                    payload = b'{"jsonrpc":"2.0","id":"1","error":{"message":"missing mcp-session-id"}}'
                    self.send_response(400)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                    return
                state.initialized_notified = True
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        else:
            payload = b'{"jsonrpc":"2.0","id":"1","error":{"message":"unknown"}}'
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        response = {
            "jsonrpc": "2.0",
            "id": request_json.get("id"),
            "result": result,
        }
        payload = json.dumps(response).encode("utf-8")
        if method == "initialize" and state.mode == "requires_initialized":
            state.session_id = "sess-123"
            state.initialized_notified = False
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("mcp-session-id", state.session_id)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if state.mode == "sse_success":
            sse_payload = f"event: message\ndata: {json.dumps(response)}\n\n".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(sse_payload)))
            self.end_headers()
            self.wfile.write(sse_payload)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args) -> None:
        return


class OpenContextMCPClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), MCPStubHandler)
        cls.server.state = StubState()
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.endpoint = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self) -> None:
        self.server.state.mode = "success"
        self.server.state.call_count = 0
        self.server.state.session_id = ""
        self.server.state.initialized_notified = False

    def test_success_initialize_tools_list_and_tools_call(self) -> None:
        client = OpenContextMCPClient(self.endpoint, read_timeout_ms=700, max_retries=0)

        init_result = client.initialize()
        tools_result = client.tools_list()
        call_result = client.tools_call("ckan__search_datasets", {"query": "housing"})

        self.assertEqual(init_result.method, "initialize")
        self.assertIn("serverInfo", init_result.result)
        self.assertEqual(tools_result.method, "tools/list")
        self.assertTrue(len(tools_result.result.get("tools", [])) > 0)
        self.assertEqual(call_result.method, "tools/call")
        self.assertIn("content", call_result.result)

    def test_timeout_error(self) -> None:
        client = OpenContextMCPClient(self.endpoint, read_timeout_ms=90, max_retries=0)

        with patch("services.opencontext_mcp_client.urllib.request.urlopen", side_effect=TimeoutError()):
            with self.assertRaises(MCPClientError) as ctx:
                client.ping()

        self.assertEqual(ctx.exception.code, "timeout")
        self.assertEqual(ctx.exception.method, "ping")

    def test_malformed_response_error(self) -> None:
        client = OpenContextMCPClient(self.endpoint, read_timeout_ms=700, max_retries=0)

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def getcode(self):
                return 200

            def read(self):
                return b"not-json"

        with patch("services.opencontext_mcp_client.urllib.request.urlopen", return_value=FakeResponse()):
            with self.assertRaises(MCPClientError) as ctx:
                client.ping()

        self.assertEqual(ctx.exception.code, "parse_error")
        self.assertEqual(ctx.exception.method, "ping")

    def test_non_200_error(self) -> None:
        self.server.state.mode = "http_500"
        client = OpenContextMCPClient(self.endpoint, read_timeout_ms=700, max_retries=0)

        with self.assertRaises(MCPClientError) as ctx:
            client.ping()

        self.assertEqual(ctx.exception.code, "http_error")
        self.assertEqual(ctx.exception.status_code, 500)

    def test_retries_then_success(self) -> None:
        self.server.state.mode = "retry_once_then_success"
        client = OpenContextMCPClient(self.endpoint, read_timeout_ms=700, max_retries=1)

        result = client.ping()
        self.assertEqual(result.method, "ping")
        self.assertEqual(result.result.get("status"), "ok")
        self.assertEqual(self.server.state.call_count, 2)

    def test_parses_sse_wrapped_jsonrpc_message(self) -> None:
        self.server.state.mode = "sse_success"
        client = OpenContextMCPClient(self.endpoint, read_timeout_ms=700, max_retries=0)

        init_result = client.initialize()
        tools_result = client.tools_list()

        self.assertEqual(init_result.method, "initialize")
        self.assertIn("serverInfo", init_result.result)
        self.assertEqual(tools_result.method, "tools/list")
        self.assertIn("tools", tools_result.result)

    def test_initialize_notification_with_session_header(self) -> None:
        self.server.state.mode = "requires_initialized"
        client = OpenContextMCPClient(self.endpoint, read_timeout_ms=700, max_retries=0)

        init_result = client.initialize()
        tools_result = client.tools_list()

        self.assertEqual(init_result.method, "initialize")
        self.assertIn("serverInfo", init_result.result)
        self.assertEqual(tools_result.method, "tools/list")
        self.assertIn("tools", tools_result.result)


if __name__ == "__main__":
    unittest.main()
