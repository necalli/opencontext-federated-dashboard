from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional


class AnthropicConnectorError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: Optional[int] = None,
        details: Optional[Dict[str, Any]] = None,
        retriable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}
        self.retriable = retriable

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "status_code": self.status_code,
            "retriable": self.retriable,
            "details": self.details,
        }


class AnthropicMCPConnectorRuntime:
    def __init__(self) -> None:
        self.api_key = str(os.getenv("ANTHROPIC_API_KEY", "")).strip()
        self.model = str(os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")).strip()
        self.base_url = str(os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com/v1")).rstrip("/")
        self.version = str(os.getenv("ANTHROPIC_VERSION", "2023-06-01")).strip()
        self.beta = str(os.getenv("ANTHROPIC_BETA", "mcp-client-2025-11-20")).strip()
        self.max_tokens = max(256, int(os.getenv("ANTHROPIC_MAX_TOKENS", "900") or 900))
        self.timeout_seconds = max(5, int(os.getenv("ANTHROPIC_TIMEOUT_SECONDS", "60") or 60))

    def generate(
        self,
        *,
        message: str,
        mcp_servers: List[Dict[str, Any]],
        history: Optional[List[Dict[str, Any]]] = None,
        system_prompt: str = "",
    ) -> Dict[str, Any]:
        prompt = str(message or "").strip()
        if not prompt:
            raise AnthropicConnectorError("validation_error", "message is required", retriable=False)

        if not self.api_key:
            raise AnthropicConnectorError(
                "missing_api_key",
                "ANTHROPIC_API_KEY is not configured",
                retriable=False,
            )

        normalized_servers = self._build_server_defs(mcp_servers)
        if not normalized_servers:
            raise AnthropicConnectorError(
                "no_servers",
                "No enabled MCP servers are available for connector runtime",
                retriable=False,
            )

        payload: Dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": self._build_messages(prompt, history or []),
            "mcp_servers": normalized_servers,
            "tools": [
                {
                    "type": "mcp_toolset",
                    "mcp_server_name": server["name"],
                }
                for server in normalized_servers
            ],
        }
        if str(system_prompt or "").strip():
            payload["system"] = str(system_prompt).strip()

        raw_response = self._post_messages(payload)
        try:
            parsed = json.loads(raw_response.decode("utf-8"))
        except Exception as exc:
            raise AnthropicConnectorError(
                "parse_error",
                "Anthropic response was not valid JSON",
                retriable=False,
                details={"reason": str(exc), "raw": raw_response.decode("utf-8", errors="ignore")[:500]},
            ) from exc

        if not isinstance(parsed, dict):
            raise AnthropicConnectorError(
                "invalid_response",
                "Anthropic response JSON must be an object",
                retriable=False,
                details={"response_type": type(parsed).__name__},
            )

        content = parsed.get("content") if isinstance(parsed.get("content"), list) else []
        text = self._extract_text_content(content)
        tool_events = self._extract_tool_events(content)

        return {
            "text": text,
            "response_id": parsed.get("id"),
            "model": parsed.get("model"),
            "stop_reason": parsed.get("stop_reason"),
            "usage": parsed.get("usage") if isinstance(parsed.get("usage"), dict) else {},
            "server_names": [entry["name"] for entry in normalized_servers],
            "toolset_count": len(normalized_servers),
            "tool_events": tool_events,
            "raw": parsed,
        }

    def _post_messages(self, payload: Dict[str, Any]) -> bytes:
        url = f"{self.base_url}/messages"
        data = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "X-API-Key": self.api_key,
            "anthropic-version": self.version,
            "anthropic-beta": self.beta,
        }

        req = urllib.request.Request(url, data=data, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as response:
                status = int(response.getcode())
                body = response.read()
                if status < 200 or status >= 300:
                    raise AnthropicConnectorError(
                        "http_error",
                        f"Anthropic returned HTTP {status}",
                        status_code=status,
                        retriable=status >= 500,
                        details={"body": body.decode("utf-8", errors="ignore")[:500]},
                    )
                return body
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="ignore") if hasattr(exc, "read") else ""
            status_code = int(exc.code)
            raise AnthropicConnectorError(
                "http_error",
                f"Anthropic returned HTTP {status_code}",
                status_code=status_code,
                retriable=status_code >= 500,
                details={"body": raw[:500]},
            ) from exc
        except urllib.error.URLError as exc:
            raise AnthropicConnectorError(
                "network_error",
                "Could not reach Anthropic API",
                retriable=True,
                details={"reason": str(exc.reason)},
            ) from exc
        except TimeoutError as exc:
            raise AnthropicConnectorError(
                "timeout",
                "Anthropic request timed out",
                retriable=True,
                details={"timeout_seconds": self.timeout_seconds},
            ) from exc

    def _build_messages(self, prompt: str, history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for item in history:
            role = str(item.get("role") or "").strip().lower()
            content = str(item.get("content") or "").strip()
            if role not in {"user", "assistant"} or not content:
                continue
            out.append({"role": role, "content": content})
        out.append({"role": "user", "content": prompt})
        return out

    def _build_server_defs(self, servers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        used_names = set()

        for index, server in enumerate(servers):
            if not isinstance(server, dict):
                continue
            if not bool(server.get("enabled", True)):
                continue

            endpoint = str(server.get("endpoint") or "").strip()
            parsed = urllib.parse.urlparse(endpoint)
            if parsed.scheme != "https":
                raise AnthropicConnectorError(
                    "invalid_mcp_url",
                    "Anthropic MCP connector requires HTTPS MCP server URLs",
                    retriable=False,
                    details={"endpoint": endpoint},
                )

            raw_name = str(server.get("name") or "server").strip()
            safe_name = self._safe_server_name(raw_name, index)
            candidate = safe_name
            counter = 2
            while candidate in used_names:
                candidate = f"{safe_name}-{counter}"
                counter += 1
            used_names.add(candidate)

            server_def: Dict[str, Any] = {
                "type": "url",
                "url": endpoint,
                "name": candidate,
            }

            headers = server.get("headers") if isinstance(server.get("headers"), dict) else {}
            auth_token = self._extract_authorization_token(headers)
            if auth_token:
                server_def["authorization_token"] = auth_token

            out.append(server_def)

        return out

    @staticmethod
    def _safe_server_name(raw_name: str, index: int) -> str:
        lowered = raw_name.strip().lower()
        safe = re.sub(r"[^a-z0-9_-]+", "-", lowered).strip("-")
        if not safe:
            safe = f"mcp-server-{index + 1}"
        return safe[:64]

    @staticmethod
    def _extract_authorization_token(headers: Dict[str, Any]) -> str:
        token = ""
        for key, value in headers.items():
            k = str(key).strip().lower()
            if k == "authorization":
                token = str(value).strip()
                break
        if not token:
            return ""
        if token.lower().startswith("bearer "):
            return token[7:].strip()
        return token

    @staticmethod
    def _extract_text_content(content_blocks: List[Dict[str, Any]]) -> str:
        parts: List[str] = []
        for block in content_blocks:
            if not isinstance(block, dict):
                continue
            if str(block.get("type") or "").strip() != "text":
                continue
            text = str(block.get("text") or "").strip()
            if text:
                parts.append(text)
        return "\n\n".join(parts).strip()

    @staticmethod
    def _extract_tool_events(content_blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []
        for block in content_blocks:
            if not isinstance(block, dict):
                continue
            block_type = str(block.get("type") or "").strip()
            if block_type == "mcp_tool_use":
                tool_name = str(block.get("name") or "").strip()
                input_payload = block.get("input") if isinstance(block.get("input"), dict) else {}
                events.append(
                    {
                        "type": "mcp_tool_use",
                        "tool_name": tool_name,
                        "server_name": str(block.get("server_name") or "").strip(),
                        "tool_use_id": str(block.get("id") or "").strip(),
                        "input": input_payload,
                    }
                )
                continue
            if block_type == "mcp_tool_result":
                content_items = block.get("content") if isinstance(block.get("content"), list) else []
                text_preview = ""
                for item in content_items:
                    if not isinstance(item, dict):
                        continue
                    if str(item.get("type") or "").strip() != "text":
                        continue
                    text_preview = str(item.get("text") or "").strip()
                    if text_preview:
                        break
                events.append(
                    {
                        "type": "mcp_tool_result",
                        "tool_use_id": str(block.get("tool_use_id") or "").strip(),
                        "is_error": bool(block.get("is_error")),
                        "text_preview": text_preview[:220],
                    }
                )
        return events
