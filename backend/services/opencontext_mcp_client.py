from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class MCPCallResult:
    method: str
    result: Any
    latency_ms: int


class MCPClientError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        method: str,
        endpoint: str,
        status_code: Optional[int] = None,
        details: Optional[Dict[str, Any]] = None,
        retriable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.method = method
        self.endpoint = endpoint
        self.status_code = status_code
        self.details = details or {}
        self.retriable = retriable

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "method": self.method,
            "endpoint": self.endpoint,
            "status_code": self.status_code,
            "retriable": self.retriable,
            "details": self.details,
        }


class OpenContextMCPClient:
    def __init__(
        self,
        endpoint: str,
        *,
        headers: Optional[Dict[str, str]] = None,
        connect_timeout_ms: Optional[int] = None,
        read_timeout_ms: Optional[int] = None,
        max_retries: Optional[int] = None,
        client_name: str = "opencontext-federated-dashboard",
        client_version: str = "0.1.0",
    ) -> None:
        self.endpoint = self.normalize_endpoint(endpoint)
        self.headers = self._normalize_headers(headers or {})
        self.connect_timeout_seconds = max(
            1.0,
            float((connect_timeout_ms if connect_timeout_ms is not None else int(os.getenv("MCP_CONNECT_TIMEOUT_MS", "8000"))) / 1000.0),
        )
        self.read_timeout_seconds = max(
            self.connect_timeout_seconds,
            float((read_timeout_ms if read_timeout_ms is not None else int(os.getenv("MCP_READ_TIMEOUT_MS", "45000"))) / 1000.0),
        )
        self.max_retries = max(
            0,
            int(max_retries if max_retries is not None else int(os.getenv("MCP_MAX_RETRIES", "2"))),
        )
        self.client_name = client_name
        self.client_version = client_version
        self._mcp_session_id: str = ""

    @staticmethod
    def normalize_endpoint(endpoint: str) -> str:
        raw = str(endpoint or "").strip()
        if not raw:
            raise MCPClientError(
                "validation_error",
                "endpoint is required",
                method="initialize",
                endpoint="",
                retriable=False,
            )

        if not raw.startswith("http://") and not raw.startswith("https://"):
            raw = f"https://{raw}"

        parsed = urllib.parse.urlparse(raw)
        if parsed.scheme not in {"http", "https"}:
            raise MCPClientError(
                "validation_error",
                "endpoint must use http or https",
                method="initialize",
                endpoint=raw,
                retriable=False,
            )
        if not parsed.netloc:
            raise MCPClientError(
                "validation_error",
                "endpoint host is required",
                method="initialize",
                endpoint=raw,
                retriable=False,
            )

        path = parsed.path or ""
        if not path or path == "/":
            path = "/mcp"
        elif not path.endswith("/mcp"):
            path = path.rstrip("/") + "/mcp"

        return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, path, parsed.params, parsed.query, parsed.fragment))

    @staticmethod
    def _normalize_headers(headers: Dict[str, str]) -> Dict[str, str]:
        output: Dict[str, str] = {}
        for key, value in headers.items():
            k = str(key or "").strip()
            v = str(value or "").strip()
            if k and v:
                output[k] = v
        return output

    def ping(self) -> MCPCallResult:
        return self.call("ping", {})

    def initialize(self, params: Optional[Dict[str, Any]] = None) -> MCPCallResult:
        payload = params or {
            "protocolVersion": "2025-03-26",
            "clientInfo": {"name": self.client_name, "version": self.client_version},
            "capabilities": {"tools": {}},
        }
        result = self.call("initialize", payload)
        if not self._send_initialized_notification():
            # Keep backwards compatibility with less strict MCP servers.
            pass
        return result

    def tools_list(self) -> MCPCallResult:
        return self.call("tools/list", {})

    def tools_call(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> MCPCallResult:
        tool_name = str(name or "").strip()
        if not tool_name:
            raise MCPClientError(
                "validation_error",
                "tool name is required",
                method="tools/call",
                endpoint=self.endpoint,
                retriable=False,
            )
        payload = {
            "name": tool_name,
            "arguments": arguments or {},
        }
        return self.call("tools/call", payload)

    def call(self, method: str, params: Optional[Dict[str, Any]] = None) -> MCPCallResult:
        rpc_method = str(method or "").strip()
        if not rpc_method:
            raise MCPClientError(
                "validation_error",
                "method is required",
                method="unknown",
                endpoint=self.endpoint,
                retriable=False,
            )

        request_payload = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": rpc_method,
            "params": params or {},
        }

        body, latency_ms = self._post_json(request_payload, rpc_method)
        parsed = self._parse_response(body, rpc_method)

        if "error" in parsed:
            error_obj = parsed.get("error") if isinstance(parsed.get("error"), dict) else {}
            raise MCPClientError(
                "jsonrpc_error",
                str(error_obj.get("message") or "JSON-RPC error"),
                method=rpc_method,
                endpoint=self.endpoint,
                details={"error": error_obj},
                retriable=False,
            )

        if "result" not in parsed:
            raise MCPClientError(
                "invalid_response",
                "JSON-RPC response missing result",
                method=rpc_method,
                endpoint=self.endpoint,
                details={"response": parsed},
                retriable=False,
            )

        return MCPCallResult(method=rpc_method, result=parsed.get("result"), latency_ms=latency_ms)

    def _send_initialized_notification(self) -> bool:
        payload = {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {},
        }
        try:
            self._post_json(payload, "notifications/initialized")
            return True
        except MCPClientError as exc:
            if self._is_optional_initialized_failure(exc):
                return False
            raise

    @staticmethod
    def _is_optional_initialized_failure(error: MCPClientError) -> bool:
        code = str(error.code or "").strip().lower()
        if code == "jsonrpc_error":
            return True
        if code != "http_error":
            return False
        status = int(error.status_code or 0)
        if status in {400, 404, 405, 406}:
            return True
        body = str(error.details.get("body") or "").lower()
        if "notifications/initialized" in body and ("not found" in body or "unsupported" in body):
            return True
        return False

    def _parse_response(self, body: bytes, method: str) -> Dict[str, Any]:
        raw_text = body.decode("utf-8", errors="ignore")
        try:
            parsed = json.loads(raw_text)
        except Exception:
            parsed = self._extract_sse_json(raw_text)
            if parsed is None:
                raise MCPClientError(
                    "parse_error",
                    "Server returned invalid JSON",
                    method=method,
                    endpoint=self.endpoint,
                    details={"raw": raw_text[:400]},
                    retriable=False,
                )

        if not isinstance(parsed, dict):
            raise MCPClientError(
                "invalid_response",
                "JSON-RPC response must be an object",
                method=method,
                endpoint=self.endpoint,
                details={"response_type": type(parsed).__name__},
                retriable=False,
            )

        return parsed

    @staticmethod
    def _extract_sse_json(raw_text: str) -> Optional[Dict[str, Any]]:
        text = str(raw_text or "").strip()
        if not text or "data:" not in text:
            return None

        candidates = []
        blocks = text.replace("\r\n", "\n").split("\n\n")
        for block in blocks:
            lines = block.splitlines()
            data_lines = []
            for line in lines:
                stripped = line.lstrip()
                if stripped.startswith("data:"):
                    data_lines.append(stripped.split("data:", 1)[1].lstrip())
            if not data_lines:
                continue
            candidate = "\n".join(data_lines).strip()
            if not candidate or candidate == "[DONE]":
                continue
            candidates.append(candidate)

        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
            except Exception:
                continue
            if isinstance(parsed, dict):
                return parsed
        return None

    def _post_json(self, payload: Dict[str, Any], method: str) -> tuple[bytes, int]:
        data = json.dumps(payload).encode("utf-8")
        request_headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._mcp_session_id:
            request_headers["mcp-session-id"] = self._mcp_session_id
        request_headers.update(self.headers)

        last_error: Optional[MCPClientError] = None

        for attempt in range(self.max_retries + 1):
            req = urllib.request.Request(self.endpoint, data=data, headers=request_headers, method="POST")
            started = time.perf_counter()
            try:
                with urllib.request.urlopen(req, timeout=self.read_timeout_seconds) as response:
                    status = int(response.getcode())
                    body = response.read()
                    response_session = (
                        str(response.headers.get("mcp-session-id") or "").strip()
                        if hasattr(response, "headers")
                        else ""
                    )
                    if response_session:
                        self._mcp_session_id = response_session
                    latency_ms = int((time.perf_counter() - started) * 1000)
                    if status < 200 or status >= 300:
                        raise MCPClientError(
                            "http_error",
                            f"Server returned HTTP {status}",
                            method=method,
                            endpoint=self.endpoint,
                            status_code=status,
                            details={"status": status, "body": body.decode("utf-8", errors="ignore")[:400]},
                            retriable=False,
                        )
                    return body, latency_ms
            except urllib.error.HTTPError as exc:
                raw = exc.read().decode("utf-8", errors="ignore") if hasattr(exc, "read") else ""
                retriable = int(exc.code) >= 500
                last_error = MCPClientError(
                    "http_error",
                    f"Server returned HTTP {exc.code}",
                    method=method,
                    endpoint=self.endpoint,
                    status_code=int(exc.code),
                    details={"status": int(exc.code), "body": raw[:400]},
                    retriable=retriable,
                )
            except TimeoutError:
                last_error = MCPClientError(
                    "timeout",
                    "Request timed out while calling MCP endpoint",
                    method=method,
                    endpoint=self.endpoint,
                    details={"timeout_seconds": self.read_timeout_seconds},
                    retriable=True,
                )
            except urllib.error.URLError as exc:
                last_error = MCPClientError(
                    "network_error",
                    "Could not reach MCP endpoint",
                    method=method,
                    endpoint=self.endpoint,
                    details={"reason": str(exc.reason)},
                    retriable=True,
                )
            except Exception as exc:
                last_error = MCPClientError(
                    "network_error",
                    "Unexpected network error while calling MCP endpoint",
                    method=method,
                    endpoint=self.endpoint,
                    details={"reason": str(exc)},
                    retriable=True,
                )

            if attempt >= self.max_retries:
                break
            if last_error is not None and not last_error.retriable:
                break
            time.sleep(0.2 * (attempt + 1))

        if last_error is not None:
            raise last_error

        raise MCPClientError(
            "network_error",
            "Unknown request failure",
            method=method,
            endpoint=self.endpoint,
            retriable=False,
        )
