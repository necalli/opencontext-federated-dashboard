from __future__ import annotations

import os
from typing import Any, Dict, List

from .anthropic_agent_sdk_runtime import AgentSDKRuntimeError, AnthropicAgentSDKRuntime
from .anthropic_mcp_connector import AnthropicConnectorError, AnthropicMCPConnectorRuntime
from .opencontext_mcp_client import MCPClientError, OpenContextMCPClient
from .skill_packages import tool_allowed


class DeterministicMCPRuntime:
    def run(
        self,
        *,
        message: str,
        servers: List[Dict[str, Any]],
        allowed_tool_patterns: List[str] | None = None,
    ) -> Dict[str, Any]:
        allowed_patterns = [str(item).strip() for item in (allowed_tool_patterns or []) if str(item).strip()]
        active = [row for row in servers if isinstance(row, dict) and bool(row.get("enabled", True))]
        if not active:
            return {
                "text": (
                    "No enabled MCP servers are registered. Add at least one OpenContext endpoint "
                    "in the server registry."
                ),
                "server_results": [],
            }

        summaries: List[str] = []
        server_results: List[Dict[str, Any]] = []

        for server in active:
            name = str(server.get("name") or "Unnamed MCP Server")
            endpoint = str(server.get("endpoint") or "")
            headers = server.get("headers") if isinstance(server.get("headers"), dict) else {}

            try:
                client = OpenContextMCPClient(endpoint, headers=headers)
                ping = client.ping()
                tools = client.tools_list()
                tools_payload = tools.result if isinstance(tools.result, dict) else {}
                tool_rows = tools_payload.get("tools") if isinstance(tools_payload.get("tools"), list) else []
                tool_names = []
                for item in tool_rows:
                    if not isinstance(item, dict):
                        continue
                    tool_name = str(item.get("name") or "").strip()
                    if tool_name:
                        tool_names.append(tool_name)
                filtered_tool_names = [
                    tool_name for tool_name in tool_names if tool_allowed(tool_name, allowed_patterns)
                ]

                if allowed_patterns:
                    summaries.append(
                        f"{name}: reachable, {len(filtered_tool_names)} in-scope tools ({len(tool_names)} total)"
                    )
                else:
                    summaries.append(f"{name}: reachable, {len(tool_names)} tools")
                server_results.append(
                    {
                        "server_id": server.get("id"),
                        "name": name,
                        "endpoint": endpoint,
                        "ok": True,
                        "ping_status": ping.result,
                        "tool_count": len(filtered_tool_names if allowed_patterns else tool_names),
                        "tools_total": len(tool_names),
                        "tools": filtered_tool_names if allowed_patterns else tool_names,
                        "skill_scope_applied": bool(allowed_patterns),
                    }
                )
            except MCPClientError as exc:
                summaries.append(f"{name}: error ({exc.code})")
                server_results.append(
                    {
                        "server_id": server.get("id"),
                        "name": name,
                        "endpoint": endpoint,
                        "ok": False,
                        "error": exc.to_dict(),
                    }
                )

        available = [row for row in server_results if row.get("ok")]
        failed = [row for row in server_results if not row.get("ok")]
        summary_line = ", ".join(summaries) if summaries else "No server data"

        scope_note = ""
        if allowed_patterns:
            scope_note = f" Skill scope active ({len(allowed_patterns)} tool pattern(s))."

        text = (
            "Deterministic fallback runtime is active. "
            f"Reachable servers: {len(available)}. Failed servers: {len(failed)}. "
            f"Summary: {summary_line}. "
            f"{scope_note}"
            f"User request: {message.strip()}"
        )

        return {
            "text": text,
            "server_results": server_results,
        }


class AgentRuntime:
    def __init__(
        self,
        agent_sdk_runtime: AnthropicAgentSDKRuntime,
        connector_runtime: AnthropicMCPConnectorRuntime,
        deterministic_runtime: DeterministicMCPRuntime,
    ) -> None:
        self.agent_sdk_runtime = agent_sdk_runtime
        self.connector_runtime = connector_runtime
        self.deterministic_runtime = deterministic_runtime
        self.primary_runtime = str(os.getenv("AGENT_RUNTIME_PRIMARY", "agent_sdk")).strip().lower()

    @staticmethod
    def _resolve_runtime_order(
        *,
        primary_runtime: str,
        prefer_connector: bool,
        runtime_preference: str = "",
    ) -> List[str]:
        preferred = str(runtime_preference or "").strip().lower()
        if not preferred:
            preferred = primary_runtime if prefer_connector else "deterministic_mcp"

        if preferred in {"agent_sdk", "anthropic_agent_sdk"}:
            return ["agent_sdk", "anthropic_mcp_connector", "deterministic_mcp"]
        if preferred in {"connector", "anthropic_mcp_connector", "messages_api"}:
            return ["anthropic_mcp_connector", "agent_sdk", "deterministic_mcp"]
        if preferred in {"deterministic", "deterministic_mcp"}:
            return ["deterministic_mcp"]
        if preferred in {"auto"}:
            return AgentRuntime._resolve_runtime_order(
                primary_runtime=primary_runtime,
                prefer_connector=True,
                runtime_preference="",
            )
        return ["agent_sdk", "anthropic_mcp_connector", "deterministic_mcp"]

    @staticmethod
    def _skill_policy_violations(tool_events: List[Dict[str, Any]], allowed_patterns: List[str]) -> List[str]:
        if not allowed_patterns:
            return []
        violations: List[str] = []
        for event in tool_events:
            if not isinstance(event, dict):
                continue
            if str(event.get("type") or "").strip() != "mcp_tool_use":
                continue
            tool_name = str(event.get("tool_name") or "").strip()
            if tool_name and not tool_allowed(tool_name, allowed_patterns):
                violations.append(tool_name)
        deduped: List[str] = []
        seen = set()
        for item in violations:
            if item in seen:
                continue
            seen.add(item)
            deduped.append(item)
        return deduped

    @staticmethod
    def _visualization_requested(message: str) -> bool:
        text = str(message or "").strip().lower()
        if not text:
            return False
        keywords = (
            "visualize",
            "visualization",
            "chart",
            "graph",
            "plot",
            "line chart",
            "bar chart",
            "trend chart",
            "canvas",
            "dashboard",
        )
        return any(token in text for token in keywords)

    def run(
        self,
        *,
        message: str,
        servers: List[Dict[str, Any]],
        history: List[Dict[str, Any]],
        session_id: str = "",
        prefer_connector: bool,
        system_prompt: str = "",
        skill_context: Dict[str, Any] | None = None,
        runtime_preference: str = "",
        event_sink: Any | None = None,
    ) -> Dict[str, Any]:
        scoped = skill_context if isinstance(skill_context, dict) else {}
        selected_skill_ids = (
            scoped.get("selected_skill_ids") if isinstance(scoped.get("selected_skill_ids"), list) else []
        )
        selected_skill_titles = (
            scoped.get("selected_skill_titles") if isinstance(scoped.get("selected_skill_titles"), list) else []
        )
        allowed_tool_patterns = (
            scoped.get("allowed_tool_patterns") if isinstance(scoped.get("allowed_tool_patterns"), list) else []
        )
        prompt_addendum = str(scoped.get("system_prompt_addendum") or "").strip()
        effective_system_prompt = str(system_prompt or "").strip()
        if prompt_addendum:
            effective_system_prompt = (
                f"{effective_system_prompt}\n\n{prompt_addendum}".strip() if effective_system_prompt else prompt_addendum
            )

        runtime_order = self._resolve_runtime_order(
            primary_runtime=self.primary_runtime,
            prefer_connector=prefer_connector,
            runtime_preference=runtime_preference,
        )
        debug: Dict[str, Any] = {
            "preferred_runtime": runtime_order[0] if runtime_order else "deterministic_mcp",
            "runtime_order": list(runtime_order),
            "server_count": len(servers),
            "errors": [],
            "warnings": [],
            "skills": {
                "selected_skill_ids": selected_skill_ids,
                "selected_skill_titles": selected_skill_titles,
                "allowed_tool_patterns": allowed_tool_patterns,
            },
        }

        first_error: Dict[str, Any] | None = None
        for runtime_name in runtime_order:
            if runtime_name == "agent_sdk":
                try:
                    sdk_result = self.agent_sdk_runtime.generate(
                        message=message,
                        mcp_servers=servers,
                        history=history,
                        session_id=session_id,
                        system_prompt=effective_system_prompt,
                        skill_context=scoped,
                        event_sink=event_sink,
                    )
                    text = str(sdk_result.get("text") or "").strip()
                    if not text:
                        text = "Agent SDK response completed with no text output."
                        debug["warnings"].append("agent_sdk_empty_text")

                    tool_events = sdk_result.get("tool_events") if isinstance(sdk_result.get("tool_events"), list) else []
                    policy_violations = self._skill_policy_violations(tool_events, allowed_tool_patterns)
                    if policy_violations:
                        debug["warnings"].append("skill_scope_policy_violation")

                    debug["agent_sdk"] = {
                        "response_id": sdk_result.get("response_id"),
                        "stop_reason": sdk_result.get("stop_reason"),
                        "usage": sdk_result.get("usage"),
                        "server_names": sdk_result.get("server_names"),
                        "tool_events": tool_events,
                        "visualizations": (
                            sdk_result.get("visualizations")
                            if isinstance(sdk_result.get("visualizations"), list)
                            else []
                        ),
                        "policy_violations": policy_violations,
                        "sdk_meta": sdk_result.get("sdk_meta") if isinstance(sdk_result.get("sdk_meta"), dict) else {},
                    }
                    if self._visualization_requested(message):
                        visualizations = debug["agent_sdk"].get("visualizations") or []
                        if not isinstance(visualizations, list) or not visualizations:
                            debug["warnings"].append("visualization_requested_but_not_published")

                    return {
                        "message": text,
                        "meta": {
                            "runtime": "anthropic_agent_sdk",
                            "fallback_used": False,
                            "fallback_reason": None,
                            "debug": debug,
                        },
                    }
                except AgentSDKRuntimeError as exc:
                    error_payload = exc.to_dict()
                    debug["errors"].append(error_payload)
                    if first_error is None:
                        first_error = error_payload
                    continue

            if runtime_name == "anthropic_mcp_connector":
                try:
                    connector_result = self.connector_runtime.generate(
                        message=message,
                        mcp_servers=servers,
                        history=history,
                        system_prompt=effective_system_prompt,
                    )
                    text = str(connector_result.get("text") or "").strip()
                    if not text:
                        text = "Connector response completed with no text output."
                        debug["warnings"].append("connector_empty_text")

                    tool_events = (
                        connector_result.get("tool_events")
                        if isinstance(connector_result.get("tool_events"), list)
                        else []
                    )
                    policy_violations = self._skill_policy_violations(tool_events, allowed_tool_patterns)
                    if policy_violations:
                        debug["warnings"].append("skill_scope_policy_violation")

                    debug["connector"] = {
                        "response_id": connector_result.get("response_id"),
                        "stop_reason": connector_result.get("stop_reason"),
                        "usage": connector_result.get("usage"),
                        "server_names": connector_result.get("server_names"),
                        "tool_events": tool_events,
                        "policy_violations": policy_violations,
                    }

                    if first_error is None:
                        fallback_reason = None
                        fallback_used = False
                    else:
                        fallback_reason = first_error
                        fallback_used = True

                    return {
                        "message": text,
                        "meta": {
                            "runtime": "anthropic_mcp_connector",
                            "fallback_used": fallback_used,
                            "fallback_reason": fallback_reason,
                            "debug": debug,
                        },
                    }
                except AnthropicConnectorError as exc:
                    error_payload = exc.to_dict()
                    debug["errors"].append(error_payload)
                    if first_error is None:
                        first_error = error_payload
                    continue

            if runtime_name == "deterministic_mcp":
                fallback = self.deterministic_runtime.run(
                    message=message,
                    servers=servers,
                    allowed_tool_patterns=allowed_tool_patterns,
                )
                if first_error is None:
                    runtime_label = "deterministic_mcp"
                    fallback_used = False
                    fallback_reason = None
                else:
                    runtime_label = "deterministic_mcp_fallback"
                    fallback_used = True
                    fallback_reason = first_error
                return {
                    "message": fallback.get("text") or "Deterministic runtime completed.",
                    "meta": {
                        "runtime": runtime_label,
                        "fallback_used": fallback_used,
                        "fallback_reason": fallback_reason,
                        "debug": {
                            **debug,
                            "deterministic": {
                                "server_results": fallback.get("server_results", []),
                            },
                        },
                    },
                }

        fallback = self.deterministic_runtime.run(
            message=message,
            servers=servers,
            allowed_tool_patterns=allowed_tool_patterns,
        )
        return {
            "message": fallback.get("text") or "Deterministic fallback completed.",
            "meta": {
                "runtime": "deterministic_mcp_fallback",
                "fallback_used": True,
                "fallback_reason": first_error
                or {"code": "runtime_error", "message": "No runtime path succeeded", "details": {}},
                "debug": {
                    **debug,
                    "deterministic": {
                        "server_results": fallback.get("server_results", []),
                    },
                },
            },
        }
