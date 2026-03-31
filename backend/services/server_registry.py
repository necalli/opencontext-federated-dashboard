from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from .storage import Storage


class RegistryError(Exception):
    def __init__(self, code: str, message: str, *, details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


class ValidationError(RegistryError):
    def __init__(self, message: str, *, details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__("validation_error", message, details=details)


class NotFoundError(RegistryError):
    def __init__(self, message: str, *, details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__("not_found", message, details=details)


class MCPConnectionError(RegistryError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        stage: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(code, message, details=details)
        self.stage = stage


class ServerRegistryService:
    def __init__(self, storage: Storage) -> None:
        self.storage = storage
        self.connect_timeout_seconds = max(
            1.0,
            float(int(os.getenv("MCP_CONNECT_TIMEOUT_MS", "8000")) / 1000.0),
        )
        self.read_timeout_seconds = max(
            self.connect_timeout_seconds,
            float(int(os.getenv("MCP_READ_TIMEOUT_MS", "45000")) / 1000.0),
        )
        self.max_retries = max(0, int(os.getenv("MCP_MAX_RETRIES", "2")))

    def list_servers(self) -> List[Dict[str, Any]]:
        servers = [self._public_record(item) for item in self.storage.get_mcp_servers()]
        return sorted(servers, key=lambda row: str(row.get("name") or "").lower())

    def list_servers_internal(self) -> List[Dict[str, Any]]:
        rows = [dict(item) for item in self.storage.get_mcp_servers() if isinstance(item, dict)]
        return sorted(rows, key=lambda row: str(row.get("name") or "").lower())

    def ensure_default_servers_from_env(self) -> List[Dict[str, Any]]:
        auto_register = self._env_to_bool(os.getenv("MCP_DEFAULT_SERVER_AUTO_REGISTER"), True)
        if not auto_register:
            return []

        results: List[Dict[str, Any]] = []
        raw_multi = str(os.getenv("MCP_DEFAULT_SERVERS_JSON", "")).strip()
        if raw_multi:
            try:
                parsed = json.loads(raw_multi)
            except Exception as exc:
                raise ValidationError(
                    "MCP_DEFAULT_SERVERS_JSON must be valid JSON",
                    details={"reason": str(exc)},
                ) from exc
            if not isinstance(parsed, list):
                raise ValidationError("MCP_DEFAULT_SERVERS_JSON must be a JSON array")
            for idx, item in enumerate(parsed):
                if not isinstance(item, dict):
                    raise ValidationError(
                        "MCP_DEFAULT_SERVERS_JSON items must be JSON objects",
                        details={"index": idx},
                    )
                payload: Dict[str, Any] = {
                    "name": str(item.get("name") or f"default-mcp-{idx + 1}").strip(),
                    "endpoint": str(item.get("endpoint") or "").strip(),
                    "description": str(item.get("description") or "").strip(),
                    "enabled": bool(item.get("enabled", True)),
                    "headers": item.get("headers") if isinstance(item.get("headers"), dict) else {},
                }
                if not payload["endpoint"]:
                    raise ValidationError(
                        "MCP_DEFAULT_SERVERS_JSON entries require endpoint",
                        details={"index": idx},
                    )
                results.append(self._upsert_default_server(payload))

        single_default = self.ensure_default_server_from_env()
        if single_default:
            results.append(single_default)
        return results

    def ensure_default_server_from_env(self) -> Optional[Dict[str, Any]]:
        auto_register = self._env_to_bool(os.getenv("MCP_DEFAULT_SERVER_AUTO_REGISTER"), True)
        endpoint = str(os.getenv("MCP_DEFAULT_SERVER_ENDPOINT", "")).strip()
        if not auto_register or not endpoint:
            return None

        name = str(os.getenv("MCP_DEFAULT_SERVER_NAME", "opencontext-main")).strip() or "opencontext-main"
        description = str(
            os.getenv("MCP_DEFAULT_SERVER_DESCRIPTION", "OpenContext default MCP server")
        ).strip()
        enabled = self._env_to_bool(os.getenv("MCP_DEFAULT_SERVER_ENABLED"), True)
        headers = self._read_default_headers_json()

        payload: Dict[str, Any] = {
            "name": name,
            "endpoint": endpoint,
            "description": description,
            "enabled": enabled,
            "headers": headers,
        }
        return self._upsert_default_server(payload)

    def get_server(self, server_id: str) -> Dict[str, Any]:
        record = self._find_server(server_id)
        if not record:
            raise NotFoundError(f"Server '{server_id}' was not found")
        return self._public_record(record)

    def create_server(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        clean = self._normalize_payload(payload, existing=None)
        now = self._now_iso()
        record: Dict[str, Any] = {
            "id": str(uuid.uuid4()),
            "name": clean["name"],
            "endpoint": clean["endpoint"],
            "description": clean.get("description", ""),
            "enabled": clean.get("enabled", True),
            "headers": clean.get("headers", {}),
            "created_at": now,
            "updated_at": now,
            "last_test": None,
        }
        rows = self.storage.get_mcp_servers()
        rows.append(record)
        self.storage.save_mcp_servers(rows)
        return self._public_record(record)

    def update_server(self, server_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        rows = self.storage.get_mcp_servers()
        index = self._find_server_index(rows, server_id)
        if index < 0:
            raise NotFoundError(f"Server '{server_id}' was not found")

        current = rows[index]
        clean = self._normalize_payload(payload, existing=current)
        current["name"] = clean["name"]
        current["endpoint"] = clean["endpoint"]
        current["description"] = clean.get("description", "")
        current["enabled"] = clean.get("enabled", True)
        current["headers"] = clean.get("headers", {})
        current["updated_at"] = self._now_iso()

        rows[index] = current
        self.storage.save_mcp_servers(rows)
        return self._public_record(current)

    def delete_server(self, server_id: str) -> None:
        rows = self.storage.get_mcp_servers()
        index = self._find_server_index(rows, server_id)
        if index < 0:
            raise NotFoundError(f"Server '{server_id}' was not found")
        rows.pop(index)
        self.storage.save_mcp_servers(rows)

    def test_connection(self, server_id: str) -> Dict[str, Any]:
        rows = self.storage.get_mcp_servers()
        index = self._find_server_index(rows, server_id)
        if index < 0:
            raise NotFoundError(f"Server '{server_id}' was not found")

        record = rows[index]
        endpoint = str(record.get("endpoint") or "").strip()
        headers = record.get("headers") if isinstance(record.get("headers"), dict) else {}

        checks: List[Dict[str, Any]] = []
        session_state: Dict[str, str] = {}
        stage = "ping"
        try:
            ping_ok = False
            ping_error: Optional[MCPConnectionError] = None
            try:
                _, ping_ms = self._jsonrpc_call(
                    endpoint=endpoint,
                    method="ping",
                    params={},
                    stage="ping",
                    headers=headers,
                    session_state=session_state,
                )
                checks.append({"name": "ping", "ok": True, "latency_ms": ping_ms})
                ping_ok = True
            except MCPConnectionError as exc:
                ping_error = exc
                if self._is_optional_ping_failure(exc):
                    checks.append(
                        {
                            "name": "ping",
                            "ok": False,
                            "optional": True,
                            "error": {
                                "code": exc.code,
                                "message": exc.message,
                                "details": exc.details,
                            },
                        }
                    )
                else:
                    raise

            stage = "initialize"
            init_result, init_ms = self._jsonrpc_call(
                endpoint=endpoint,
                method="initialize",
                params={
                    "protocolVersion": "2025-03-26",
                    "clientInfo": {
                        "name": "opencontext-federated-dashboard",
                        "version": "0.1.0",
                    },
                    "capabilities": {"tools": {}},
                },
                stage="initialize",
                headers=headers,
                session_state=session_state,
            )
            checks.append({"name": "initialize", "ok": True, "latency_ms": init_ms})

            stage = "initialized"
            try:
                _, init_notify_ms = self._jsonrpc_notification(
                    endpoint=endpoint,
                    method="notifications/initialized",
                    params={},
                    stage="initialized",
                    headers=headers,
                    session_state=session_state,
                )
                checks.append({"name": "initialized", "ok": True, "latency_ms": init_notify_ms})
            except MCPConnectionError as exc:
                if self._is_optional_initialized_failure(exc):
                    checks.append(
                        {
                            "name": "initialized",
                            "ok": False,
                            "optional": True,
                            "error": {
                                "code": exc.code,
                                "message": exc.message,
                                "details": exc.details,
                            },
                        }
                    )
                else:
                    raise

            stage = "tools/list"
            tools_result, tools_ms = self._jsonrpc_call(
                endpoint=endpoint,
                method="tools/list",
                params={},
                stage="tools/list",
                headers=headers,
                session_state=session_state,
            )
            tools = tools_result.get("tools") if isinstance(tools_result, dict) else []
            if not isinstance(tools, list):
                raise MCPConnectionError(
                    "invalid_response",
                    "tools/list response did not include a tools array",
                    stage="tools/list",
                    details={"response": tools_result},
                )
            checks.append(
                {
                    "name": "tools/list",
                    "ok": True,
                    "latency_ms": tools_ms,
                    "tool_count": len(tools),
                }
            )

            result = {
                "ok": True,
                "stage": "complete",
                "checks": checks,
                "server_info": init_result.get("serverInfo") if isinstance(init_result, dict) else None,
                "tool_count": len(tools),
                "ping_optional_used": (not ping_ok and ping_error is not None and self._is_optional_ping_failure(ping_error)),
                "tested_at": self._now_iso(),
            }
            record["last_test"] = result
            record["updated_at"] = self._now_iso()
            rows[index] = record
            self.storage.save_mcp_servers(rows)
            return result

        except MCPConnectionError as exc:
            checks.append(
                {
                    "name": stage,
                    "ok": False,
                    "error": {
                        "code": exc.code,
                        "message": exc.message,
                        "details": exc.details,
                    },
                }
            )
            result = {
                "ok": False,
                "stage": exc.stage,
                "checks": checks,
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "details": exc.details,
                },
                "tested_at": self._now_iso(),
            }
            record["last_test"] = result
            record["updated_at"] = self._now_iso()
            rows[index] = record
            self.storage.save_mcp_servers(rows)
            return result

    def _jsonrpc_call(
        self,
        *,
        endpoint: str,
        method: str,
        params: Dict[str, Any],
        stage: str,
        headers: Dict[str, Any],
        session_state: Optional[Dict[str, str]] = None,
    ) -> Tuple[Dict[str, Any], int]:
        body = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": method,
            "params": params,
        }
        merged_headers: Dict[str, str] = {}
        for key, value in headers.items():
            k = str(key).strip()
            v = str(value).strip()
            if k and v:
                merged_headers[k] = v

        raw, latency_ms = self._post_json(
            endpoint,
            body,
            merged_headers,
            stage=stage,
            session_state=session_state,
        )
        parsed = self._parse_jsonrpc_payload(raw)

        if not isinstance(parsed, dict):
            raise MCPConnectionError(
                "invalid_response",
                "JSON-RPC response was not an object",
                stage=stage,
                details={"response_type": type(parsed).__name__},
            )

        if "error" in parsed:
            error_obj = parsed.get("error") if isinstance(parsed.get("error"), dict) else {}
            raise MCPConnectionError(
                "jsonrpc_error",
                str(error_obj.get("message") or "JSON-RPC error"),
                stage=stage,
                details={"error": error_obj},
            )

        if "result" not in parsed:
            raise MCPConnectionError(
                "invalid_response",
                "JSON-RPC response missing result",
                stage=stage,
                details={"response": parsed},
            )

        result = parsed.get("result")
        if not isinstance(result, dict):
            return ({"value": result}, latency_ms)
        return (result, latency_ms)

    def _jsonrpc_notification(
        self,
        *,
        endpoint: str,
        method: str,
        params: Dict[str, Any],
        stage: str,
        headers: Dict[str, Any],
        session_state: Optional[Dict[str, str]] = None,
    ) -> Tuple[Dict[str, Any], int]:
        body = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        merged_headers: Dict[str, str] = {}
        for key, value in headers.items():
            k = str(key).strip()
            v = str(value).strip()
            if k and v:
                merged_headers[k] = v

        raw, latency_ms = self._post_json(
            endpoint,
            body,
            merged_headers,
            stage=stage,
            session_state=session_state,
        )
        if not raw:
            return ({}, latency_ms)
        parsed = self._parse_jsonrpc_payload(raw)
        if "error" in parsed:
            error_obj = parsed.get("error") if isinstance(parsed.get("error"), dict) else {}
            raise MCPConnectionError(
                "jsonrpc_error",
                str(error_obj.get("message") or "JSON-RPC error"),
                stage=stage,
                details={"error": error_obj},
            )
        return (parsed, latency_ms)

    def _post_json(
        self,
        endpoint: str,
        payload: Dict[str, Any],
        headers: Dict[str, str],
        *,
        stage: str,
        session_state: Optional[Dict[str, str]] = None,
    ) -> Tuple[bytes, int]:
        target = self._normalize_endpoint(endpoint)
        data = json.dumps(payload).encode("utf-8")
        request_headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if session_state:
            current_session = str(session_state.get("mcp_session_id") or "").strip()
            if current_session:
                request_headers["mcp-session-id"] = current_session
        request_headers.update(headers)

        last_error: Optional[MCPConnectionError] = None
        for attempt in range(self.max_retries + 1):
            req = urllib.request.Request(target, data=data, headers=request_headers, method="POST")
            started = time.perf_counter()
            try:
                with urllib.request.urlopen(req, timeout=self.read_timeout_seconds) as resp:
                    status = int(resp.getcode())
                    body = resp.read()
                    if session_state is not None and hasattr(resp, "headers"):
                        response_session = str(resp.headers.get("mcp-session-id") or "").strip()
                        if response_session:
                            session_state["mcp_session_id"] = response_session
                    latency_ms = int((time.perf_counter() - started) * 1000)
                    if status < 200 or status >= 300:
                        raise MCPConnectionError(
                            "http_error",
                            f"Server returned HTTP {status}",
                            stage=stage,
                            details={"status": status, "body": body.decode("utf-8", errors="ignore")[:400]},
                        )
                    return body, latency_ms
            except urllib.error.HTTPError as exc:
                raw = exc.read().decode("utf-8", errors="ignore") if hasattr(exc, "read") else ""
                last_error = MCPConnectionError(
                    "http_error",
                    f"Server returned HTTP {exc.code}",
                    stage=stage,
                    details={"status": int(exc.code), "body": raw[:400]},
                )
            except urllib.error.URLError as exc:
                last_error = MCPConnectionError(
                    "network_error",
                    "Could not reach MCP server endpoint",
                    stage=stage,
                    details={"reason": str(exc.reason)},
                )
            except TimeoutError:
                last_error = MCPConnectionError(
                    "timeout",
                    "Request timed out while calling MCP server",
                    stage=stage,
                    details={"timeout_seconds": self.read_timeout_seconds},
                )
            except Exception as exc:
                last_error = MCPConnectionError(
                    "network_error",
                    "Unexpected network error while calling MCP server",
                    stage=stage,
                    details={"reason": str(exc)},
                )

            if attempt >= self.max_retries:
                break
            time.sleep(0.2 * (attempt + 1))

        if last_error:
            raise last_error
        raise MCPConnectionError("network_error", "Unknown connection failure", stage=stage)

    def _parse_jsonrpc_payload(self, raw: bytes) -> Dict[str, Any]:
        text = raw.decode("utf-8", errors="ignore")
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass

        sse_payload = self._extract_sse_json_payload(text)
        if sse_payload is not None:
            return sse_payload

        raise MCPConnectionError(
            "invalid_response",
            "Server returned non-JSON response",
            stage="parse",
            details={"raw": text[:400]},
        )

    @staticmethod
    def _extract_sse_json_payload(raw_text: str) -> Optional[Dict[str, Any]]:
        text = str(raw_text or "").strip()
        if not text or "data:" not in text:
            return None

        blocks = text.replace("\r\n", "\n").split("\n\n")
        for block in blocks:
            data_lines = []
            for line in block.splitlines():
                stripped = line.lstrip()
                if stripped.startswith("data:"):
                    data_lines.append(stripped.split("data:", 1)[1].lstrip())
            if not data_lines:
                continue
            candidate = "\n".join(data_lines).strip()
            if not candidate or candidate == "[DONE]":
                continue
            try:
                parsed = json.loads(candidate)
            except Exception:
                continue
            if isinstance(parsed, dict):
                return parsed
        return None

    @staticmethod
    def _is_optional_ping_failure(error: MCPConnectionError) -> bool:
        code = str(error.code or "").strip().lower()
        if code == "jsonrpc_error":
            return True
        if code != "http_error":
            return False
        status = int(error.details.get("status") or 0)
        if status in {400, 404, 405, 406}:
            return True
        body = str(error.details.get("body") or "").lower()
        if "ping" in body and ("not found" in body or "unsupported" in body):
            return True
        if "initialize" in body and ("required" in body or "before" in body):
            return True
        return False

    @staticmethod
    def _is_optional_initialized_failure(error: MCPConnectionError) -> bool:
        code = str(error.code or "").strip().lower()
        if code == "jsonrpc_error":
            return True
        if code != "http_error":
            return False
        status = int(error.details.get("status") or 0)
        if status in {400, 404, 405, 406}:
            return True
        body = str(error.details.get("body") or "").lower()
        if "notifications/initialized" in body and ("not found" in body or "unsupported" in body):
            return True
        return False

    def _normalize_payload(
        self,
        payload: Dict[str, Any],
        *,
        existing: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        name_raw = payload.get("name") if "name" in payload else (existing or {}).get("name")
        endpoint_raw = payload.get("endpoint") if "endpoint" in payload else (existing or {}).get("endpoint")

        name = str(name_raw or "").strip()
        if not name:
            raise ValidationError("name is required")

        endpoint = self._normalize_endpoint(str(endpoint_raw or "").strip())

        description_raw = payload.get("description") if "description" in payload else (existing or {}).get("description")
        enabled_raw = payload.get("enabled") if "enabled" in payload else (existing or {}).get("enabled", True)
        headers_raw = payload.get("headers") if "headers" in payload else (existing or {}).get("headers", {})

        description = str(description_raw or "").strip()
        enabled = bool(enabled_raw)

        headers: Dict[str, str] = {}
        if isinstance(headers_raw, dict):
            for key, value in headers_raw.items():
                k = str(key or "").strip()
                v = str(value or "").strip()
                if k and v:
                    headers[k] = v

        return {
            "name": name,
            "endpoint": endpoint,
            "description": description,
            "enabled": enabled,
            "headers": headers,
        }

    def _normalize_endpoint(self, endpoint: str) -> str:
        trimmed = endpoint.strip()
        if not trimmed:
            raise ValidationError("endpoint is required")

        if not trimmed.startswith("http://") and not trimmed.startswith("https://"):
            trimmed = f"https://{trimmed}"

        parsed = urllib.parse.urlparse(trimmed)
        if parsed.scheme not in {"http", "https"}:
            raise ValidationError("endpoint must use http or https")
        if not parsed.netloc:
            raise ValidationError("endpoint host is required")

        path = parsed.path or ""
        if not path or path == "/":
            path = "/mcp"
        elif not path.endswith("/mcp"):
            path = path.rstrip("/") + "/mcp"

        normalized = urllib.parse.urlunparse(
            (parsed.scheme, parsed.netloc, path, parsed.params, parsed.query, parsed.fragment)
        )
        return normalized

    def _find_server(self, server_id: str) -> Optional[Dict[str, Any]]:
        rows = self.storage.get_mcp_servers()
        for item in rows:
            if str(item.get("id") or "") == str(server_id):
                return item
        return None

    def _find_server_index(self, rows: List[Dict[str, Any]], server_id: str) -> int:
        target = str(server_id)
        for idx, row in enumerate(rows):
            if str(row.get("id") or "") == target:
                return idx
        return -1

    def _public_record(self, record: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(record)
        headers = out.get("headers") if isinstance(out.get("headers"), dict) else {}
        out.pop("headers", None)
        out["has_headers"] = bool(headers)
        out["header_keys"] = sorted([str(key) for key in headers.keys()])
        return out

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _env_to_bool(value: Any, default: bool) -> bool:
        if value is None:
            return bool(default)
        if isinstance(value, bool):
            return value
        lowered = str(value).strip().lower()
        if not lowered:
            return bool(default)
        if lowered in {"1", "true", "yes", "y", "on"}:
            return True
        if lowered in {"0", "false", "no", "n", "off"}:
            return False
        return bool(default)

    def _read_default_headers_json(self) -> Dict[str, str]:
        raw = str(os.getenv("MCP_DEFAULT_SERVER_HEADERS_JSON", "")).strip()
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except Exception as exc:
            raise ValidationError(
                "MCP_DEFAULT_SERVER_HEADERS_JSON must be a valid JSON object",
                details={"reason": str(exc)},
            ) from exc
        if not isinstance(parsed, dict):
            raise ValidationError("MCP_DEFAULT_SERVER_HEADERS_JSON must be a JSON object")
        headers: Dict[str, str] = {}
        for key, value in parsed.items():
            k = str(key or "").strip()
            v = str(value or "").strip()
            if k and v:
                headers[k] = v
        return headers

    def _upsert_default_server(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        clean = self._normalize_payload(payload, existing=None)
        rows = self.storage.get_mcp_servers()
        match_index = -1
        target_name = str(clean["name"]).casefold()
        target_endpoint = str(clean["endpoint"]).strip()
        for idx, row in enumerate(rows):
            row_name = str(row.get("name") or "").strip().casefold()
            row_endpoint = str(row.get("endpoint") or "").strip()
            if row_name == target_name or row_endpoint == target_endpoint:
                match_index = idx
                break
        if match_index >= 0:
            existing_id = str(rows[match_index].get("id") or "").strip()
            return self.update_server(existing_id, payload)
        return self.create_server(payload)

