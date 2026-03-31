from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Callable, Dict, List, Set, Tuple

from .opencontext_mcp_client import MCPClientError, OpenContextMCPClient


class ToolRouterError(Exception):
    def __init__(self, code: str, message: str, *, details: Dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def to_dict(self) -> Dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}


@dataclass
class RouterCatalog:
    tools: List[Dict[str, Any]]
    errors: List[Dict[str, Any]]
    server_tool_names: Dict[str, Set[str]]
    tool_server_ids: Dict[str, List[str]]


class ToolRouter:
    def __init__(self, client_factory: Callable[..., OpenContextMCPClient] | None = None) -> None:
        self.client_factory = client_factory or OpenContextMCPClient

    def build_catalog(self, servers: List[Dict[str, Any]]) -> RouterCatalog:
        tools_out: List[Dict[str, Any]] = []
        errors: List[Dict[str, Any]] = []
        server_tool_names: Dict[str, Set[str]] = {}

        for server in servers:
            server_id = str(server.get("id") or "").strip()
            server_name = str(server.get("name") or "").strip()
            endpoint = str(server.get("endpoint") or "").strip()
            headers = server.get("headers") if isinstance(server.get("headers"), dict) else {}
            if not server_id or not endpoint:
                continue

            try:
                client = self.client_factory(endpoint, headers=headers)
                client.initialize()
                tools_result = client.tools_list()
                payload = tools_result.result if isinstance(tools_result.result, dict) else {}
                rows = payload.get("tools") if isinstance(payload.get("tools"), list) else []
                names: Set[str] = set()
                for item in rows:
                    if not isinstance(item, dict):
                        continue
                    tool_name = str(item.get("name") or "").strip()
                    if not tool_name:
                        continue
                    names.add(tool_name)
                    tools_out.append(
                        {
                            "server_id": server_id,
                            "server_name": server_name,
                            "name": tool_name,
                            "description": str(item.get("description") or "").strip(),
                            "input_schema": (
                                item.get("inputSchema")
                                if isinstance(item.get("inputSchema"), dict)
                                else {}
                            ),
                        }
                    )
                server_tool_names[server_id] = names
            except MCPClientError as exc:
                server_tool_names[server_id] = set()
                errors.append(
                    {
                        "server_id": server_id,
                        "server_name": server_name,
                        "error": exc.to_dict(),
                    }
                )

        tool_server_ids: Dict[str, List[str]] = {}
        for server_id, names in server_tool_names.items():
            for tool_name in names:
                ids = tool_server_ids.setdefault(tool_name, [])
                if server_id not in ids:
                    ids.append(server_id)

        return RouterCatalog(
            tools=tools_out,
            errors=errors,
            server_tool_names=server_tool_names,
            tool_server_ids=tool_server_ids,
        )

    def route_candidates(
        self,
        *,
        tool_name: str,
        servers: List[Dict[str, Any]],
        preferred_server_id: str = "",
    ) -> Tuple[List[Dict[str, Any]], RouterCatalog]:
        requested_tool = str(tool_name or "").strip()
        if not requested_tool:
            raise ToolRouterError("validation_error", "tool_name is required")

        catalog = self.build_catalog(servers)
        by_id = {str(row.get("id") or "").strip(): row for row in servers}
        preferred = str(preferred_server_id or "").strip()

        if preferred:
            chosen = by_id.get(preferred)
            if not chosen:
                raise ToolRouterError(
                    "not_found",
                    "Preferred server was not found",
                    details={"server_id": preferred},
                )
            known_tools = catalog.server_tool_names.get(preferred, set())
            if known_tools and requested_tool not in known_tools:
                raise ToolRouterError(
                    "tool_not_available",
                    "Selected server does not expose the requested tool",
                    details={
                        "server_id": preferred,
                        "tool_name": requested_tool,
                        "available_tools": sorted(list(known_tools)),
                    },
                )
            return [chosen], catalog

        candidate_ids = catalog.tool_server_ids.get(requested_tool, [])
        if not candidate_ids:
            raise ToolRouterError(
                "tool_not_routable",
                "No enabled server exposes the requested tool",
                details={
                    "tool_name": requested_tool,
                    "available_tool_count": len(catalog.tool_server_ids),
                },
            )

        candidates: List[Dict[str, Any]] = []
        for server_id in candidate_ids:
            row = by_id.get(server_id)
            if row is not None:
                candidates.append(row)
        if not candidates:
            raise ToolRouterError(
                "tool_not_routable",
                "Tool match found but server candidates were unavailable",
                details={"tool_name": requested_tool},
            )
        return candidates, catalog

    def validate_execute_sql_arguments(self, arguments: Dict[str, Any], *, max_rows: int) -> Dict[str, Any]:
        payload = arguments if isinstance(arguments, dict) else {}
        query = str(payload.get("query") or payload.get("sql") or "").strip()
        if not query:
            raise ToolRouterError(
                "validation_error",
                "execute_sql requires a non-empty SQL query in arguments.query or arguments.sql",
            )

        lowered = query.lower().strip()
        if not (lowered.startswith("select") or lowered.startswith("with")):
            raise ToolRouterError(
                "sql_unsafe_query",
                "Only SELECT/CTE SQL statements are allowed",
                details={"hint": "Use SELECT ... FROM ... LIMIT N"},
            )

        limit_match = re.search(r"\blimit\s+(\d+)\b", query, flags=re.IGNORECASE)
        if not limit_match:
            raise ToolRouterError(
                "sql_limit_required",
                "SQL query must include an explicit LIMIT clause",
                details={"max_rows": max_rows},
            )

        try:
            limit_value = int(limit_match.group(1))
        except Exception:
            raise ToolRouterError(
                "sql_limit_invalid",
                "SQL LIMIT must be an integer",
                details={"max_rows": max_rows},
            )

        if limit_value <= 0:
            raise ToolRouterError(
                "sql_limit_invalid",
                "SQL LIMIT must be greater than zero",
                details={"max_rows": max_rows},
            )

        if limit_value > int(max_rows):
            raise ToolRouterError(
                "sql_limit_exceeded",
                "SQL LIMIT exceeds configured safety maximum",
                details={"limit": limit_value, "max_rows": int(max_rows)},
            )

        preview = query if len(query) <= 220 else f"{query[:217]}..."
        return {
            "query_preview": preview,
            "limit": limit_value,
            "max_rows": int(max_rows),
        }
