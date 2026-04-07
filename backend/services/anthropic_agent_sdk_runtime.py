from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import html
import importlib.util
import inspect
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .opencontext_mcp_client import MCPClientError, OpenContextMCPClient
from .server_registry import NotFoundError, RegistryError, ServerRegistryService
from .skill_packages import tool_allowed
from .storage import Storage
from .tool_router import ToolRouter, ToolRouterError

try:
    from claude_agent_sdk import (
        AgentDefinition,
        AssistantMessage as SdkAssistantMessage,
        ClaudeAgentOptions,
        ClaudeSDKClient,
        ResultMessage as SdkResultMessage,
        TextBlock as SdkTextBlock,
        create_sdk_mcp_server,
        tool as sdk_tool,
    )

    AGENT_SDK_AVAILABLE = True
    AGENT_SDK_IMPORT_ERROR = ""
except Exception as exc:  # pragma: no cover - optional dependency
    AgentDefinition = None  # type: ignore[assignment]
    SdkAssistantMessage = None  # type: ignore[assignment]
    ClaudeAgentOptions = None  # type: ignore[assignment]
    ClaudeSDKClient = None  # type: ignore[assignment]
    SdkResultMessage = None  # type: ignore[assignment]
    SdkTextBlock = None  # type: ignore[assignment]
    create_sdk_mcp_server = None  # type: ignore[assignment]
    sdk_tool = None  # type: ignore[assignment]
    AGENT_SDK_AVAILABLE = False
    AGENT_SDK_IMPORT_ERROR = str(exc)


def _to_int(value: Any, fallback: int) -> int:
    try:
        return int(value)
    except Exception:
        return int(fallback)


def _to_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    lowered = str(value).strip().lower()
    if not lowered:
        return bool(default)
    return lowered in {"1", "true", "yes", "y", "on"}


def _parse_csv(value: Any) -> List[str]:
    text = str(value or "").strip()
    if not text:
        return []
    out: List[str] = []
    seen = set()
    for part in text.split(","):
        token = str(part or "").strip()
        if not token or token in seen:
            continue
        seen.add(token)
        out.append(token)
    return out


MCP_ONBOARDING_TOOL_NAMES: Tuple[str, ...] = (
    "mcp_server_discover",
    "mcp_server_onboard",
    "mcp_servers_list",
    "mcp_server_upsert",
    "mcp_server_test",
    "mcp_tools_list_by_server",
    "mcp_server_disable",
    "mcp_stdio_bridge_plan",
    "mcp_stdio_bridge_start",
    "mcp_stdio_bridge_status",
    "mcp_stdio_bridge_stop",
)


class AgentSDKRuntimeError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: Dict[str, Any] | None = None,
        retriable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}
        self.retriable = retriable

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "retriable": self.retriable,
            "details": self.details,
        }


class AnthropicAgentSDKRuntime:
    def __init__(
        self,
        *,
        client_factory: Any | None = None,
        tool_router: ToolRouter | None = None,
        server_registry: ServerRegistryService | None = None,
    ) -> None:
        self.api_key = str(os.getenv("ANTHROPIC_API_KEY", "")).strip()
        self.model = str(os.getenv("AGENT_SDK_MODEL", os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6"))).strip()
        self.max_turns = max(1, _to_int(os.getenv("AGENT_SDK_MAX_TURNS"), 12))
        self.permission_mode = str(os.getenv("AGENT_SDK_PERMISSION_MODE", "default")).strip() or "default"
        self.server_alias = str(os.getenv("AGENT_SDK_SERVER_ALIAS", "opencontext")).strip() or "opencontext"
        self.setting_sources = _parse_csv(os.getenv("AGENT_SDK_SETTING_SOURCES", ""))
        self.subagent_model = str(os.getenv("AGENT_SDK_SUBAGENT_MODEL", self.model)).strip()
        self.prompt_history_fallback_enabled = _to_bool(
            os.getenv("AGENT_SDK_PROMPT_HISTORY_FALLBACK_ENABLED", "false"),
            False,
        )
        self.history_turn_limit = max(2, _to_int(os.getenv("AGENT_SDK_HISTORY_TURNS"), 8))
        self.history_char_limit = max(1200, _to_int(os.getenv("AGENT_SDK_HISTORY_CHARS"), 6000))
        self.native_skills_enabled = _to_bool(os.getenv("AGENT_SDK_NATIVE_SKILLS_ENABLED", "false"), False)
        self.visualization_tool_enabled = _to_bool(
            os.getenv("AGENT_SDK_VISUALIZATION_TOOL_ENABLED", "true"),
            True,
        )
        self.auto_approve_builtins_enabled = _to_bool(
            os.getenv("AGENT_SDK_AUTO_APPROVE_BUILTINS_ENABLED", "false"),
            False,
        )
        self.auto_approve_builtins = _parse_csv(os.getenv("AGENT_SDK_AUTO_APPROVE_BUILTINS", ""))
        self.auto_approve_tools_extra = _parse_csv(os.getenv("AGENT_SDK_AUTO_APPROVE_TOOLS", ""))
        self.duplicate_tool_alias_enabled = _to_bool(
            os.getenv("AGENT_SDK_DUPLICATE_TOOL_ALIAS_ENABLED", "true"),
            True,
        )
        self.mcp_onboarding_enabled = _to_bool(
            os.getenv("AGENT_SDK_MCP_ONBOARDING_ENABLED", "true"),
            True,
        )
        self.mcp_discovery_enabled = _to_bool(
            os.getenv("AGENT_SDK_MCP_DISCOVERY_ENABLED", "true"),
            True,
        )
        self.mcp_discovery_timeout_seconds = max(
            1.0,
            float(int(os.getenv("AGENT_SDK_MCP_DISCOVERY_TIMEOUT_MS", "8000")) / 1000.0),
        )
        self.mcp_discovery_confirm_required = _to_bool(
            os.getenv("AGENT_SDK_MCP_ONBOARD_CONFIRM_REQUIRED", "true"),
            True,
        )
        disallowed_default = (
            "ToolSearch,AskUserQuestion,WebFetch,WebSearch,TodoWrite,NotebookEdit,"
            "TaskOutput,TaskStop,CronCreate,CronDelete,CronList,EnterPlanMode,"
            "ExitPlanMode,EnterWorktree,Bash,Read,Write,Edit,Glob,Grep,NotebookEdit"
        )
        self.disallowed_tools = _parse_csv(os.getenv("AGENT_SDK_DISALLOWED_TOOLS", disallowed_default))
        self.project_root = Path(__file__).resolve().parents[2]
        self.skill_source_dir = Path(
            str(os.getenv("AGENT_SKILLS_DIR", str(self.project_root / "backend" / "agent_skills"))).strip()
        )
        self.native_skill_target_dir = self.project_root / ".claude" / "skills"
        self.bridge_runtime_dir = Path(
            str(os.getenv("AGENT_SDK_BRIDGE_RUNTIME_DIR", str(self.project_root / ".runtime_bridges"))).strip()
        )
        self._session_map: Dict[str, str] = {}
        self._bridge_processes: Dict[str, Dict[str, Any]] = {}

        self.client_factory = client_factory or OpenContextMCPClient
        self.tool_router = tool_router or ToolRouter(client_factory=self.client_factory)
        self.server_registry = server_registry

    def generate(
        self,
        *,
        message: str,
        mcp_servers: List[Dict[str, Any]],
        history: Optional[List[Dict[str, Any]]] = None,
        session_id: str = "",
        system_prompt: str = "",
        skill_context: Optional[Dict[str, Any]] = None,
        event_sink: Any | None = None,
    ) -> Dict[str, Any]:
        prompt = str(message or "").strip()
        if not prompt:
            raise AgentSDKRuntimeError("validation_error", "message is required", retriable=False)
        if not self.api_key:
            raise AgentSDKRuntimeError("missing_api_key", "ANTHROPIC_API_KEY is not configured", retriable=False)
        if not AGENT_SDK_AVAILABLE or ClaudeAgentOptions is None or ClaudeSDKClient is None:
            raise AgentSDKRuntimeError(
                "agent_sdk_unavailable",
                "claude_agent_sdk is not installed",
                retriable=False,
                details={"import_error": AGENT_SDK_IMPORT_ERROR},
            )
        if create_sdk_mcp_server is None or sdk_tool is None:
            raise AgentSDKRuntimeError(
                "agent_sdk_unavailable",
                "Required Agent SDK tool APIs are unavailable in this environment",
                retriable=False,
            )

        active_servers = [row for row in mcp_servers if isinstance(row, dict) and bool(row.get("enabled", True))]

        scoped = skill_context if isinstance(skill_context, dict) else {}
        allowed_patterns = (
            scoped.get("allowed_tool_patterns") if isinstance(scoped.get("allowed_tool_patterns"), list) else []
        )
        selected_skills = scoped.get("selected_skills") if isinstance(scoped.get("selected_skills"), list) else []
        visualization_requested = self._is_visualization_request(prompt)
        onboarding_tool_names = self._allowed_onboarding_tool_names(allowed_patterns)

        if not active_servers and not onboarding_tool_names:
            raise AgentSDKRuntimeError(
                "no_servers",
                "No enabled MCP servers are available for Agent SDK runtime",
                retriable=False,
            )

        catalog = self.tool_router.build_catalog(active_servers)
        available_tools = self._catalog_tools_for_sdk(catalog.tools, allowed_patterns)
        if not available_tools and not onboarding_tool_names:
            raise AgentSDKRuntimeError(
                "no_tools",
                "No MCP tools are available for Agent SDK execution",
                retriable=False,
                details={
                    "server_count": len(active_servers),
                    "catalog_error_count": len(catalog.errors),
                    "skill_scope_applied": bool(allowed_patterns),
                },
            )

        tool_events: List[Dict[str, Any]] = []
        builtin_tool_events: List[Dict[str, Any]] = []
        visualization_artifacts: List[Dict[str, Any]] = []
        wrapped_tools, allowed_tool_names, internal_to_allowed = self._build_wrapped_tools(
            available_tools=available_tools,
            active_servers=active_servers,
            tool_events=tool_events,
            event_sink=event_sink,
        )
        if self._visualization_tool_allowed(allowed_patterns, force=visualization_requested):
            wrapped_tools.append(
                self._build_visualization_tool(
                    visualization_artifacts=visualization_artifacts,
                    event_sink=event_sink,
                )
            )
            viz_allowed_name = f"mcp__{self.server_alias}__create_visualization"
            if viz_allowed_name not in allowed_tool_names:
                allowed_tool_names.append(viz_allowed_name)
            internal_to_allowed.setdefault("create_visualization", [])
            if viz_allowed_name not in internal_to_allowed["create_visualization"]:
                internal_to_allowed["create_visualization"].append(viz_allowed_name)

        for onboarding_tool_name in onboarding_tool_names:
            onboarding_tool = self._build_mcp_onboarding_tool(
                tool_name=onboarding_tool_name,
                event_sink=event_sink,
            )
            wrapped_tools.append(
                self._instrument_onboarding_tool(
                    tool=onboarding_tool,
                    tool_events=tool_events,
                    event_sink=event_sink,
                )
            )
            allowed_name = f"mcp__{self.server_alias}__{onboarding_tool_name}"
            if allowed_name not in allowed_tool_names:
                allowed_tool_names.append(allowed_name)
            internal_to_allowed.setdefault(onboarding_tool_name, [])
            if allowed_name not in internal_to_allowed[onboarding_tool_name]:
                internal_to_allowed[onboarding_tool_name].append(allowed_name)

        if not wrapped_tools:
            raise AgentSDKRuntimeError("no_tools", "No tools could be wrapped for Agent SDK runtime", retriable=False)

        subagents = self._build_subagents(
            selected_skills=selected_skills,
            internal_to_allowed=internal_to_allowed,
        )
        self._sync_native_skills()
        app_session_id = str(session_id or "").strip()
        prior_sdk_session = self._session_map.get(app_session_id, "") if app_session_id else ""
        supported_option_keys = self._supported_option_keys()

        options_kwargs: Dict[str, Any] = {
            "model": self.model,
            "max_turns": self.max_turns,
            "permission_mode": self.permission_mode,
            "cwd": str(self.project_root),
            "env": {"ANTHROPIC_API_KEY": self.api_key},
            "mcp_servers": {
                self.server_alias: create_sdk_mcp_server(
                    name="opencontext-federated-tools",
                    version="1.0.0",
                    tools=wrapped_tools,
                )
            },
            "allowed_tools": list(allowed_tool_names),
        }
        self._apply_continue_conversation_option(
            options_kwargs=options_kwargs,
            supported_keys=supported_option_keys,
        )

        if self.auto_approve_builtins_enabled:
            for tool_name in self.auto_approve_builtins:
                value = str(tool_name or "").strip()
                if value and value not in options_kwargs["allowed_tools"]:
                    options_kwargs["allowed_tools"].append(value)
        for tool_name in self.auto_approve_tools_extra:
            value = str(tool_name or "").strip()
            if value and value not in options_kwargs["allowed_tools"]:
                options_kwargs["allowed_tools"].append(value)

        if self.native_skills_enabled and "Skill" not in options_kwargs["allowed_tools"]:
            options_kwargs["allowed_tools"].append("Skill")
        if subagents:
            options_kwargs["agents"] = subagents
            if "Task" not in options_kwargs["allowed_tools"]:
                options_kwargs["allowed_tools"].append("Task")
        if self.setting_sources:
            options_kwargs["setting_sources"] = list(self.setting_sources)
        if self.disallowed_tools:
            options_kwargs["disallowed_tools"] = [
                item
                for item in self.disallowed_tools
                if item and item not in options_kwargs["allowed_tools"]
            ]
        if visualization_requested and self.visualization_tool_enabled:
            disallow_for_viz = {"Write", "Edit", "Bash"}
            options_kwargs["allowed_tools"] = [
                item for item in options_kwargs["allowed_tools"] if item not in disallow_for_viz
            ]
            existing_disallowed = set(options_kwargs.get("disallowed_tools") or [])
            for item in disallow_for_viz:
                if item not in existing_disallowed:
                    existing_disallowed.add(item)
            options_kwargs["disallowed_tools"] = sorted(existing_disallowed)
        if str(system_prompt or "").strip():
            options_kwargs["system_prompt"] = str(system_prompt).strip()
        if visualization_requested and self.visualization_tool_enabled:
            viz_directive = (
                "Visualization directive: When the user asks for a chart/graph/visualization, "
                "you must call create_visualization with chart_type, title, and records before your final answer. "
                "For geospatial asks, publish chart_type=map with lat_key/lon_key, optional chart_options.map_mode (points or heatmap), "
                "and chart_options.basemap='osm' unless user requests otherwise. "
                "Do not write chart files; publish to the dashboard canvas via create_visualization."
            )
            if options_kwargs.get("system_prompt"):
                options_kwargs["system_prompt"] = f"{options_kwargs['system_prompt']}\n\n{viz_directive}".strip()
            else:
                options_kwargs["system_prompt"] = viz_directive
        resume_applied = self._apply_resume_option(
            options_kwargs=options_kwargs,
            supported_keys=supported_option_keys,
            sdk_session_id=prior_sdk_session,
        )

        filtered_options = self._filter_supported_options(options_kwargs)
        options = ClaudeAgentOptions(**filtered_options)
        effective_message = prompt
        history_fallback_used = False
        if self.prompt_history_fallback_enabled and history and not resume_applied:
            effective_message = self._build_contextual_prompt(message=prompt, history=history)
            history_fallback_used = True

        run_result = self._run_sdk_query(
            options=options,
            message=effective_message,
            app_session_id=app_session_id,
            prior_sdk_session=prior_sdk_session,
            builtin_tool_events=builtin_tool_events,
            event_sink=event_sink,
        )
        sdk_meta = run_result.get("sdk_meta") if isinstance(run_result.get("sdk_meta"), dict) else {}
        sdk_session_id = str(sdk_meta.get("session_id") or run_result.get("response_id") or "").strip()
        if app_session_id and sdk_session_id:
            self._session_map[app_session_id] = sdk_session_id
        sdk_meta["app_session_id"] = app_session_id
        sdk_meta["resume_applied"] = bool(resume_applied)
        sdk_meta["history_fallback_used"] = bool(history_fallback_used)
        if prior_sdk_session:
            sdk_meta["resume_source_session_id"] = prior_sdk_session

        return {
            "text": run_result["text"],
            "response_id": run_result.get("response_id"),
            "stop_reason": run_result.get("stop_reason"),
            "usage": run_result.get("usage", {}),
            "server_names": [str(row.get("name") or "").strip() for row in active_servers],
            "tool_events": tool_events,
            "builtin_tool_events": builtin_tool_events,
            "mcp_init_status": (
                run_result.get("mcp_init_status")
                if isinstance(run_result.get("mcp_init_status"), list)
                else []
            ),
            "visualizations": visualization_artifacts,
            "sdk_meta": sdk_meta,
        }

    def _catalog_tools_for_sdk(
        self,
        tools: List[Dict[str, Any]],
        allowed_patterns: List[str],
    ) -> List[Dict[str, Any]]:
        normalized: List[Dict[str, Any]] = []
        for item in tools:
            if not isinstance(item, dict):
                continue
            tool_name = str(item.get("name") or "").strip()
            if not tool_name:
                continue
            if allowed_patterns and not tool_allowed(tool_name, allowed_patterns):
                continue
            normalized.append(
                {
                    "sdk_name": tool_name,
                    "internal_tool_name": tool_name,
                    "server_id": str(item.get("server_id") or "").strip(),
                    "server_name": str(item.get("server_name") or "").strip(),
                    "description": str(item.get("description") or "").strip(),
                    "input_schema": item.get("input_schema") if isinstance(item.get("input_schema"), dict) else {},
                }
            )

        if not self.duplicate_tool_alias_enabled:
            deduped: Dict[str, Dict[str, Any]] = {}
            for row in normalized:
                tool_name = str(row.get("internal_tool_name") or "").strip()
                if tool_name and tool_name not in deduped:
                    deduped[tool_name] = row
            return list(deduped.values())

        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for row in normalized:
            tool_name = str(row.get("internal_tool_name") or "").strip()
            if not tool_name:
                continue
            grouped.setdefault(tool_name, []).append(row)

        output: List[Dict[str, Any]] = []
        for tool_name in sorted(grouped.keys()):
            group = grouped.get(tool_name) or []
            if len(group) <= 1:
                if group:
                    output.append(group[0])
                continue

            used_sdk_names: set[str] = set()
            for index, row in enumerate(group, start=1):
                server_hint = (
                    str(row.get("server_name") or "").strip()
                    or str(row.get("server_id") or "").strip()
                    or f"server_{index}"
                )
                slug = re.sub(r"[^a-z0-9]+", "_", server_hint.lower()).strip("_")
                if not slug:
                    slug = f"server_{index}"
                slug = slug[:48]

                sdk_name = f"{tool_name}__{slug}"
                counter = 2
                while sdk_name in used_sdk_names:
                    sdk_name = f"{tool_name}__{slug}_{counter}"
                    counter += 1
                used_sdk_names.add(sdk_name)

                aliased = dict(row)
                aliased["sdk_name"] = sdk_name
                output.append(aliased)

        output.sort(key=lambda row: str(row.get("sdk_name") or "").strip())
        return output

    def _build_wrapped_tools(
        self,
        *,
        available_tools: List[Dict[str, Any]],
        active_servers: List[Dict[str, Any]],
        tool_events: List[Dict[str, Any]],
        event_sink: Any | None = None,
    ) -> Tuple[List[Any], List[str], Dict[str, List[str]]]:
        wrapped: List[Any] = []
        allowed_tools: List[str] = []
        internal_to_allowed: Dict[str, List[str]] = {}

        for tool_def in available_tools:
            sdk_name = str(tool_def.get("sdk_name") or "").strip()
            internal_tool_name = str(tool_def.get("internal_tool_name") or "").strip()
            if not sdk_name or not internal_tool_name:
                continue

            description = str(tool_def.get("description") or "").strip() or f"Execute {internal_tool_name}"
            source_server = str(tool_def.get("server_name") or "").strip()
            if source_server:
                description = f"[Source server: {source_server}] {description}".strip()
            if internal_tool_name == "get_data" and source_server:
                lowered = source_server.lower()
                if "nys" in lowered or "new york state" in lowered or "mta" in lowered:
                    description = (
                        f"{description} Use this for New York State Socrata retrieval from data.ny.gov, "
                        "including statewide datasets and MTA-published tables."
                    ).strip()
                elif "nyc" in lowered or "opengov" in lowered or "socrata" in lowered:
                    description = (
                        f"{description} Use this for NYC / New York Socrata data retrieval, "
                        "including table lookups and query constraints."
                    ).strip()
            input_schema = tool_def.get("input_schema") if isinstance(tool_def.get("input_schema"), dict) else {}
            if not input_schema:
                input_schema = {"type": "object", "properties": {}}

            wrapped.append(
                self._tool_factory(
                    sdk_name=sdk_name,
                    internal_tool_name=internal_tool_name,
                    preferred_server_id=(
                        str(tool_def.get("server_id") or "").strip()
                        if self.duplicate_tool_alias_enabled
                        else ""
                    ),
                    description=description,
                    input_schema=input_schema,
                    active_servers=active_servers,
                    tool_events=tool_events,
                    event_sink=event_sink,
                )
            )

            allowed_name = f"mcp__{self.server_alias}__{sdk_name}"
            allowed_tools.append(allowed_name)
            internal_to_allowed.setdefault(internal_tool_name, [])
            if allowed_name not in internal_to_allowed[internal_tool_name]:
                internal_to_allowed[internal_tool_name].append(allowed_name)
            internal_to_allowed.setdefault(sdk_name, [])
            if allowed_name not in internal_to_allowed[sdk_name]:
                internal_to_allowed[sdk_name].append(allowed_name)

        return wrapped, allowed_tools, internal_to_allowed

    def _tool_factory(
        self,
        *,
        sdk_name: str,
        internal_tool_name: str,
        preferred_server_id: str,
        description: str,
        input_schema: Dict[str, Any],
        active_servers: List[Dict[str, Any]],
        tool_events: List[Dict[str, Any]],
        event_sink: Any | None = None,
    ) -> Any:
        @sdk_tool(sdk_name, description, input_schema)
        async def _wrapped(args: Any) -> Dict[str, Any]:
            tool_input = args if isinstance(args, dict) else {}
            tool_use_id = f"sdktool_{uuid.uuid4().hex[:12]}"
            tool_events.append(
                {
                    "type": "mcp_tool_use",
                    "tool_name": internal_tool_name,
                    "server_name": "",
                    "tool_use_id": tool_use_id,
                    "input": dict(tool_input),
                }
            )
            self._emit_event(
                event_sink,
                {
                    "event": "tool_progress",
                    "payload": {
                        "phase": "tool_use",
                        "tool_name": internal_tool_name,
                        "server_name": "",
                        "tool_use_id": tool_use_id,
                    },
                },
            )

            attempts: List[Dict[str, Any]] = []
            try:
                candidates, _ = self.tool_router.route_candidates(
                    tool_name=internal_tool_name,
                    servers=active_servers,
                    preferred_server_id=preferred_server_id,
                )
            except ToolRouterError as exc:
                text = f"Tool routing failed for {internal_tool_name}: {exc.message}"
                tool_events.append(
                    {
                        "type": "mcp_tool_result",
                        "tool_name": internal_tool_name,
                        "tool_use_id": tool_use_id,
                        "is_error": True,
                        "text_preview": text[:220],
                    }
                )
                self._emit_event(
                    event_sink,
                    {
                        "event": "tool_progress",
                        "payload": {
                            "phase": "tool_result",
                            "tool_name": internal_tool_name,
                            "tool_use_id": tool_use_id,
                            "is_error": True,
                            "text_preview": text[:220],
                        },
                    },
                )
                return {"content": [{"type": "text", "text": text}], "is_error": True}

            for server in candidates:
                endpoint = str(server.get("endpoint") or "").strip()
                headers = server.get("headers") if isinstance(server.get("headers"), dict) else {}
                try:
                    client = self.client_factory(endpoint, headers=headers)
                    client.initialize()
                    result = client.tools_call(internal_tool_name, tool_input)
                    text = self._tool_result_text(result.result)
                    tool_events[-1]["server_name"] = str(server.get("name") or "").strip()
                    tool_events.append(
                        {
                            "type": "mcp_tool_result",
                            "tool_name": internal_tool_name,
                            "tool_use_id": tool_use_id,
                            "is_error": False,
                            "text_preview": text[:220],
                        }
                    )
                    self._emit_event(
                        event_sink,
                        {
                            "event": "tool_progress",
                            "payload": {
                                "phase": "tool_result",
                                "tool_name": internal_tool_name,
                                "tool_use_id": tool_use_id,
                                "is_error": False,
                                "text_preview": text[:220],
                            },
                        },
                    )
                    return {"content": [{"type": "text", "text": text}], "is_error": False}
                except MCPClientError as exc:
                    attempts.append(
                        {
                            "server_id": str(server.get("id") or "").strip(),
                            "server_name": str(server.get("name") or "").strip(),
                            "error": exc.to_dict(),
                        }
                    )

            error_text = (
                f"All MCP server candidates failed for {internal_tool_name}. "
                f"Attempts: {json.dumps(attempts, ensure_ascii=True)[:500]}"
            )
            tool_events.append(
                {
                    "type": "mcp_tool_result",
                    "tool_name": internal_tool_name,
                    "tool_use_id": tool_use_id,
                    "is_error": True,
                    "text_preview": error_text[:220],
                }
            )
            self._emit_event(
                event_sink,
                {
                    "event": "tool_progress",
                    "payload": {
                        "phase": "tool_result",
                        "tool_name": internal_tool_name,
                        "tool_use_id": tool_use_id,
                        "is_error": True,
                        "text_preview": error_text[:220],
                    },
                },
            )
            return {"content": [{"type": "text", "text": error_text}], "is_error": True}

        return _wrapped

    def _build_subagents(
        self,
        *,
        selected_skills: List[Dict[str, Any]],
        internal_to_allowed: Dict[str, List[str]],
    ) -> Dict[str, Any]:
        output: Dict[str, Any] = {}
        for item in selected_skills:
            if not isinstance(item, dict):
                continue
            skill_id = str(item.get("skill_id") or "").strip()
            if not skill_id:
                continue
            title = str(item.get("title") or skill_id).strip()
            description = str(item.get("description") or "").strip() or f"Specialist for {title}"
            instruction = str(item.get("instruction") or "").strip()
            tool_names = item.get("tools") if isinstance(item.get("tools"), list) else []
            allowed: List[str] = []
            for name in tool_names:
                mapped = internal_to_allowed.get(str(name or "").strip(), [])
                mapped_values = [mapped] if isinstance(mapped, str) else list(mapped)
                for allowed_name in mapped_values:
                    value = str(allowed_name or "").strip()
                    if value and value not in allowed:
                        allowed.append(value)
            if not allowed:
                continue

            prompt_lines = [
                f"You are the specialist worker for {title}.",
                "Focus on the skill instructions and produce concise, evidence-based outputs.",
            ]
            if instruction:
                prompt_lines.extend(["", "Skill instructions:", instruction])
            definition_kwargs: Dict[str, Any] = {
                "description": description,
                "tools": allowed,
                "prompt": "\n".join(prompt_lines).strip(),
                "model": self.subagent_model,
            }
            agent_name = skill_id.replace("_", "-")
            if AgentDefinition is not None:
                try:
                    output[agent_name] = AgentDefinition(**definition_kwargs)
                    continue
                except Exception:
                    pass
            output[agent_name] = definition_kwargs
        return output

    def _allowed_onboarding_tool_names(self, allowed_patterns: List[str]) -> List[str]:
        if not self.mcp_onboarding_enabled:
            return []
        if not allowed_patterns:
            return list(MCP_ONBOARDING_TOOL_NAMES)
        output: List[str] = []
        for tool_name in MCP_ONBOARDING_TOOL_NAMES:
            if tool_allowed(tool_name, allowed_patterns):
                output.append(tool_name)
        return output

    def _build_mcp_onboarding_tool(
        self,
        *,
        tool_name: str,
        event_sink: Any | None = None,
    ) -> Any:
        if tool_name == "mcp_server_discover":
            return self._build_mcp_server_discover_tool(event_sink=event_sink)
        if tool_name == "mcp_servers_list":
            return self._build_mcp_servers_list_tool(event_sink=event_sink)
        if tool_name == "mcp_server_upsert":
            return self._build_mcp_server_upsert_tool(event_sink=event_sink)
        if tool_name == "mcp_server_test":
            return self._build_mcp_server_test_tool(event_sink=event_sink)
        if tool_name == "mcp_tools_list_by_server":
            return self._build_mcp_tools_list_by_server_tool(event_sink=event_sink)
        if tool_name == "mcp_server_disable":
            return self._build_mcp_server_disable_tool(event_sink=event_sink)
        if tool_name == "mcp_stdio_bridge_plan":
            return self._build_mcp_stdio_bridge_plan_tool(event_sink=event_sink)
        if tool_name == "mcp_stdio_bridge_start":
            return self._build_mcp_stdio_bridge_start_tool(event_sink=event_sink)
        if tool_name == "mcp_stdio_bridge_status":
            return self._build_mcp_stdio_bridge_status_tool(event_sink=event_sink)
        if tool_name == "mcp_stdio_bridge_stop":
            return self._build_mcp_stdio_bridge_stop_tool(event_sink=event_sink)
        if tool_name == "mcp_server_onboard":
            return self._build_mcp_server_onboard_tool(event_sink=event_sink)
        raise ValueError(f"Unsupported MCP onboarding tool: {tool_name}")

    def _instrument_onboarding_tool(
        self,
        *,
        tool: Any,
        tool_events: List[Dict[str, Any]],
        event_sink: Any | None = None,
    ) -> Any:
        tool_name = str(getattr(tool, "name", "") or "").strip()
        description = str(getattr(tool, "description", "") or "").strip() or f"Execute {tool_name}"
        input_schema = getattr(tool, "input_schema", None) if isinstance(getattr(tool, "input_schema", None), dict) else {}
        if not input_schema:
            input_schema = {"type": "object", "properties": {}}
        handler = getattr(tool, "handler", None)
        if not callable(handler) or not tool_name:
            return tool

        @sdk_tool(tool_name, description, input_schema)
        async def _wrapped(args: Any) -> Dict[str, Any]:
            tool_input = args if isinstance(args, dict) else {}
            tool_use_id = f"sdktool_{uuid.uuid4().hex[:12]}"
            tool_events.append(
                {
                    "type": "mcp_tool_use",
                    "tool_name": tool_name,
                    "server_name": "",
                    "tool_use_id": tool_use_id,
                    "input": dict(tool_input),
                }
            )
            self._emit_event(
                event_sink,
                {
                    "event": "tool_progress",
                    "payload": {
                        "phase": "tool_use",
                        "tool_name": tool_name,
                        "server_name": "",
                        "tool_use_id": tool_use_id,
                    },
                },
            )

            try:
                result = handler(args)
                if inspect.isawaitable(result):
                    result = await result
            except Exception as exc:
                error_text = f"Onboarding tool '{tool_name}' failed: {exc}"
                tool_events.append(
                    {
                        "type": "mcp_tool_result",
                        "tool_name": tool_name,
                        "tool_use_id": tool_use_id,
                        "is_error": True,
                        "text_preview": error_text[:220],
                    }
                )
                self._emit_event(
                    event_sink,
                    {
                        "event": "tool_progress",
                        "payload": {
                            "phase": "tool_result",
                            "tool_name": tool_name,
                            "tool_use_id": tool_use_id,
                            "is_error": True,
                            "text_preview": error_text[:220],
                        },
                    },
                )
                raise

            text_preview = ""
            is_error = False
            if isinstance(result, dict):
                is_error = bool(result.get("is_error"))
                content = result.get("content")
                if isinstance(content, list):
                    for item in content:
                        if isinstance(item, dict) and str(item.get("type") or "").strip() == "text":
                            text_preview = str(item.get("text") or "").strip()
                            break

            tool_events.append(
                {
                    "type": "mcp_tool_result",
                    "tool_name": tool_name,
                    "tool_use_id": tool_use_id,
                    "is_error": is_error,
                    "text_preview": text_preview[:220],
                }
            )
            self._emit_event(
                event_sink,
                {
                    "event": "tool_progress",
                    "payload": {
                        "phase": "tool_result",
                        "tool_name": tool_name,
                        "tool_use_id": tool_use_id,
                        "is_error": is_error,
                        "text_preview": text_preview[:220],
                    },
                },
            )
            return result

        return _wrapped

    def _registry_service(self) -> ServerRegistryService:
        if self.server_registry is None:
            self.server_registry = ServerRegistryService(storage=Storage())
        return self.server_registry

    def _server_rows_for_runtime(self, *, enabled_only: Optional[bool] = None) -> List[Dict[str, Any]]:
        rows = self._registry_service().list_servers_internal()
        if enabled_only is True:
            return [row for row in rows if bool(row.get("enabled", True))]
        if enabled_only is False:
            return [row for row in rows if not bool(row.get("enabled", True))]
        return rows

    def _resolve_server_selector(
        self,
        *,
        server_id: str,
        server_name: str,
        enabled_only: Optional[bool] = None,
    ) -> Dict[str, Any]:
        rows = self._server_rows_for_runtime(enabled_only=enabled_only)
        target_id = str(server_id or "").strip()
        if target_id:
            for row in rows:
                if str(row.get("id") or "").strip() == target_id:
                    return row
            raise NotFoundError(
                f"Server '{target_id}' was not found",
                details={"server_id": target_id},
            )

        target_name = str(server_name or "").strip().casefold()
        if target_name:
            for row in rows:
                if str(row.get("name") or "").strip().casefold() == target_name:
                    return row
            raise NotFoundError(
                f"Server '{server_name}' was not found",
                details={"server_name": str(server_name or "").strip()},
            )

        raise RegistryError(
            "validation_error",
            "server_id or server_name is required",
        )

    @staticmethod
    def _safe_json_text(payload: Any) -> str:
        try:
            return json.dumps(payload, ensure_ascii=True, indent=2, default=str)
        except Exception:
            return str(payload)

    def _tool_text_response(self, payload: Dict[str, Any], *, is_error: bool) -> Dict[str, Any]:
        return {
            "content": [{"type": "text", "text": self._safe_json_text(payload)}],
            "is_error": bool(is_error),
        }

    def _http_json_get(
        self,
        *,
        url: str,
        headers: Dict[str, str] | None = None,
    ) -> Any:
        req_headers = {"Accept": "application/json"}
        if isinstance(headers, dict):
            for key, value in headers.items():
                k = str(key or "").strip()
                v = str(value or "").strip()
                if k and v:
                    req_headers[k] = v
        try:
            req = urllib.request.Request(url, headers=req_headers, method="GET")
            with urllib.request.urlopen(req, timeout=self.mcp_discovery_timeout_seconds) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw)
        except urllib.error.HTTPError as exc:
            raise RegistryError(
                "http_error",
                f"HTTP {exc.code} while requesting discovery source",
                details={"url": url, "status": int(exc.code)},
            ) from exc
        except Exception as exc:
            raise RegistryError(
                "network_error",
                "Failed requesting discovery source",
                details={"url": url, "reason": str(exc)},
            ) from exc

    @staticmethod
    def _http_text_get(
        *,
        url: str,
        timeout_seconds: float,
        headers: Dict[str, str] | None = None,
    ) -> str:
        req_headers = {"Accept": "text/html,application/xhtml+xml"}
        if isinstance(headers, dict):
            for key, value in headers.items():
                k = str(key or "").strip()
                v = str(value or "").strip()
                if k and v:
                    req_headers[k] = v
        try:
            req = urllib.request.Request(url, headers=req_headers, method="GET")
            with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            raise RegistryError(
                "http_error",
                f"HTTP {exc.code} while requesting discovery source",
                details={"url": url, "status": int(exc.code)},
            ) from exc
        except Exception as exc:
            raise RegistryError(
                "network_error",
                "Failed requesting discovery source",
                details={"url": url, "reason": str(exc)},
            ) from exc

    @staticmethod
    def _strip_html_text(value: str) -> str:
        text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", str(value or ""))
        text = re.sub(r"(?s)<[^>]+>", " ", text)
        text = html.unescape(text)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    @staticmethod
    def _extract_http_urls(value: str) -> List[str]:
        out: List[str] = []
        seen = set()
        for raw in re.findall(r"https?://[^\s\"'<>()]+", str(value or "")):
            url = str(raw or "").strip().rstrip(".,;:!?)")
            if not url or url in seen:
                continue
            seen.add(url)
            out.append(url)
        return out

    @staticmethod
    def _infer_auth_requirement(*, text: str) -> Tuple[str, List[str]]:
        blob = str(text or "").lower()
        evidence: List[str] = []

        no_auth_patterns = [
            r"\bno auth(?:entication)?\b",
            r"\bno api key\b",
            r"\bwithout (?:an )?api key\b",
            r"\bauth(?:entication)?\s*:\s*none\b",
            r"\bnone required\b",
            r"\bno login required\b",
        ]
        auth_patterns = [
            r"\boauth(?:2(?:\.1)?)?\b",
            r"\bapi key\b",
            r"\bx-api-key\b",
            r"\bauthorization\b",
            r"\bbearer\b",
            r"\baccess token\b",
            r"\blogin required\b",
            r"\bauth(?:entication)? required\b",
        ]

        for pattern in no_auth_patterns:
            if re.search(pattern, blob):
                evidence.append(pattern)
        if evidence:
            return "no_auth_required", evidence

        auth_evidence: List[str] = []
        for pattern in auth_patterns:
            if re.search(pattern, blob):
                auth_evidence.append(pattern)
        if auth_evidence:
            return "auth_required", auth_evidence
        return "unknown", []

    @staticmethod
    def _build_vetting(
        *,
        endpoint: str,
        homepage: str,
        auth_requirement: str,
        source_count: int = 1,
    ) -> Dict[str, Any]:
        endpoint = str(endpoint or "").strip()
        homepage = str(homepage or "").strip()
        checks = {
            "endpoint_present": bool(endpoint),
            "endpoint_https": endpoint.startswith("https://"),
            "endpoint_looks_mcp": bool(re.search(r"/mcp(?:$|[/?#])", endpoint, re.IGNORECASE)),
            "has_repo_or_homepage": bool(homepage),
            "cross_source_verified": int(source_count) > 1,
        }
        score = 0
        if checks["endpoint_present"]:
            score += 25
        if checks["endpoint_https"]:
            score += 20
        if checks["endpoint_looks_mcp"]:
            score += 20
        if checks["has_repo_or_homepage"]:
            score += 15
        if checks["cross_source_verified"]:
            score += 20
        if auth_requirement == "no_auth_required":
            score += 5
        elif auth_requirement == "unknown":
            score -= 5

        verdict = "low"
        if score >= 70:
            verdict = "high"
        elif score >= 45:
            verdict = "medium"
        return {
            "score": int(max(0, min(100, score))),
            "verdict": verdict,
            "checks": checks,
        }

    @staticmethod
    def _extract_items(payload: Any) -> List[Dict[str, Any]]:
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if not isinstance(payload, dict):
            return []
        for key in ("servers", "items", "results", "data"):
            rows = payload.get(key)
            if isinstance(rows, list):
                return [item for item in rows if isinstance(item, dict)]
        return []

    @staticmethod
    def _extract_mcpmarket_server_links(search_html: str, *, limit: int) -> List[str]:
        links: List[str] = []
        seen = set()
        for href in re.findall(r'href=["\'](/server/[^"\'>?#]+)["\']', str(search_html or ""), flags=re.IGNORECASE):
            path = str(href or "").strip()
            if not path:
                continue
            url = f"https://mcpmarket.com{path}"
            if url in seen:
                continue
            seen.add(url)
            links.append(url)
            if len(links) >= limit:
                break
        return links

    def _normalize_mcpmarket_page(self, *, page_url: str, html_text: str) -> Dict[str, Any]:
        title_match = re.search(r"(?is)<h1[^>]*>(.*?)</h1>", html_text or "")
        title = self._strip_html_text(title_match.group(1) if title_match else "")
        if not title:
            title_match = re.search(r"(?is)<title[^>]*>(.*?)</title>", html_text or "")
            title = self._strip_html_text(title_match.group(1) if title_match else "")
        meta_desc_match = re.search(
            r'(?is)<meta[^>]+(?:name|property)=["\'](?:description|og:description)["\'][^>]+content=["\'](.*?)["\']',
            html_text or "",
        )
        description = self._strip_html_text(meta_desc_match.group(1) if meta_desc_match else "")
        visible_text = self._strip_html_text(html_text or "")
        if not description:
            description = visible_text[:320]

        urls = self._extract_http_urls(html_text or "")
        endpoint = ""
        for candidate in urls:
            lower = candidate.lower()
            if "mcpmarket.com" in lower:
                continue
            if re.search(r"/mcp(?:$|[/?#])", lower) or re.search(r"/api(?:$|[/?#])", lower):
                endpoint = candidate
                break
        if not endpoint:
            for candidate in urls:
                lower = candidate.lower()
                if "mcpmarket.com" in lower:
                    continue
                endpoint = candidate
                break

        homepage = ""
        for candidate in urls:
            if "github.com/" in candidate.lower():
                homepage = candidate
                break
        if not homepage:
            for candidate in urls:
                if "mcpmarket.com" in candidate.lower():
                    continue
                homepage = candidate
                break

        auth_requirement, auth_evidence = self._infer_auth_requirement(text=f"{title} {description} {visible_text}")
        transport_mode = self._infer_transport_mode(
            item={"name": title, "description": description, "text": visible_text},
            endpoint=endpoint,
        )
        stdio_launch = self._extract_stdio_launch_spec(
            {"name": title, "description": description, "text": visible_text}
        )
        vetting = self._build_vetting(
            endpoint=endpoint,
            homepage=homepage,
            auth_requirement=auth_requirement,
            source_count=1,
        )

        return {
            "source": "mcpmarket",
            "name": title,
            "description": description,
            "mcp_url": endpoint,
            "homepage": homepage,
            "tags": [],
            "updated_at": "",
            "transport_mode": transport_mode,
            "stdio_launch": stdio_launch,
            "auth_requirement": auth_requirement,
            "auth_evidence": auth_evidence,
            "verification": vetting,
            "raw": {"page_url": page_url},
        }

    @staticmethod
    def _extract_tags(item: Dict[str, Any]) -> List[str]:
        tags = item.get("tags")
        if isinstance(tags, list):
            return [str(tag or "").strip() for tag in tags if str(tag or "").strip()]
        categories = item.get("categories")
        if isinstance(categories, list):
            return [str(tag or "").strip() for tag in categories if str(tag or "").strip()]
        return []

    @staticmethod
    def _extract_mcp_url(item: Dict[str, Any]) -> str:
        for key in ("mcpUrl", "mcp_url", "endpoint", "url"):
            value = str(item.get(key) or "").strip()
            if value:
                return value
        server_block = item.get("server")
        if isinstance(server_block, dict):
            for key in ("mcpUrl", "mcp_url", "endpoint", "url"):
                value = str(server_block.get(key) or "").strip()
                if value:
                    return value

        def _walk_transport(value: Any) -> str:
            if isinstance(value, dict):
                kind = str(
                    value.get("type")
                    or value.get("transport")
                    or value.get("protocol")
                    or value.get("kind")
                    or ""
                ).strip().lower()
                for key in ("url", "endpoint", "mcpUrl", "mcp_url"):
                    candidate = str(value.get(key) or "").strip()
                    if candidate.startswith("http://") or candidate.startswith("https://"):
                        return candidate
                if kind in {"streamable-http", "sse", "http", "https", "websocket"}:
                    for key in ("value", "target"):
                        candidate = str(value.get(key) or "").strip()
                        if candidate.startswith("http://") or candidate.startswith("https://"):
                            return candidate
                for nested in value.values():
                    candidate = _walk_transport(nested)
                    if candidate:
                        return candidate
                return ""
            if isinstance(value, list):
                for entry in value:
                    candidate = _walk_transport(entry)
                    if candidate:
                        return candidate
            return ""

        transports = item.get("transports")
        candidate = _walk_transport(transports)
        if candidate:
            return candidate
        for key in ("transport", "remote", "remotes", "connections", "endpoints"):
            candidate = _walk_transport(item.get(key))
            if candidate:
                return candidate
        blob_candidate = ""
        for candidate in AnthropicAgentSDKRuntime._extract_http_urls(json.dumps(item, ensure_ascii=True, default=str)):
            lower = candidate.lower()
            if "registry.modelcontextprotocol.io" in lower:
                continue
            if "/mcp" in lower or "/sse" in lower or "/api" in lower:
                blob_candidate = candidate
                break
        if blob_candidate:
            return blob_candidate
        return ""

    @staticmethod
    def _infer_transport_mode(*, item: Dict[str, Any], endpoint: str) -> str:
        endpoint_value = str(endpoint or "").strip().lower()
        if endpoint_value.startswith("http://") or endpoint_value.startswith("https://"):
            return "remote_http"
        blob = json.dumps(item, ensure_ascii=True, default=str).lower()
        if "stdio" in blob:
            return "stdio_only"
        if "streamable-http" in blob or "\"sse\"" in blob or "server-sent" in blob:
            return "remote_transport_unknown_endpoint"
        return "unknown"

    @staticmethod
    def _guess_package_name(item: Dict[str, Any]) -> str:
        candidates = [
            item.get("package"),
            item.get("packageName"),
            item.get("npmPackage"),
            item.get("npm_package"),
            item.get("id"),
            item.get("qualifiedName"),
            item.get("name"),
        ]
        server_block = item.get("server")
        if isinstance(server_block, dict):
            candidates.extend(
                [
                    server_block.get("package"),
                    server_block.get("packageName"),
                    server_block.get("npmPackage"),
                    server_block.get("id"),
                    server_block.get("name"),
                ]
            )
        for value in candidates:
            token = str(value or "").strip()
            if not token:
                continue
            if token.startswith("@") and "/" in token:
                return token
            if token.startswith("mcp-") or token.endswith("-mcp") or "mcp-server" in token:
                return token
        blob = json.dumps(item, ensure_ascii=True, default=str)
        for token in re.findall(r"@[a-zA-Z0-9._-]+/[a-zA-Z0-9._-]+", blob):
            return str(token or "").strip()
        return ""

    @staticmethod
    def _extract_stdio_launch_spec(item: Dict[str, Any]) -> Dict[str, Any]:
        command = ""
        args: List[str] = []
        env: Dict[str, str] = {}

        direct_command = str(item.get("command") or "").strip()
        if direct_command:
            command = direct_command
        direct_args = item.get("args")
        if isinstance(direct_args, list):
            args = [str(value or "").strip() for value in direct_args if str(value or "").strip()]
        direct_env = item.get("env")
        if isinstance(direct_env, dict):
            env = {str(k): str(v) for k, v in direct_env.items() if str(k or "").strip()}

        server_block = item.get("server")
        if not command and isinstance(server_block, dict):
            nested_command = str(server_block.get("command") or "").strip()
            if nested_command:
                command = nested_command
            nested_args = server_block.get("args")
            if isinstance(nested_args, list) and not args:
                args = [str(value or "").strip() for value in nested_args if str(value or "").strip()]
            nested_env = server_block.get("env")
            if isinstance(nested_env, dict) and not env:
                env = {str(k): str(v) for k, v in nested_env.items() if str(k or "").strip()}

        package_name = AnthropicAgentSDKRuntime._guess_package_name(item)
        if not command and package_name:
            command = "npx"
            args = ["-y", package_name]

        return {
            "command": command,
            "args": args,
            "env": env,
            "package_name": package_name,
            "ready": bool(command),
        }

    @staticmethod
    def _slugify(value: str) -> str:
        text = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower()).strip("-")
        return text or "mcp-server"

    def _build_stdio_bridge_plan(
        self,
        *,
        server_name: str,
        command: str,
        args: List[str],
        env: Dict[str, str] | None = None,
        bridge_host: str = "0.0.0.0",
        bridge_port: int = 8300,
        bridge_path: str = "/mcp",
    ) -> Dict[str, Any]:
        safe_name = str(server_name or "").strip() or "stdio-mcp"
        slug = self._slugify(safe_name)
        host = str(bridge_host or "").strip() or "0.0.0.0"
        path = str(bridge_path or "").strip() or "/mcp"
        if not path.startswith("/"):
            path = f"/{path}"
        port = max(1, min(65535, int(bridge_port)))
        cmd = str(command or "").strip()
        args_list = [str(value or "").strip() for value in (args or []) if str(value or "").strip()]
        env_map = env if isinstance(env, dict) else {}
        config_payload = {
            "server": {
                "command": cmd,
                "args": args_list,
                "env": {str(k): str(v) for k, v in env_map.items() if str(k or "").strip()},
            }
        }
        config_path = f"/content/{slug}-bridge.json"
        run_cmd = (
            "python -m mcp_http_bridge.main "
            f"--config {config_path} --host {host} --port {port} --path {path}"
        )
        local_endpoint = f"http://127.0.0.1:{port}{path}"
        return {
            "mode": "stdio_bridge_required",
            "bridge_type": "mcp-http-bridge",
            "config_path_suggestion": config_path,
            "config": config_payload,
            "run_command": run_cmd,
            "local_endpoint": local_endpoint,
            "onboard_payload_template": {
                "name": safe_name,
                "endpoint": local_endpoint,
                "description": f"{safe_name} via stdio->HTTP bridge",
                "enabled": True,
            },
            "steps": [
                "Install bridge dependency: python -m pip install mcp-http-bridge",
                f"Write bridge config JSON to {config_path}",
                f"Start bridge: {run_cmd}",
                f"Call mcp_server_onboard with endpoint={local_endpoint}",
            ],
        }

    def _bridge_plan_from_candidate(
        self,
        *,
        candidate: Dict[str, Any],
        bridge_port: int = 8300,
    ) -> Dict[str, Any]:
        name = str(candidate.get("name") or "").strip() or "stdio-mcp"
        launch = candidate.get("stdio_launch") if isinstance(candidate.get("stdio_launch"), dict) else {}
        command = str(launch.get("command") or "").strip()
        args = launch.get("args") if isinstance(launch.get("args"), list) else []
        if not command:
            package_name = str(launch.get("package_name") or "").strip() or self._guess_package_name(candidate)
            if package_name:
                command = "npx"
                args = ["-y", package_name]
        return self._build_stdio_bridge_plan(
            server_name=name,
            command=command,
            args=[str(value or "").strip() for value in args if str(value or "").strip()],
            env=launch.get("env") if isinstance(launch.get("env"), dict) else {},
            bridge_port=bridge_port,
        )

    def _bridge_runtime_key(self, name: str) -> str:
        return self._slugify(name)

    def _ensure_bridge_runtime_dir(self) -> None:
        self.bridge_runtime_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _tail_text_file(path: str, max_lines: int = 80) -> str:
        target = str(path or "").strip()
        if not target:
            return ""
        try:
            lines = Path(target).read_text(encoding="utf-8", errors="ignore").splitlines()
            return "\n".join(lines[-max(1, int(max_lines)) :])
        except Exception:
            return ""

    @staticmethod
    def _http_json_post(url: str, payload: Dict[str, Any], timeout_seconds: float) -> Tuple[int, str]:
        body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
                text = resp.read().decode("utf-8", errors="replace")
                return int(getattr(resp, "status", 200) or 200), text
        except urllib.error.HTTPError as exc:
            text = ""
            try:
                text = exc.read().decode("utf-8", errors="replace")
            except Exception:
                text = str(exc)
            return int(exc.code), text

    def _probe_bridge_health(self, endpoint: str, timeout_seconds: float = 8.0) -> Dict[str, Any]:
        url = str(endpoint or "").strip()
        if not url:
            return {"ok": False, "status": 0, "error": "missing_endpoint"}
        init_payload = {
            "jsonrpc": "2.0",
            "id": "1",
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "clientInfo": {"name": "bridge-health", "version": "0.1.0"},
                "capabilities": {"tools": {}},
            },
        }
        try:
            status, text = self._http_json_post(url, init_payload, timeout_seconds=timeout_seconds)
            ok = status < 500 and status > 0
            return {
                "ok": bool(ok),
                "status": int(status),
                "body_preview": str(text or "")[:400],
            }
        except Exception as exc:
            return {
                "ok": False,
                "status": 0,
                "error": str(exc),
            }

    @staticmethod
    def _bridge_process_running(record: Dict[str, Any]) -> bool:
        proc = record.get("process")
        if proc is None:
            return False
        try:
            return proc.poll() is None
        except Exception:
            return False

    def _wait_for_bridge_ready(
        self,
        *,
        record: Dict[str, Any],
        timeout_seconds: float,
    ) -> Dict[str, Any]:
        start = time.time()
        endpoint = str(record.get("endpoint") or "").strip()
        while time.time() - start < max(1.0, float(timeout_seconds)):
            proc = record.get("process")
            if proc is not None:
                try:
                    if proc.poll() is not None:
                        return {
                            "ok": False,
                            "error": f"bridge_process_exited:{proc.poll()}",
                            "log_tail": self._tail_text_file(str(record.get("log_path") or ""), max_lines=80),
                        }
                except Exception:
                    pass
            health = self._probe_bridge_health(endpoint, timeout_seconds=4.0)
            if bool(health.get("ok")):
                return {"ok": True, "health": health}
            time.sleep(1.0)
        return {
            "ok": False,
            "error": "bridge_start_timeout",
            "health": self._probe_bridge_health(endpoint, timeout_seconds=4.0),
            "log_tail": self._tail_text_file(str(record.get("log_path") or ""), max_lines=80),
        }

    def _start_or_reuse_bridge(
        self,
        *,
        name: str,
        command: str,
        args: List[str],
        env: Dict[str, str] | None,
        bridge_host: str,
        bridge_port: int,
        bridge_path: str,
        wait_seconds: float,
        restart_if_running: bool,
    ) -> Dict[str, Any]:
        if importlib.util.find_spec("mcp_http_bridge.main") is None:
            raise RegistryError(
                "bridge_dependency_missing",
                "mcp-http-bridge is not installed in backend runtime",
                details={"pip_install": "python -m pip install mcp-http-bridge"},
            )

        safe_name = str(name or "").strip()
        if not safe_name:
            raise RegistryError("validation_error", "name is required")

        plan = self._build_stdio_bridge_plan(
            server_name=safe_name,
            command=command,
            args=args,
            env=env,
            bridge_host=bridge_host,
            bridge_port=bridge_port,
            bridge_path=bridge_path,
        )

        key = self._bridge_runtime_key(safe_name)
        existing = self._bridge_processes.get(key) if isinstance(self._bridge_processes.get(key), dict) else None
        if existing and self._bridge_process_running(existing):
            if not restart_if_running:
                return {"ok": True, "action": "reused", "bridge": self._serialize_bridge_record(existing), "plan": plan}
            self._stop_bridge_record(existing)

        self._ensure_bridge_runtime_dir()
        config_path = str(plan.get("config_path_suggestion") or "").strip()
        if not config_path:
            config_path = str(self.bridge_runtime_dir / f"{key}.json")
        else:
            config_path = str(self.bridge_runtime_dir / Path(config_path).name)
        log_path = str(self.bridge_runtime_dir / f"{key}.log")
        Path(config_path).write_text(
            json.dumps(plan.get("config") if isinstance(plan.get("config"), dict) else {}, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )
        cmd = [
            sys.executable,
            "-m",
            "mcp_http_bridge.main",
            "--config",
            config_path,
            "--host",
            str(bridge_host or "0.0.0.0"),
            "--port",
            str(int(bridge_port)),
            "--path",
            str(bridge_path or "/mcp"),
        ]
        env_vars = os.environ.copy()
        extra_env = env if isinstance(env, dict) else {}
        for k, v in extra_env.items():
            key_name = str(k or "").strip()
            if key_name:
                env_vars[key_name] = str(v or "")
        with open(log_path, "a", encoding="utf-8", errors="ignore") as log_file:
            proc = subprocess.Popen(
                cmd,
                stdout=log_file,
                stderr=log_file,
                cwd=str(self.project_root),
                env=env_vars,
                start_new_session=True,
            )

        record = {
            "key": key,
            "name": safe_name,
            "endpoint": str(plan.get("local_endpoint") or "").strip(),
            "config_path": config_path,
            "log_path": log_path,
            "pid": int(proc.pid),
            "process": proc,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "command": cmd,
            "plan": plan,
        }
        self._bridge_processes[key] = record
        readiness = self._wait_for_bridge_ready(record=record, timeout_seconds=max(5.0, float(wait_seconds)))
        if not bool(readiness.get("ok")):
            self._stop_bridge_record(record)
            raise RegistryError(
                "bridge_start_failed",
                "Bridge failed to become ready",
                details={
                    "bridge": self._serialize_bridge_record(record),
                    "readiness": readiness,
                },
            )
        return {
            "ok": True,
            "action": "started",
            "bridge": self._serialize_bridge_record(record),
            "plan": plan,
            "health": readiness.get("health"),
        }

    def _serialize_bridge_record(self, record: Dict[str, Any]) -> Dict[str, Any]:
        payload = {
            "name": str(record.get("name") or "").strip(),
            "key": str(record.get("key") or "").strip(),
            "endpoint": str(record.get("endpoint") or "").strip(),
            "config_path": str(record.get("config_path") or "").strip(),
            "log_path": str(record.get("log_path") or "").strip(),
            "pid": int(record.get("pid") or 0),
            "started_at": str(record.get("started_at") or "").strip(),
            "running": self._bridge_process_running(record),
        }
        if payload["running"]:
            payload["health"] = self._probe_bridge_health(payload["endpoint"], timeout_seconds=4.0)
        return payload

    def _stop_bridge_record(self, record: Dict[str, Any]) -> Dict[str, Any]:
        proc = record.get("process")
        terminated = False
        if proc is not None:
            try:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=4)
                    except Exception:
                        proc.kill()
                        proc.wait(timeout=2)
                terminated = True
            except Exception:
                terminated = False
        key = str(record.get("key") or "").strip()
        if key and key in self._bridge_processes:
            self._bridge_processes.pop(key, None)
        payload = self._serialize_bridge_record(record)
        payload["terminated"] = terminated
        payload["log_tail"] = self._tail_text_file(str(record.get("log_path") or ""), max_lines=80)
        return payload

    def _perform_onboard(
        self,
        *,
        name: str,
        endpoint: str,
        description: str,
        enabled: bool,
        headers: Dict[str, str],
        disable_on_failed_test: bool,
    ) -> Dict[str, Any]:
        upserted = self._upsert_server_from_payload(
            {
                "name": str(name or "").strip(),
                "endpoint": str(endpoint or "").strip(),
                "description": str(description or "").strip(),
                "enabled": bool(enabled),
                "headers": headers,
            }
        )
        server = upserted.get("server") if isinstance(upserted.get("server"), dict) else {}
        server_id = str(server.get("id") or "").strip()
        registry = self._registry_service()
        test_result = registry.test_connection(server_id)
        tools_result: Dict[str, Any] = {"ok": False, "tool_count": 0, "tools": [], "errors": []}
        if bool(test_result.get("ok")):
            rows = [registry.get_server_internal(server_id)]
            catalog = self.tool_router.build_catalog(rows)
            tools_result = {
                "ok": len(catalog.errors) == 0,
                "tool_count": len(catalog.tools),
                "tools": catalog.tools,
                "errors": catalog.errors,
            }

        disabled_after_failure = False
        if disable_on_failed_test and not bool(test_result.get("ok")):
            registry.update_server(server_id, {"enabled": False})
            disabled_after_failure = True

        overall_ok = bool(test_result.get("ok")) and int(tools_result.get("tool_count") or 0) > 0
        return {
            "ok": overall_ok,
            "action": str(upserted.get("action") or ""),
            "server": registry.get_server(server_id),
            "test_result": test_result,
            "tools_result": tools_result,
            "disabled_after_failure": disabled_after_failure,
        }

    @staticmethod
    def _extract_registry_server_name(item: Dict[str, Any]) -> str:
        if not isinstance(item, dict):
            return ""
        direct = str(item.get("name") or item.get("serverName") or item.get("qualifiedName") or "").strip()
        if direct:
            return direct
        server_block = item.get("server")
        if isinstance(server_block, dict):
            nested = str(
                server_block.get("name")
                or server_block.get("serverName")
                or server_block.get("qualifiedName")
                or ""
            ).strip()
            if nested:
                return nested
        return ""

    @staticmethod
    def _extract_official_registry_records(payload: Any) -> Tuple[List[Dict[str, Any]], List[str]]:
        records: List[Dict[str, Any]] = []
        names: List[str] = []

        def _consume_list(rows: Any) -> None:
            if not isinstance(rows, list):
                return
            for row in rows:
                if isinstance(row, dict):
                    records.append(row)
                    name = AnthropicAgentSDKRuntime._extract_registry_server_name(row)
                    if name:
                        names.append(name)
                elif isinstance(row, str):
                    value = str(row or "").strip()
                    if value:
                        names.append(value)

        if isinstance(payload, list):
            _consume_list(payload)
            return records, names
        if not isinstance(payload, dict):
            return records, names

        _consume_list(payload.get("servers"))
        _consume_list(payload.get("items"))
        _consume_list(payload.get("results"))
        _consume_list(payload.get("data"))
        data_block = payload.get("data")
        if isinstance(data_block, dict):
            _consume_list(data_block.get("servers"))
            _consume_list(data_block.get("items"))
            _consume_list(data_block.get("results"))

        if isinstance(payload.get("name"), str):
            records.append(payload)
            name = AnthropicAgentSDKRuntime._extract_registry_server_name(payload)
            if name:
                names.append(name)

        deduped_names: List[str] = []
        seen = set()
        for name in names:
            value = str(name or "").strip()
            if not value or value in seen:
                continue
            seen.add(value)
            deduped_names.append(value)
        return records, deduped_names

    def _normalize_discovery_item(
        self,
        *,
        source: str,
        item: Dict[str, Any],
    ) -> Dict[str, Any]:
        name = str(item.get("name") or item.get("title") or item.get("qualifiedName") or "").strip()
        description = str(item.get("description") or item.get("summary") or item.get("shortDescription") or "").strip()
        homepage = str(item.get("homepage") or item.get("website") or item.get("repositoryUrl") or "").strip()
        mcp_url = self._extract_mcp_url(item)
        tags = self._extract_tags(item)
        updated_at = str(item.get("updatedAt") or item.get("updated_at") or item.get("lastUpdated") or "").strip()
        transport_mode = self._infer_transport_mode(item=item, endpoint=mcp_url)
        stdio_launch = self._extract_stdio_launch_spec(item)
        auth_requirement, auth_evidence = self._infer_auth_requirement(
            text=f"{name} {description} {' '.join(tags)} {homepage} {json.dumps(item, ensure_ascii=True, default=str)}"
        )
        verification = self._build_vetting(
            endpoint=mcp_url,
            homepage=homepage,
            auth_requirement=auth_requirement,
            source_count=1,
        )
        return {
            "source": source,
            "name": name,
            "description": description,
            "mcp_url": mcp_url,
            "homepage": homepage,
            "tags": tags,
            "updated_at": updated_at,
            "transport_mode": transport_mode,
            "stdio_launch": stdio_launch,
            "auth_requirement": auth_requirement,
            "auth_evidence": auth_evidence,
            "verification": verification,
            "raw": item,
        }

    def _discover_from_official_registry(self, *, query: str, limit: int) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        topic = str(query or "").strip()
        encoded = urllib.parse.quote_plus(topic)
        fetch_limit = max(5, min(50, int(limit)))
        base = "https://registry.modelcontextprotocol.io/v0.1/servers"
        urls: List[str] = []
        if topic:
            urls.append(f"{base}?search={encoded}&version=latest&limit={fetch_limit}")
            urls.append(f"{base}?search={encoded}&limit={fetch_limit}")
        else:
            urls.append(f"{base}?version=latest&limit={fetch_limit}")
        urls.append(f"{base}?limit={fetch_limit}")
        last_error: Dict[str, Any] | None = None
        list_payload: Any = None
        list_url = ""
        for url in urls:
            try:
                list_payload = self._http_json_get(url=url)
                list_url = url
                break
            except RegistryError as exc:
                last_error = {"source": "official_registry", "error": exc.to_dict() if hasattr(exc, "to_dict") else {"message": str(exc)}}

        if list_payload is None:
            return [], {"ok": False, "source": "official_registry", "error": last_error or {"message": "unavailable"}}

        records, names = self._extract_official_registry_records(list_payload)
        normalized: List[Dict[str, Any]] = []
        detail_reports: List[Dict[str, Any]] = []
        names_limit = max(3, min(fetch_limit, int(limit)))

        for record in records[: names_limit * 2]:
            item = self._normalize_discovery_item(source="official_registry", item=record)
            endpoint = str(item.get("mcp_url") or "").strip()
            if endpoint.startswith("http://") or endpoint.startswith("https://"):
                item["onboarding_readiness"] = "remote_endpoint_available"
                normalized.append(item)

        detail_names = names[:names_limit]
        for name in detail_names:
            quoted = urllib.parse.quote(str(name or "").strip(), safe="")
            detail_urls = [
                f"https://registry.modelcontextprotocol.io/v0.1/servers/{quoted}/versions/latest",
                f"https://registry.modelcontextprotocol.io/v0.1/servers/{quoted}/versions",
            ]
            detail_payload: Any = None
            detail_url = ""
            for candidate_url in detail_urls:
                try:
                    detail_payload = self._http_json_get(url=candidate_url)
                    detail_url = candidate_url
                    break
                except RegistryError as exc:
                    detail_reports.append(
                        {
                            "ok": False,
                            "source": "official_registry",
                            "name": name,
                            "url": candidate_url,
                            "error": exc.to_dict() if hasattr(exc, "to_dict") else {"message": str(exc)},
                        }
                    )
            if detail_payload is None:
                continue

            detail_records, _ = self._extract_official_registry_records(detail_payload)
            if not detail_records and isinstance(detail_payload, dict):
                detail_records = [detail_payload]
            for record in detail_records:
                row = dict(record)
                if not str(row.get("name") or "").strip():
                    row["name"] = name
                item = self._normalize_discovery_item(source="official_registry", item=row)
                endpoint = str(item.get("mcp_url") or "").strip()
                if endpoint.startswith("http://") or endpoint.startswith("https://"):
                    item["onboarding_readiness"] = "remote_endpoint_available"
                    normalized.append(item)
                else:
                    item["onboarding_readiness"] = "stdio_or_non_remote_transport"
                detail_reports.append(
                    {
                        "ok": True,
                        "source": "official_registry",
                        "name": name,
                        "url": detail_url,
                        "endpoint_found": bool(endpoint),
                        "onboarding_readiness": str(item.get("onboarding_readiness") or ""),
                    }
                )

        deduped = self._dedupe_discovery_candidates(normalized)
        if query.strip():
            deduped = self._filter_discovery_items_by_query(deduped, query=query)

        return deduped[: max(1, int(limit))], {
            "ok": bool(deduped),
            "source": "official_registry",
            "url": list_url,
            "records_seen": len(records),
            "names_seen": len(names),
            "candidates_onboardable": len(deduped),
            "detail_reports": detail_reports[: max(3, int(limit) * 3)],
        }

    def _discover_from_mcpmarket(self, *, query: str, limit: int) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        topic = str(query or "").strip()
        encoded = urllib.parse.quote(topic, safe="")
        search_urls = [
            f"https://mcpmarket.com/search/{encoded}",
            f"https://mcpmarket.com/search?q={urllib.parse.quote_plus(topic)}",
        ]
        link_limit = max(4, min(12, limit * 3))
        server_links: List[str] = []
        seen_links = set()
        search_reports: List[Dict[str, Any]] = []

        for search_url in search_urls:
            try:
                html_text = self._http_text_get(url=search_url, timeout_seconds=self.mcp_discovery_timeout_seconds)
                links = self._extract_mcpmarket_server_links(html_text, limit=link_limit)
                for link in links:
                    if link in seen_links:
                        continue
                    seen_links.add(link)
                    server_links.append(link)
                    if len(server_links) >= link_limit:
                        break
                search_reports.append(
                    {
                        "ok": True,
                        "source": "mcpmarket",
                        "url": search_url,
                        "server_link_count": len(links),
                    }
                )
                if len(server_links) >= link_limit:
                    break
            except RegistryError as exc:
                search_reports.append(
                    {
                        "ok": False,
                        "source": "mcpmarket",
                        "url": search_url,
                        "error": exc.to_dict() if hasattr(exc, "to_dict") else {"message": str(exc)},
                    }
                )

        if not server_links:
            return [], {
                "ok": False,
                "source": "mcpmarket",
                "error": {"message": "No MCP Market server links discovered from search pages"},
                "search_reports": search_reports,
            }

        candidates: List[Dict[str, Any]] = []
        page_reports: List[Dict[str, Any]] = []
        for server_url in server_links[:link_limit]:
            try:
                page_html = self._http_text_get(url=server_url, timeout_seconds=self.mcp_discovery_timeout_seconds)
                candidate = self._normalize_mcpmarket_page(page_url=server_url, html_text=page_html)
                if str(candidate.get("name") or "").strip() or str(candidate.get("mcp_url") or "").strip():
                    candidates.append(candidate)
                page_reports.append({"ok": True, "source": "mcpmarket", "url": server_url})
            except RegistryError as exc:
                page_reports.append(
                    {
                        "ok": False,
                        "source": "mcpmarket",
                        "url": server_url,
                        "error": exc.to_dict() if hasattr(exc, "to_dict") else {"message": str(exc)},
                    }
                )

        if topic:
            candidates = self._filter_discovery_items_by_query(candidates, query=topic)

        return candidates[:limit], {
            "ok": bool(candidates),
            "source": "mcpmarket",
            "search_reports": search_reports,
            "page_reports": page_reports,
        }

    @staticmethod
    def _topic_tokens(query: str) -> List[str]:
        tokens: List[str] = []
        seen = set()
        for token in re.split(r"[^a-z0-9]+", str(query or "").lower()):
            value = token.strip()
            if len(value) < 3 or value in seen:
                continue
            seen.add(value)
            tokens.append(value)
        return tokens

    @staticmethod
    def _filter_discovery_items_by_query(items: List[Dict[str, Any]], *, query: str) -> List[Dict[str, Any]]:
        tokens = AnthropicAgentSDKRuntime._topic_tokens(query)
        if not tokens:
            return list(items)
        filtered: List[Dict[str, Any]] = []
        for item in items:
            text = " ".join(
                [
                    str(item.get("name") or ""),
                    str(item.get("description") or ""),
                    " ".join([str(tag or "") for tag in (item.get("tags") if isinstance(item.get("tags"), list) else [])]),
                ]
            ).lower()
            if any(token in text for token in tokens):
                filtered.append(item)
        return filtered

    def _dedupe_discovery_candidates(self, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        by_key: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
        for row in rows:
            name = str(row.get("name") or "").strip().casefold()
            mcp_url = str(row.get("mcp_url") or "").strip().casefold()
            homepage = str(row.get("homepage") or "").strip().casefold()
            key = (name, mcp_url, homepage)
            if key not in by_key:
                item = dict(row)
                source = str(item.get("source") or "").strip()
                item["sources"] = [source] if source else []
                by_key[key] = item
                continue
            current = by_key[key]
            source = str(row.get("source") or "").strip()
            sources = current.get("sources") if isinstance(current.get("sources"), list) else []
            if source and source not in sources:
                sources.append(source)
                current["sources"] = sources

            if not str(current.get("description") or "").strip():
                current["description"] = str(row.get("description") or "").strip()
            if not str(current.get("homepage") or "").strip():
                current["homepage"] = str(row.get("homepage") or "").strip()
            if not str(current.get("mcp_url") or "").strip():
                current["mcp_url"] = str(row.get("mcp_url") or "").strip()
            if not str(current.get("transport_mode") or "").strip():
                current["transport_mode"] = str(row.get("transport_mode") or "").strip()
            if not isinstance(current.get("stdio_launch"), dict):
                current["stdio_launch"] = row.get("stdio_launch") if isinstance(row.get("stdio_launch"), dict) else {}
            else:
                existing_launch = current.get("stdio_launch") if isinstance(current.get("stdio_launch"), dict) else {}
                incoming_launch = row.get("stdio_launch") if isinstance(row.get("stdio_launch"), dict) else {}
                if incoming_launch:
                    if not str(existing_launch.get("command") or "").strip():
                        existing_launch["command"] = str(incoming_launch.get("command") or "").strip()
                    existing_args = existing_launch.get("args") if isinstance(existing_launch.get("args"), list) else []
                    incoming_args = incoming_launch.get("args") if isinstance(incoming_launch.get("args"), list) else []
                    if not existing_args and incoming_args:
                        existing_launch["args"] = [str(v or "").strip() for v in incoming_args if str(v or "").strip()]
                    existing_package = str(existing_launch.get("package_name") or "").strip()
                    incoming_package = str(incoming_launch.get("package_name") or "").strip()
                    if not existing_package and incoming_package:
                        existing_launch["package_name"] = incoming_package
                    existing_launch["ready"] = bool(str(existing_launch.get("command") or "").strip())
                    current["stdio_launch"] = existing_launch
            current_tags = current.get("tags") if isinstance(current.get("tags"), list) else []
            for tag in row.get("tags") if isinstance(row.get("tags"), list) else []:
                if tag not in current_tags:
                    current_tags.append(tag)
            current["tags"] = current_tags

            auth_set = {
                str(current.get("auth_requirement") or "").strip(),
                str(row.get("auth_requirement") or "").strip(),
            }
            if "auth_required" in auth_set and "no_auth_required" in auth_set:
                current["auth_requirement"] = "unknown"
            elif "auth_required" in auth_set:
                current["auth_requirement"] = "auth_required"
            elif "no_auth_required" in auth_set:
                current["auth_requirement"] = "no_auth_required"
            else:
                current["auth_requirement"] = "unknown"

            evidence = current.get("auth_evidence") if isinstance(current.get("auth_evidence"), list) else []
            for token in row.get("auth_evidence") if isinstance(row.get("auth_evidence"), list) else []:
                if token not in evidence:
                    evidence.append(token)
            current["auth_evidence"] = evidence
            by_key[key] = current

        output: List[Dict[str, Any]] = []
        for item in by_key.values():
            sources = item.get("sources") if isinstance(item.get("sources"), list) else []
            source_count = max(1, len(sources))
            item["verification"] = self._build_vetting(
                endpoint=str(item.get("mcp_url") or "").strip(),
                homepage=str(item.get("homepage") or "").strip(),
                auth_requirement=str(item.get("auth_requirement") or "unknown").strip() or "unknown",
                source_count=source_count,
            )
            item["source_count"] = source_count
            output.append(item)
        return output

    def _existing_coverage_candidates(self, *, query: str) -> List[Dict[str, Any]]:
        enabled_servers = self._server_rows_for_runtime(enabled_only=True)
        if not enabled_servers:
            return []
        tokens = self._topic_tokens(query)
        if not tokens:
            return []
        catalog = self.tool_router.build_catalog(enabled_servers)
        server_tools: Dict[str, List[str]] = {}
        for item in catalog.tools:
            if not isinstance(item, dict):
                continue
            server_id = str(item.get("server_id") or "").strip()
            tool_name = str(item.get("name") or "").strip()
            if not server_id or not tool_name:
                continue
            server_tools.setdefault(server_id, [])
            if tool_name not in server_tools[server_id]:
                server_tools[server_id].append(tool_name)

        output: List[Dict[str, Any]] = []
        for row in enabled_servers:
            server_id = str(row.get("id") or "").strip()
            text = " ".join(
                [
                    str(row.get("name") or ""),
                    str(row.get("description") or ""),
                    " ".join(server_tools.get(server_id, [])),
                ]
            ).lower()
            matches = [token for token in tokens if token in text]
            if not matches:
                continue
            output.append(
                {
                    "id": server_id,
                    "name": str(row.get("name") or "").strip(),
                    "endpoint": str(row.get("endpoint") or "").strip(),
                    "match_tokens": matches,
                    "match_score": min(100, len(matches) * 18),
                    "tool_count": len(server_tools.get(server_id, [])),
                }
            )
        output.sort(key=lambda item: (-int(item.get("match_score") or 0), str(item.get("name") or "")))
        return output

    def _score_discovery_candidates(
        self,
        *,
        query: str,
        candidates: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        trust_weights = {
            "official_registry": 35,
            "mcpmarket": 20,
        }
        tokens = self._topic_tokens(query)
        existing = self._server_rows_for_runtime(enabled_only=None)
        existing_by_name = {str(row.get("name") or "").strip().casefold() for row in existing}
        existing_by_endpoint = {str(row.get("endpoint") or "").strip().casefold() for row in existing}

        scored: List[Dict[str, Any]] = []
        for candidate in candidates:
            item = dict(candidate)
            source = str(item.get("source") or "").strip()
            name = str(item.get("name") or "").strip()
            description = str(item.get("description") or "").strip()
            endpoint = str(item.get("mcp_url") or "").strip()
            tags = item.get("tags") if isinstance(item.get("tags"), list) else []
            text = f"{name} {description} {' '.join([str(tag or '') for tag in tags])}".lower()
            reasons: List[str] = []
            score = int(trust_weights.get(source, 0))
            if score:
                reasons.append(f"source_trust:{source}")

            if endpoint.startswith("http://") or endpoint.startswith("https://"):
                score += 20
                reasons.append("endpoint_present")
            else:
                transport_mode = str(item.get("transport_mode") or "").strip()
                if transport_mode == "stdio_only":
                    score -= 6
                    reasons.append("endpoint_missing_stdio_bridge_required")
                else:
                    score -= 20
                    reasons.append("endpoint_missing_or_non_http")

            verification = item.get("verification") if isinstance(item.get("verification"), dict) else {}
            verification_score = int(verification.get("score") or 0)
            if verification_score > 0:
                score += min(25, verification_score // 4)
                reasons.append(f"verification:{verification_score}")

            source_count = int(item.get("source_count") or 1)
            if source_count > 1:
                score += min(12, (source_count - 1) * 6)
                reasons.append(f"cross_source_verified:{source_count}")

            match_count = 0
            for token in tokens:
                if token in text:
                    match_count += 1
            if match_count:
                score += min(40, match_count * 12)
                reasons.append(f"topic_match:{match_count}")

            auth_requirement = str(item.get("auth_requirement") or "unknown").strip() or "unknown"
            if auth_requirement == "no_auth_required":
                score += 8
                reasons.append("auth:no_auth_required")
            elif auth_requirement == "auth_required":
                score += 0
                reasons.append("auth:auth_required")
            else:
                score -= 4
                reasons.append("auth:unknown")

            transport_mode = str(item.get("transport_mode") or "").strip()
            if transport_mode == "stdio_only":
                score += 4
                reasons.append("transport:stdio_supported_via_bridge")

            already_registered = False
            if name and name.casefold() in existing_by_name:
                score -= 50
                already_registered = True
                reasons.append("duplicate_name")
            if endpoint and endpoint.casefold() in existing_by_endpoint:
                score -= 50
                already_registered = True
                reasons.append("duplicate_endpoint")

            item["score"] = int(score)
            item["already_registered"] = already_registered
            item["reasons"] = reasons
            scored.append(item)

        scored.sort(
            key=lambda item: (
                bool(item.get("already_registered")),
                -int(item.get("score") or 0),
                str(item.get("source") or ""),
                str(item.get("name") or ""),
            )
        )
        return scored

    def _discover_server_candidates(
        self,
        *,
        query: str,
        limit: int,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        rows: List[Dict[str, Any]] = []
        source_reports: List[Dict[str, Any]] = []
        official_rows, official_report = self._discover_from_official_registry(query=query, limit=limit * 3)
        rows.extend(official_rows)
        source_reports.append(official_report)
        rows = self._dedupe_discovery_candidates(rows)
        ranked = self._score_discovery_candidates(query=query, candidates=rows)
        return ranked[: max(1, int(limit))], source_reports

    @staticmethod
    def _normalize_headers(
        headers: Any,
        headers_env: Any,
    ) -> Tuple[Dict[str, str], List[str]]:
        output: Dict[str, str] = {}
        missing_env: List[str] = []

        if isinstance(headers, dict):
            for key, value in headers.items():
                k = str(key or "").strip()
                v = str(value or "").strip()
                if k and v:
                    output[k] = v

        if isinstance(headers_env, dict):
            for key, env_name in headers_env.items():
                header_key = str(key or "").strip()
                env_key = str(env_name or "").strip()
                if not header_key or not env_key:
                    continue
                env_value = str(os.getenv(env_key, "")).strip()
                if env_value:
                    output[header_key] = env_value
                else:
                    missing_env.append(env_key)

        return output, missing_env

    def _upsert_server_from_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        name = str(payload.get("name") or "").strip()
        endpoint = str(payload.get("endpoint") or "").strip()
        if not name:
            raise RegistryError("validation_error", "name is required")
        if not endpoint:
            raise RegistryError("validation_error", "endpoint is required")

        registry = self._registry_service()
        rows = registry.list_servers_internal()
        match: Dict[str, Any] | None = None
        for row in rows:
            if str(row.get("name") or "").strip().casefold() == name.casefold():
                match = row
                break
        if match is None:
            endpoint_lower = endpoint.casefold()
            for row in rows:
                if str(row.get("endpoint") or "").strip().casefold() == endpoint_lower:
                    match = row
                    break

        update_payload = {
            "name": name,
            "endpoint": endpoint,
            "description": str(payload.get("description") or "").strip(),
            "enabled": bool(payload.get("enabled", True)),
            "headers": payload.get("headers") if isinstance(payload.get("headers"), dict) else {},
        }
        if match is not None:
            updated = registry.update_server(str(match.get("id") or ""), update_payload)
            return {"action": "updated", "server": updated}
        created = registry.create_server(update_payload)
        return {"action": "created", "server": created}

    def _build_mcp_servers_list_tool(self, *, event_sink: Any | None = None) -> Any:
        input_schema = {
            "type": "object",
            "properties": {
                "enabled_only": {"type": "boolean"},
            },
            "additionalProperties": False,
        }

        @sdk_tool(
            "mcp_servers_list",
            "List MCP servers currently registered in the dashboard control plane.",
            input_schema,
        )
        async def _mcp_servers_list(args: Any) -> Dict[str, Any]:
            payload = args if isinstance(args, dict) else {}
            enabled_only = _to_bool(payload.get("enabled_only"), False)
            servers = self._registry_service().list_servers()
            if enabled_only:
                servers = [row for row in servers if bool(row.get("enabled", True))]
            response = {
                "ok": True,
                "server_count": len(servers),
                "servers": servers,
            }
            self._emit_event(event_sink, {"event": "mcp_onboarding", "payload": {"action": "list_servers"}})
            return self._tool_text_response(response, is_error=False)

        return _mcp_servers_list

    def _build_mcp_server_discover_tool(self, *, event_sink: Any | None = None) -> Any:
        input_schema = {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer"},
                "include_existing_coverage": {"type": "boolean"},
            },
            "required": ["query"],
            "additionalProperties": False,
        }

        @sdk_tool(
            "mcp_server_discover",
            (
                "Discover candidate MCP servers using the Official MCP Registry REST API, "
                "rank by relevance/trust/operability, and include authenticity + auth requirement vetting."
            ),
            input_schema,
        )
        async def _mcp_server_discover(args: Any) -> Dict[str, Any]:
            if not self.mcp_discovery_enabled:
                return self._tool_text_response(
                    {
                        "ok": False,
                        "error": {
                            "code": "discovery_disabled",
                            "message": "MCP server discovery is disabled by AGENT_SDK_MCP_DISCOVERY_ENABLED",
                        },
                    },
                    is_error=True,
                )

            payload = args if isinstance(args, dict) else {}
            query = str(payload.get("query") or "").strip()
            if not query:
                return self._tool_text_response(
                    {"ok": False, "error": {"code": "validation_error", "message": "query is required"}},
                    is_error=True,
                )
            limit = _to_int(payload.get("limit"), 5)
            limit = max(1, min(10, limit))
            include_existing = _to_bool(payload.get("include_existing_coverage"), True)

            discovered, source_reports = self._discover_server_candidates(query=query, limit=limit)
            for item in discovered:
                endpoint = str(item.get("mcp_url") or "").strip()
                transport_mode = str(item.get("transport_mode") or "").strip()
                if endpoint.startswith("http://") or endpoint.startswith("https://"):
                    item["onboarding_mode"] = "direct_http"
                elif transport_mode == "stdio_only":
                    item["onboarding_mode"] = "stdio_bridge_required"
                    item["bridge_plan"] = self._bridge_plan_from_candidate(candidate=item)
                else:
                    item["onboarding_mode"] = "unknown"

            existing_coverage = self._existing_coverage_candidates(query=query) if include_existing else []
            recommended_existing = existing_coverage[0] if existing_coverage else None

            recommendation: Dict[str, Any] | None = None
            if recommended_existing and int(recommended_existing.get("match_score") or 0) >= 18:
                recommendation = {
                    "type": "reuse_existing",
                    "server_id": str(recommended_existing.get("id") or "").strip(),
                    "server_name": str(recommended_existing.get("name") or "").strip(),
                    "reason": "Existing enabled server appears to cover this request.",
                }
            elif discovered:
                top = discovered[0]
                endpoint = str(top.get("mcp_url") or "").strip()
                transport_mode = str(top.get("transport_mode") or "").strip()
                if endpoint.startswith("http://") or endpoint.startswith("https://"):
                    recommendation = {
                        "type": "onboard_candidate",
                        "name": str(top.get("name") or "").strip(),
                        "mcp_url": endpoint,
                        "source": str(top.get("source") or "").strip(),
                        "score": int(top.get("score") or 0),
                        "auth_requirement": str(top.get("auth_requirement") or "unknown").strip() or "unknown",
                        "verification": top.get("verification") if isinstance(top.get("verification"), dict) else {},
                        "reason": "Top-ranked candidate by trust/relevance/operability.",
                    }
                elif transport_mode == "stdio_only":
                    recommendation = {
                        "type": "bridge_then_onboard",
                        "name": str(top.get("name") or "").strip(),
                        "source": str(top.get("source") or "").strip(),
                        "score": int(top.get("score") or 0),
                        "auth_requirement": str(top.get("auth_requirement") or "unknown").strip() or "unknown",
                        "verification": top.get("verification") if isinstance(top.get("verification"), dict) else {},
                        "bridge_plan": self._bridge_plan_from_candidate(candidate=top),
                        "reason": "Top candidate is stdio-only; bridge to HTTP first, then onboard endpoint.",
                    }
                else:
                    recommendation = {
                        "type": "candidate_needs_manual_endpoint",
                        "name": str(top.get("name") or "").strip(),
                        "source": str(top.get("source") or "").strip(),
                        "score": int(top.get("score") or 0),
                        "auth_requirement": str(top.get("auth_requirement") or "unknown").strip() or "unknown",
                        "verification": top.get("verification") if isinstance(top.get("verification"), dict) else {},
                        "reason": "Candidate found but no reliable remote endpoint was detected.",
                    }

            response = {
                "ok": True,
                "query": query,
                "candidates": discovered,
                "candidate_count": len(discovered),
                "existing_coverage": existing_coverage,
                "recommendation": recommendation,
                "source_reports": source_reports,
                "next_step": (
                    "Confirm the recommended option. For direct_http candidates call mcp_server_onboard with confirmed=true. "
                    "For stdio_bridge_required candidates prefer mcp_stdio_bridge_start with auto_onboard=true and confirmed=true "
                    "(or use mcp_stdio_bridge_plan for manual bridge steps)."
                    if recommendation is not None
                    else "No strong candidates found. Refine query or provide an endpoint manually."
                ),
            }
            self._emit_event(
                event_sink,
                {
                    "event": "mcp_onboarding",
                    "payload": {"action": "discover_servers", "query": query, "candidate_count": len(discovered)},
                },
            )
            return self._tool_text_response(response, is_error=False)

        return _mcp_server_discover

    def _build_mcp_stdio_bridge_plan_tool(self, *, event_sink: Any | None = None) -> Any:
        input_schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "command": {"type": "string"},
                "args": {"type": "array", "items": {"type": "string"}},
                "env": {"type": "object"},
                "package_name": {"type": "string"},
                "bridge_host": {"type": "string"},
                "bridge_port": {"type": "integer"},
                "bridge_path": {"type": "string"},
            },
            "required": ["name"],
            "additionalProperties": False,
        }

        @sdk_tool(
            "mcp_stdio_bridge_plan",
            (
                "Create a concrete stdio-to-HTTP bridge plan (config + commands + local endpoint) "
                "for stdio-only MCP servers so they can be onboarded to the dashboard."
            ),
            input_schema,
        )
        async def _mcp_stdio_bridge_plan(args: Any) -> Dict[str, Any]:
            payload = args if isinstance(args, dict) else {}
            name = str(payload.get("name") or "").strip()
            if not name:
                return self._tool_text_response(
                    {"ok": False, "error": {"code": "validation_error", "message": "name is required"}},
                    is_error=True,
                )

            command = str(payload.get("command") or "").strip()
            args_value = payload.get("args") if isinstance(payload.get("args"), list) else []
            command_args = [str(value or "").strip() for value in args_value if str(value or "").strip()]
            env_map = payload.get("env") if isinstance(payload.get("env"), dict) else {}
            package_name = str(payload.get("package_name") or "").strip()
            if not command and package_name:
                command = "npx"
                command_args = ["-y", package_name]

            if not command:
                return self._tool_text_response(
                    {
                        "ok": False,
                        "error": {
                            "code": "missing_stdio_launch",
                            "message": "Provide command/args or package_name to generate a bridge plan.",
                        },
                    },
                    is_error=True,
                )

            bridge_plan = self._build_stdio_bridge_plan(
                server_name=name,
                command=command,
                args=command_args,
                env={str(k): str(v) for k, v in env_map.items() if str(k or "").strip()},
                bridge_host=str(payload.get("bridge_host") or "").strip() or "0.0.0.0",
                bridge_port=max(1, min(65535, _to_int(payload.get("bridge_port"), 8300))),
                bridge_path=str(payload.get("bridge_path") or "").strip() or "/mcp",
            )
            response = {
                "ok": True,
                "server": {"name": name},
                "bridge_plan": bridge_plan,
                "next_step": (
                    "Run bridge steps, then call mcp_server_onboard using "
                    f"endpoint={bridge_plan.get('local_endpoint')}"
                ),
            }
            self._emit_event(
                event_sink,
                {"event": "mcp_onboarding", "payload": {"action": "build_stdio_bridge_plan", "server_name": name}},
            )
            return self._tool_text_response(response, is_error=False)

        return _mcp_stdio_bridge_plan

    def _build_mcp_stdio_bridge_start_tool(self, *, event_sink: Any | None = None) -> Any:
        input_schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "command": {"type": "string"},
                "args": {"type": "array", "items": {"type": "string"}},
                "env": {"type": "object"},
                "package_name": {"type": "string"},
                "bridge_host": {"type": "string"},
                "bridge_port": {"type": "integer"},
                "bridge_path": {"type": "string"},
                "wait_seconds": {"type": "integer"},
                "restart_if_running": {"type": "boolean"},
                "auto_onboard": {"type": "boolean"},
                "description": {"type": "string"},
                "enabled": {"type": "boolean"},
                "headers": {"type": "object"},
                "headers_env": {"type": "object"},
                "disable_on_failed_test": {"type": "boolean"},
                "confirmed": {"type": "boolean"},
            },
            "required": ["name"],
            "additionalProperties": False,
        }

        @sdk_tool(
            "mcp_stdio_bridge_start",
            (
                "Start or reuse a stdio->HTTP bridge process for a stdio MCP server. "
                "Can auto-onboard the bridged endpoint after health check."
            ),
            input_schema,
        )
        async def _mcp_stdio_bridge_start(args: Any) -> Dict[str, Any]:
            payload = args if isinstance(args, dict) else {}
            name = str(payload.get("name") or "").strip()
            if not name:
                return self._tool_text_response(
                    {"ok": False, "error": {"code": "validation_error", "message": "name is required"}},
                    is_error=True,
                )

            command = str(payload.get("command") or "").strip()
            args_value = payload.get("args") if isinstance(payload.get("args"), list) else []
            command_args = [str(value or "").strip() for value in args_value if str(value or "").strip()]
            package_name = str(payload.get("package_name") or "").strip()
            if not command and package_name:
                command = "npx"
                command_args = ["-y", package_name]
            if not command:
                return self._tool_text_response(
                    {
                        "ok": False,
                        "error": {
                            "code": "missing_stdio_launch",
                            "message": "Provide command/args or package_name to start bridge.",
                        },
                    },
                    is_error=True,
                )

            env_map = payload.get("env") if isinstance(payload.get("env"), dict) else {}
            try:
                bridge_result = self._start_or_reuse_bridge(
                    name=name,
                    command=command,
                    args=command_args,
                    env={str(k): str(v) for k, v in env_map.items() if str(k or "").strip()},
                    bridge_host=str(payload.get("bridge_host") or "").strip() or "0.0.0.0",
                    bridge_port=max(1, min(65535, _to_int(payload.get("bridge_port"), 8300))),
                    bridge_path=str(payload.get("bridge_path") or "").strip() or "/mcp",
                    wait_seconds=max(5, _to_int(payload.get("wait_seconds"), 35)),
                    restart_if_running=_to_bool(payload.get("restart_if_running"), False),
                )
            except RegistryError as exc:
                return self._tool_text_response(
                    {"ok": False, "error": exc.to_dict() if hasattr(exc, "to_dict") else {"message": str(exc)}},
                    is_error=True,
                )

            auto_onboard = _to_bool(payload.get("auto_onboard"), True)
            response: Dict[str, Any] = {
                "ok": True,
                "bridge": bridge_result.get("bridge"),
                "bridge_action": str(bridge_result.get("action") or ""),
                "bridge_health": bridge_result.get("health"),
            }
            if not auto_onboard:
                response["next_step"] = (
                    "Bridge is running. Call mcp_server_onboard with endpoint="
                    f"{(bridge_result.get('bridge') or {}).get('endpoint')}"
                )
                self._emit_event(
                    event_sink,
                    {"event": "mcp_onboarding", "payload": {"action": "start_stdio_bridge", "name": name}},
                )
                return self._tool_text_response(response, is_error=False)

            headers, missing_env = self._normalize_headers(payload.get("headers"), payload.get("headers_env"))
            if missing_env:
                response.update(
                    {
                        "ok": False,
                        "error": {
                            "code": "missing_env_headers",
                            "message": "One or more headers_env variables are missing",
                            "details": {"missing_env": missing_env},
                        },
                    }
                )
                return self._tool_text_response(response, is_error=True)

            confirmed = _to_bool(payload.get("confirmed"), False)
            if self.mcp_discovery_confirm_required and not confirmed:
                response.update(
                    {
                        "ok": False,
                        "confirmation_required": True,
                        "message": (
                            "Discovery/recommendation flow requires explicit confirmation before onboarding. "
                            "Call mcp_stdio_bridge_start again with confirmed=true (or call mcp_server_onboard)."
                        ),
                        "proposed_server": {
                            "name": name,
                            "endpoint": str((bridge_result.get("bridge") or {}).get("endpoint") or ""),
                            "description": str(payload.get("description") or f"{name} via stdio bridge"),
                            "enabled": bool(payload.get("enabled", True)),
                            "header_keys": sorted([str(key) for key in headers.keys()]),
                        },
                    }
                )
                return self._tool_text_response(response, is_error=False)

            try:
                onboard_result = self._perform_onboard(
                    name=name,
                    endpoint=str((bridge_result.get("bridge") or {}).get("endpoint") or ""),
                    description=str(payload.get("description") or f"{name} via stdio bridge"),
                    enabled=bool(payload.get("enabled", True)),
                    headers=headers,
                    disable_on_failed_test=_to_bool(payload.get("disable_on_failed_test"), False),
                )
                response["onboard_result"] = onboard_result
                response["ok"] = bool(onboard_result.get("ok"))
                response["next_step"] = (
                    "Bridge started and onboarding passed."
                    if bool(onboard_result.get("ok"))
                    else "Bridge started but onboarding failed. Inspect test_result/tools_result."
                )
                self._emit_event(
                    event_sink,
                    {
                        "event": "mcp_onboarding",
                        "payload": {
                            "action": "start_bridge_and_onboard",
                            "name": name,
                            "ok": bool(onboard_result.get("ok")),
                        },
                    },
                )
                return self._tool_text_response(response, is_error=not bool(response.get("ok")))
            except RegistryError as exc:
                response.update(
                    {
                        "ok": False,
                        "error": exc.to_dict() if hasattr(exc, "to_dict") else {"message": str(exc)},
                    }
                )
                return self._tool_text_response(response, is_error=True)

        return _mcp_stdio_bridge_start

    def _build_mcp_stdio_bridge_status_tool(self, *, event_sink: Any | None = None) -> Any:
        input_schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
            },
            "additionalProperties": False,
        }

        @sdk_tool(
            "mcp_stdio_bridge_status",
            "Report status for one bridge by name, or list all running/stopped bridge records.",
            input_schema,
        )
        async def _mcp_stdio_bridge_status(args: Any) -> Dict[str, Any]:
            payload = args if isinstance(args, dict) else {}
            name = str(payload.get("name") or "").strip()
            if name:
                key = self._bridge_runtime_key(name)
                row = self._bridge_processes.get(key) if isinstance(self._bridge_processes.get(key), dict) else None
                if row is None:
                    return self._tool_text_response(
                        {
                            "ok": False,
                            "error": {"code": "not_found", "message": f"Bridge '{name}' was not found"},
                        },
                        is_error=True,
                    )
                response = {"ok": True, "bridge": self._serialize_bridge_record(row)}
                self._emit_event(
                    event_sink,
                    {"event": "mcp_onboarding", "payload": {"action": "bridge_status", "name": name}},
                )
                return self._tool_text_response(response, is_error=False)

            rows = [self._serialize_bridge_record(row) for row in self._bridge_processes.values() if isinstance(row, dict)]
            response = {
                "ok": True,
                "bridge_count": len(rows),
                "bridges": rows,
            }
            self._emit_event(
                event_sink,
                {"event": "mcp_onboarding", "payload": {"action": "bridge_status_all", "count": len(rows)}},
            )
            return self._tool_text_response(response, is_error=False)

        return _mcp_stdio_bridge_status

    def _build_mcp_stdio_bridge_stop_tool(self, *, event_sink: Any | None = None) -> Any:
        input_schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "stop_all": {"type": "boolean"},
            },
            "additionalProperties": False,
        }

        @sdk_tool(
            "mcp_stdio_bridge_stop",
            "Stop one stdio bridge by name, or stop all bridges when stop_all=true.",
            input_schema,
        )
        async def _mcp_stdio_bridge_stop(args: Any) -> Dict[str, Any]:
            payload = args if isinstance(args, dict) else {}
            stop_all = _to_bool(payload.get("stop_all"), False)
            name = str(payload.get("name") or "").strip()

            targets: List[Dict[str, Any]] = []
            if stop_all:
                targets = [row for row in self._bridge_processes.values() if isinstance(row, dict)]
            else:
                if not name:
                    return self._tool_text_response(
                        {
                            "ok": False,
                            "error": {"code": "validation_error", "message": "name or stop_all=true is required"},
                        },
                        is_error=True,
                    )
                key = self._bridge_runtime_key(name)
                row = self._bridge_processes.get(key) if isinstance(self._bridge_processes.get(key), dict) else None
                if row is None:
                    return self._tool_text_response(
                        {"ok": False, "error": {"code": "not_found", "message": f"Bridge '{name}' was not found"}},
                        is_error=True,
                    )
                targets = [row]

            stopped: List[Dict[str, Any]] = []
            for row in list(targets):
                stopped.append(self._stop_bridge_record(row))

            response = {
                "ok": True,
                "stopped_count": len(stopped),
                "stopped": stopped,
            }
            self._emit_event(
                event_sink,
                {"event": "mcp_onboarding", "payload": {"action": "bridge_stop", "count": len(stopped)}},
            )
            return self._tool_text_response(response, is_error=False)

        return _mcp_stdio_bridge_stop

    def _build_mcp_server_upsert_tool(self, *, event_sink: Any | None = None) -> Any:
        input_schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "endpoint": {"type": "string"},
                "description": {"type": "string"},
                "enabled": {"type": "boolean"},
                "headers": {"type": "object"},
                "headers_env": {"type": "object"},
                "test_after_upsert": {"type": "boolean"},
                "disable_on_failed_test": {"type": "boolean"},
            },
            "required": ["name", "endpoint"],
            "additionalProperties": False,
        }

        @sdk_tool(
            "mcp_server_upsert",
            (
                "Create or update an MCP server in the dashboard registry by name/endpoint. "
                "Supports secure header injection via headers_env."
            ),
            input_schema,
        )
        async def _mcp_server_upsert(args: Any) -> Dict[str, Any]:
            payload = args if isinstance(args, dict) else {}
            headers, missing_env = self._normalize_headers(payload.get("headers"), payload.get("headers_env"))
            if missing_env:
                return self._tool_text_response(
                    {
                        "ok": False,
                        "error": {
                            "code": "missing_env_headers",
                            "message": "One or more headers_env variables are missing",
                            "details": {"missing_env": missing_env},
                        },
                    },
                    is_error=True,
                )

            try:
                upserted = self._upsert_server_from_payload(
                    {
                        "name": str(payload.get("name") or "").strip(),
                        "endpoint": str(payload.get("endpoint") or "").strip(),
                        "description": str(payload.get("description") or "").strip(),
                        "enabled": bool(payload.get("enabled", True)),
                        "headers": headers,
                    }
                )
                server = upserted.get("server") if isinstance(upserted.get("server"), dict) else {}
                server_id = str(server.get("id") or "").strip()
                test_after = _to_bool(payload.get("test_after_upsert"), True)
                disable_on_failed_test = _to_bool(payload.get("disable_on_failed_test"), False)
                test_result: Dict[str, Any] | None = None
                disabled_after_failure = False
                if server_id and test_after:
                    registry = self._registry_service()
                    test_result = registry.test_connection(server_id)
                    if (
                        isinstance(test_result, dict)
                        and not bool(test_result.get("ok"))
                        and disable_on_failed_test
                    ):
                        registry.update_server(server_id, {"enabled": False})
                        disabled_after_failure = True

                response = {
                    "ok": not (isinstance(test_result, dict) and not bool(test_result.get("ok"))),
                    "action": str(upserted.get("action") or ""),
                    "server": server,
                    "test_result": test_result,
                    "disabled_after_failure": disabled_after_failure,
                }
                self._emit_event(
                    event_sink,
                    {
                        "event": "mcp_onboarding",
                        "payload": {
                            "action": "upsert_server",
                            "server_id": server_id,
                            "ok": bool(response.get("ok")),
                        },
                    },
                )
                return self._tool_text_response(response, is_error=not bool(response.get("ok")))
            except RegistryError as exc:
                return self._tool_text_response(
                    {"ok": False, "error": exc.to_dict() if hasattr(exc, "to_dict") else {"message": str(exc)}},
                    is_error=True,
                )

        return _mcp_server_upsert

    def _build_mcp_server_test_tool(self, *, event_sink: Any | None = None) -> Any:
        input_schema = {
            "type": "object",
            "properties": {
                "server_id": {"type": "string"},
                "server_name": {"type": "string"},
            },
            "additionalProperties": False,
        }

        @sdk_tool(
            "mcp_server_test",
            "Run MCP handshake tests for a registered server and return stage-by-stage diagnostics.",
            input_schema,
        )
        async def _mcp_server_test(args: Any) -> Dict[str, Any]:
            payload = args if isinstance(args, dict) else {}
            try:
                server = self._resolve_server_selector(
                    server_id=str(payload.get("server_id") or "").strip(),
                    server_name=str(payload.get("server_name") or "").strip(),
                    enabled_only=None,
                )
                server_id = str(server.get("id") or "").strip()
                registry = self._registry_service()
                result = registry.test_connection(server_id)
                response = {
                    "ok": bool(result.get("ok")),
                    "server": registry.get_server(server_id),
                    "test_result": result,
                }
                self._emit_event(
                    event_sink,
                    {
                        "event": "mcp_onboarding",
                        "payload": {"action": "test_server", "server_id": server_id, "ok": bool(result.get("ok"))},
                    },
                )
                return self._tool_text_response(response, is_error=not bool(result.get("ok")))
            except RegistryError as exc:
                return self._tool_text_response(
                    {"ok": False, "error": exc.to_dict() if hasattr(exc, "to_dict") else {"message": str(exc)}},
                    is_error=True,
                )

        return _mcp_server_test

    def _build_mcp_tools_list_by_server_tool(self, *, event_sink: Any | None = None) -> Any:
        input_schema = {
            "type": "object",
            "properties": {
                "server_id": {"type": "string"},
                "server_name": {"type": "string"},
                "enabled_only": {"type": "boolean"},
            },
            "additionalProperties": False,
        }

        @sdk_tool(
            "mcp_tools_list_by_server",
            "List tools exposed by one server (or all servers) using live tools/list capability checks.",
            input_schema,
        )
        async def _mcp_tools_list_by_server(args: Any) -> Dict[str, Any]:
            payload = args if isinstance(args, dict) else {}
            enabled_only = _to_bool(payload.get("enabled_only"), True)
            server_id = str(payload.get("server_id") or "").strip()
            server_name = str(payload.get("server_name") or "").strip()
            rows = self._server_rows_for_runtime(enabled_only=enabled_only)

            if server_id or server_name:
                try:
                    selected = self._resolve_server_selector(
                        server_id=server_id,
                        server_name=server_name,
                        enabled_only=enabled_only if enabled_only else None,
                    )
                    rows = [selected]
                except RegistryError as exc:
                    return self._tool_text_response(
                        {"ok": False, "error": exc.to_dict() if hasattr(exc, "to_dict") else {"message": str(exc)}},
                        is_error=True,
                    )

            if not rows:
                return self._tool_text_response(
                    {
                        "ok": False,
                        "error": {
                            "code": "not_found",
                            "message": "No MCP servers matched the filter",
                        },
                    },
                    is_error=True,
                )

            catalog = self.tool_router.build_catalog(rows)
            response = {
                "ok": len(catalog.errors) == 0,
                "tool_count": len(catalog.tools),
                "tools": catalog.tools,
                "servers": [
                    {
                        "id": str(row.get("id") or "").strip(),
                        "name": str(row.get("name") or "").strip(),
                        "endpoint": str(row.get("endpoint") or "").strip(),
                        "enabled": bool(row.get("enabled", True)),
                    }
                    for row in rows
                ],
                "errors": catalog.errors,
                "filters": {
                    "server_id": server_id or None,
                    "server_name": server_name or None,
                    "enabled_only": enabled_only,
                },
            }
            self._emit_event(
                event_sink,
                {
                    "event": "mcp_onboarding",
                    "payload": {"action": "list_tools", "tool_count": int(response.get("tool_count") or 0)},
                },
            )
            is_error = len(catalog.errors) > 0 and len(catalog.tools) == 0
            return self._tool_text_response(response, is_error=is_error)

        return _mcp_tools_list_by_server

    def _build_mcp_server_disable_tool(self, *, event_sink: Any | None = None) -> Any:
        input_schema = {
            "type": "object",
            "properties": {
                "server_id": {"type": "string"},
                "server_name": {"type": "string"},
                "reason": {"type": "string"},
            },
            "additionalProperties": False,
        }

        @sdk_tool(
            "mcp_server_disable",
            "Disable a server in registry. Use this to rollback failed onboarding attempts.",
            input_schema,
        )
        async def _mcp_server_disable(args: Any) -> Dict[str, Any]:
            payload = args if isinstance(args, dict) else {}
            try:
                server = self._resolve_server_selector(
                    server_id=str(payload.get("server_id") or "").strip(),
                    server_name=str(payload.get("server_name") or "").strip(),
                    enabled_only=None,
                )
                server_id = str(server.get("id") or "").strip()
                updated = self._registry_service().update_server(server_id, {"enabled": False})
                response = {
                    "ok": True,
                    "reason": str(payload.get("reason") or "").strip(),
                    "server": updated,
                }
                self._emit_event(
                    event_sink,
                    {"event": "mcp_onboarding", "payload": {"action": "disable_server", "server_id": server_id}},
                )
                return self._tool_text_response(response, is_error=False)
            except RegistryError as exc:
                return self._tool_text_response(
                    {"ok": False, "error": exc.to_dict() if hasattr(exc, "to_dict") else {"message": str(exc)}},
                    is_error=True,
                )

        return _mcp_server_disable

    def _build_mcp_server_onboard_tool(self, *, event_sink: Any | None = None) -> Any:
        input_schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "endpoint": {"type": "string"},
                "description": {"type": "string"},
                "enabled": {"type": "boolean"},
                "headers": {"type": "object"},
                "headers_env": {"type": "object"},
                "disable_on_failed_test": {"type": "boolean"},
                "confirmed": {"type": "boolean"},
            },
            "required": ["name", "endpoint"],
            "additionalProperties": False,
        }

        @sdk_tool(
            "mcp_server_onboard",
            (
                "Idempotent onboarding flow for one MCP server: upsert in registry, test MCP connection, "
                "verify visible tools, and optionally disable on failed test. "
                "Use confirmed=true after user approves a discovery recommendation."
            ),
            input_schema,
        )
        async def _mcp_server_onboard(args: Any) -> Dict[str, Any]:
            payload = args if isinstance(args, dict) else {}
            headers, missing_env = self._normalize_headers(payload.get("headers"), payload.get("headers_env"))
            if missing_env:
                return self._tool_text_response(
                    {
                        "ok": False,
                        "error": {
                            "code": "missing_env_headers",
                            "message": "One or more headers_env variables are missing",
                            "details": {"missing_env": missing_env},
                        },
                    },
                    is_error=True,
                )

            confirmed = _to_bool(payload.get("confirmed"), False)
            if self.mcp_discovery_confirm_required and not confirmed:
                dry_run = {
                    "ok": False,
                    "confirmation_required": True,
                    "message": (
                        "Discovery/recommendation flow requires explicit confirmation before onboarding. "
                        "Review candidates with mcp_server_discover, then call mcp_server_onboard with confirmed=true."
                    ),
                    "proposed_server": {
                        "name": str(payload.get("name") or "").strip(),
                        "endpoint": str(payload.get("endpoint") or "").strip(),
                        "description": str(payload.get("description") or "").strip(),
                        "enabled": bool(payload.get("enabled", True)),
                        "header_keys": sorted([str(key) for key in headers.keys()]),
                    },
                }
                return self._tool_text_response(dry_run, is_error=False)

            try:
                upserted = self._upsert_server_from_payload(
                    {
                        "name": str(payload.get("name") or "").strip(),
                        "endpoint": str(payload.get("endpoint") or "").strip(),
                        "description": str(payload.get("description") or "").strip(),
                        "enabled": bool(payload.get("enabled", True)),
                        "headers": headers,
                    }
                )
                server = upserted.get("server") if isinstance(upserted.get("server"), dict) else {}
                server_id = str(server.get("id") or "").strip()
                registry = self._registry_service()
                test_result = registry.test_connection(server_id)
                tools_result: Dict[str, Any] = {"ok": False, "tool_count": 0, "tools": [], "errors": []}
                if bool(test_result.get("ok")):
                    rows = [registry.get_server_internal(server_id)]
                    catalog = self.tool_router.build_catalog(rows)
                    tools_result = {
                        "ok": len(catalog.errors) == 0,
                        "tool_count": len(catalog.tools),
                        "tools": catalog.tools,
                        "errors": catalog.errors,
                    }

                disable_on_failed_test = _to_bool(payload.get("disable_on_failed_test"), False)
                disabled_after_failure = False
                if disable_on_failed_test and not bool(test_result.get("ok")):
                    registry.update_server(server_id, {"enabled": False})
                    disabled_after_failure = True

                overall_ok = bool(test_result.get("ok")) and int(tools_result.get("tool_count") or 0) > 0
                response = {
                    "ok": overall_ok,
                    "action": str(upserted.get("action") or ""),
                    "server": registry.get_server(server_id),
                    "test_result": test_result,
                    "tools_result": tools_result,
                    "disabled_after_failure": disabled_after_failure,
                }
                self._emit_event(
                    event_sink,
                    {
                        "event": "mcp_onboarding",
                        "payload": {
                            "action": "onboard_server",
                            "server_id": server_id,
                            "ok": overall_ok,
                            "tool_count": int(tools_result.get("tool_count") or 0),
                        },
                    },
                )
                return self._tool_text_response(response, is_error=not overall_ok)
            except RegistryError as exc:
                return self._tool_text_response(
                    {"ok": False, "error": exc.to_dict() if hasattr(exc, "to_dict") else {"message": str(exc)}},
                    is_error=True,
                )

        return _mcp_server_onboard

    def _visualization_tool_allowed(self, allowed_patterns: List[str], *, force: bool = False) -> bool:
        if not self.visualization_tool_enabled:
            return False
        if force:
            return True
        if not allowed_patterns:
            return True
        return tool_allowed("create_visualization", allowed_patterns)

    @staticmethod
    def _is_visualization_request(message: str) -> bool:
        text = str(message or "").strip().lower()
        if not text:
            return False
        keywords = (
            "visualize",
            "visualization",
            "chart",
            "graph",
            "plot",
            "map",
            "heatmap",
            "geospatial",
            "geojson",
            "line chart",
            "bar chart",
            "trend chart",
            "canvas",
            "dashboard",
        )
        return any(token in text for token in keywords)

    def _build_visualization_tool(
        self,
        *,
        visualization_artifacts: List[Dict[str, Any]],
        event_sink: Any | None = None,
    ) -> Any:
        input_schema: Dict[str, Any] = {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "summary": {"type": "string"},
                "chart_type": {
                    "type": "string",
                    "enum": [
                        "bar",
                        "line",
                        "area",
                        "table",
                        "metric",
                        "pie",
                        "donut",
                        "scatter",
                        "histogram",
                        "stacked_bar",
                        "stacked-bar",
                        "column",
                        "kpi",
                        "timeseries",
                        "map",
                        "map_points",
                        "map_heatmap",
                        "heatmap",
                        "geo",
                        "geospatial",
                    ],
                },
                "x_key": {"type": "string"},
                "y_key": {"type": "string"},
                "series_key": {"type": "string"},
                "lat_key": {"type": "string"},
                "lon_key": {"type": "string"},
                "label_key": {"type": "string"},
                "weight_key": {"type": "string"},
                "source": {"type": "string"},
                "chart_options": {"type": "object"},
                "insights": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "records": {
                    "type": "array",
                    "items": {"type": "object"},
                },
            },
            "required": ["title", "chart_type", "records"],
            "additionalProperties": True,
        }

        @sdk_tool(
            "create_visualization",
            (
                "Create a visualization artifact for the dashboard canvas from structured records. "
                "Use after data retrieval/analysis to publish chart-ready output. "
                "Supported chart types: bar, line, area, pie, donut, scatter, histogram, stacked_bar, table, metric, map. "
                "For map, include lat_key/lon_key; use chart_options.map_mode (points or heatmap) and chart_options.basemap (osm or none)."
            ),
            input_schema,
        )
        async def _create_visualization(args: Any) -> Dict[str, Any]:
            payload = args if isinstance(args, dict) else {}
            try:
                artifact = self._normalize_visualization_payload(payload)
            except ValueError as exc:
                return {
                    "content": [{"type": "text", "text": f"Visualization rejected: {exc}"}],
                    "is_error": True,
                }

            visualization_artifacts.append(artifact)
            self._emit_event(
                event_sink,
                {
                    "event": "visualization",
                    "payload": {"artifact": artifact},
                },
            )
            return {
                "content": [{"type": "text", "text": self._visualization_preview_text(artifact)}],
                "is_error": False,
            }

        return _create_visualization

    @staticmethod
    def _canonical_chart_type(chart_type: str) -> str:
        normalized = chart_type.strip().lower().replace(" ", "_").replace("-", "_")
        aliases = {
            "timeseries": "line",
            "time_series": "line",
            "column": "bar",
            "columns": "bar",
            "kpi": "metric",
            "single_value": "metric",
            "stacked": "stacked_bar",
            "map_points": "map",
            "map_heatmap": "map",
            "heatmap": "map",
            "geo": "map",
            "geospatial": "map",
        }
        normalized = aliases.get(normalized, normalized)
        supported = {
            "bar",
            "line",
            "area",
            "table",
            "metric",
            "pie",
            "donut",
            "scatter",
            "histogram",
            "stacked_bar",
            "map",
        }
        if normalized in supported:
            return normalized
        return ""

    @staticmethod
    def _sanitize_chart_options(value: Any) -> Dict[str, Any]:
        if not isinstance(value, dict):
            return {}

        def _clean(node: Any, depth: int = 0) -> Any:
            if depth > 4:
                return None
            if isinstance(node, dict):
                cleaned: Dict[str, Any] = {}
                for idx, (key, val) in enumerate(node.items()):
                    if idx >= 40:
                        break
                    item = _clean(val, depth + 1)
                    if item is None:
                        continue
                    cleaned[str(key)[:80]] = item
                return cleaned
            if isinstance(node, list):
                cleaned_list: List[Any] = []
                for item in node[:80]:
                    cleaned_item = _clean(item, depth + 1)
                    if cleaned_item is None:
                        continue
                    cleaned_list.append(cleaned_item)
                return cleaned_list
            if isinstance(node, (int, float, bool)) or node is None:
                return node
            if isinstance(node, str):
                return node[:240]
            return str(node)[:240]

        cleaned_root = _clean(value, 0)
        return cleaned_root if isinstance(cleaned_root, dict) else {}

    def _normalize_visualization_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        title = str(payload.get("title") or "").strip()
        if not title:
            raise ValueError("title is required")
        title = title[:140]

        chart_type_raw = str(payload.get("chart_type") or "")
        chart_type = self._canonical_chart_type(chart_type_raw)
        if not chart_type:
            raise ValueError(
                "chart_type must be one of: bar, line, area, table, metric, pie, donut, scatter, histogram, stacked_bar, map"
            )

        records_raw = payload.get("records")
        if not isinstance(records_raw, list) or not records_raw:
            raise ValueError("records must be a non-empty array of objects")

        records: List[Dict[str, Any]] = []
        for item in records_raw[:500]:
            if not isinstance(item, dict):
                continue
            row: Dict[str, Any] = {}
            for key, value in item.items():
                row[str(key)] = value
            if row:
                records.append(row)
        if not records:
            raise ValueError("records contained no valid object rows")

        summary = str(payload.get("summary") or "").strip()[:500]
        source = str(payload.get("source") or "").strip()[:180]
        x_key = str(payload.get("x_key") or "").strip()[:100]
        y_key = str(payload.get("y_key") or "").strip()[:100]
        series_key = str(payload.get("series_key") or "").strip()[:100]
        lat_key = str(payload.get("lat_key") or "").strip()[:100]
        lon_key = str(payload.get("lon_key") or "").strip()[:100]
        label_key = str(payload.get("label_key") or "").strip()[:100]
        weight_key = str(payload.get("weight_key") or "").strip()[:100]
        chart_options = self._sanitize_chart_options(payload.get("chart_options"))
        normalized_type = chart_type_raw.strip().lower().replace(" ", "_").replace("-", "_")
        if chart_type == "map":
            if normalized_type in {"map_heatmap", "heatmap"} and not str(chart_options.get("map_mode") or "").strip():
                chart_options["map_mode"] = "heatmap"
            elif normalized_type in {"map_points", "geo", "geospatial"} and not str(chart_options.get("map_mode") or "").strip():
                chart_options["map_mode"] = "points"
        insights_raw = payload.get("insights") if isinstance(payload.get("insights"), list) else []
        insights = [str(item or "").strip()[:240] for item in insights_raw if str(item or "").strip()][:8]

        return {
            "id": f"viz_{uuid.uuid4().hex[:12]}",
            "title": title,
            "summary": summary,
            "chart_type": chart_type,
            "x_key": x_key,
            "y_key": y_key,
            "series_key": series_key,
            "lat_key": lat_key,
            "lon_key": lon_key,
            "label_key": label_key,
            "weight_key": weight_key,
            "chart_options": chart_options,
            "source": source,
            "insights": insights,
            "records": records,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

    @staticmethod
    def _visualization_preview_text(artifact: Dict[str, Any]) -> str:
        chart_type = str(artifact.get("chart_type") or "chart")
        title = str(artifact.get("title") or "Untitled")
        count = len(artifact.get("records") if isinstance(artifact.get("records"), list) else [])
        return f"Visualization ready: {title} ({chart_type}, {count} records)."

    def _sync_native_skills(self) -> None:
        if not self.native_skills_enabled:
            return
        source_dir = self.skill_source_dir
        target_dir = self.native_skill_target_dir
        if not source_dir.exists() or not source_dir.is_dir():
            return
        target_dir.mkdir(parents=True, exist_ok=True)

        for skill_dir in sorted(source_dir.iterdir()):
            if not skill_dir.is_dir():
                continue
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                continue
            target_skill_dir = target_dir / skill_dir.name
            target_skill_dir.mkdir(parents=True, exist_ok=True)
            for path in sorted(skill_dir.rglob("*")):
                if not path.is_file():
                    continue
                rel = path.relative_to(skill_dir)
                dst = target_skill_dir / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                try:
                    if dst.exists() and dst.read_bytes() == path.read_bytes():
                        continue
                except Exception:
                    pass
                shutil.copyfile(path, dst)

    def _filter_supported_options(self, options_kwargs: Dict[str, Any]) -> Dict[str, Any]:
        supported = self._supported_option_keys()
        if not supported:
            return dict(options_kwargs)
        return {key: value for key, value in options_kwargs.items() if key in supported}

    def _supported_option_keys(self) -> set[str]:
        if ClaudeAgentOptions is None:
            return set()
        try:
            signature = inspect.signature(ClaudeAgentOptions.__init__)
            return {str(name) for name in signature.parameters if str(name) != "self"}
        except Exception:
            return set()

    def _apply_resume_option(
        self,
        *,
        options_kwargs: Dict[str, Any],
        supported_keys: set[str],
        sdk_session_id: str,
    ) -> bool:
        value = str(sdk_session_id or "").strip()
        if not value or not supported_keys:
            return False
        for key in ("resume", "resume_session_id"):
            if key in supported_keys:
                options_kwargs[key] = value
                return True
        return False

    def _apply_continue_conversation_option(
        self,
        *,
        options_kwargs: Dict[str, Any],
        supported_keys: set[str],
    ) -> None:
        if "continue_conversation" in supported_keys:
            options_kwargs["continue_conversation"] = True

    def _build_contextual_prompt(self, *, message: str, history: List[Dict[str, Any]]) -> str:
        turns: List[str] = []
        for item in history:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "").strip().lower()
            content = str(item.get("content") or "").strip()
            if role not in {"user", "assistant"} or not content:
                continue
            compact = " ".join(content.split())
            if len(compact) > 800:
                compact = f"{compact[:800].rstrip()}..."
            turns.append(f"{role.upper()}: {compact}")
        if not turns:
            return message
        if len(turns) > self.history_turn_limit:
            turns = turns[-self.history_turn_limit :]
        lines = [
            "Conversation context (oldest to newest):",
            *turns,
            f"USER: {str(message or '').strip()}",
            "Respond to the latest USER message while preserving context from the conversation above.",
        ]
        prompt = "\n\n".join([line for line in lines if str(line).strip()])
        if len(prompt) > self.history_char_limit:
            prompt = prompt[-self.history_char_limit :]
        return prompt

    def _run_sdk_query(
        self,
        *,
        options: Any,
        message: str,
        app_session_id: str,
        prior_sdk_session: str,
        builtin_tool_events: List[Dict[str, Any]],
        event_sink: Any | None = None,
    ) -> Dict[str, Any]:
        try:
            return asyncio.run(
                self._run_sdk_query_async(
                    options=options,
                    message=message,
                    app_session_id=app_session_id,
                    prior_sdk_session=prior_sdk_session,
                    builtin_tool_events=builtin_tool_events,
                    event_sink=event_sink,
                )
            )
        except RuntimeError as exc:
            # Colab and notebooks may have an active loop.
            if "asyncio.run() cannot be called from a running event loop" in str(exc):
                loop = asyncio.new_event_loop()
                try:
                    return loop.run_until_complete(
                        self._run_sdk_query_async(
                            options=options,
                            message=message,
                            app_session_id=app_session_id,
                            prior_sdk_session=prior_sdk_session,
                            builtin_tool_events=builtin_tool_events,
                            event_sink=event_sink,
                        )
                    )
                finally:
                    loop.close()
            raise
        except Exception as exc:
            raise AgentSDKRuntimeError(
                "agent_sdk_runtime_error",
                f"Agent SDK run failed: {exc}",
                retriable=True,
            ) from exc

    async def _run_sdk_query_async(
        self,
        *,
        options: Any,
        message: str,
        app_session_id: str,
        prior_sdk_session: str,
        builtin_tool_events: List[Dict[str, Any]],
        event_sink: Any | None = None,
    ) -> Dict[str, Any]:
        if ClaudeSDKClient is None:
            raise AgentSDKRuntimeError("agent_sdk_unavailable", "ClaudeSDKClient unavailable", retriable=False)

        snapshots: List[str] = []
        sdk_meta: Dict[str, Any] = {}
        usage: Dict[str, Any] = {}
        stop_reason = "end_turn"
        last_stream_text = ""
        mcp_init_status: List[Dict[str, Any]] = []
        mcp_init_seen: set[tuple[str, str, str]] = set()

        async with ClaudeSDKClient(options=options) as client:
            await client.query(prompt=message)
            async for event in client.receive_response():
                for init_status in self._extract_mcp_init_status(event):
                    if not isinstance(init_status, dict):
                        continue
                    key = (
                        str(init_status.get("name") or "").strip(),
                        str(init_status.get("status") or "").strip(),
                        str(init_status.get("error") or "").strip(),
                    )
                    if key in mcp_init_seen:
                        continue
                    mcp_init_seen.add(key)
                    mcp_init_status.append(init_status)
                    self._emit_event(
                        event_sink,
                        {
                            "event": "mcp_init",
                            "payload": init_status,
                        },
                    )
                for builtin_event in self._extract_builtin_tool_events(event):
                    builtin_tool_events.append(builtin_event)
                    self._emit_event(
                        event_sink,
                        {
                            "event": "builtin_tool",
                            "payload": builtin_event,
                        },
                    )
                snapshot = self._extract_assistant_snapshot(event)
                if snapshot:
                    snapshots.append(snapshot)
                    stream_piece = str(snapshot or "")
                    if stream_piece.startswith(last_stream_text):
                        stream_piece = stream_piece[len(last_stream_text) :]
                    if stream_piece:
                        self._emit_event(
                            event_sink,
                            {
                                "event": "delta",
                                "payload": {"text": stream_piece},
                            },
                        )
                    last_stream_text = str(snapshot or "")

                result_payload = self._extract_result_payload(event)
                if not result_payload:
                    continue
                result_text = str(result_payload.get("result_text") or "").strip()
                if result_text:
                    snapshots.append(result_text)
                event_meta = result_payload.get("meta") if isinstance(result_payload.get("meta"), dict) else {}
                if event_meta:
                    sdk_meta = event_meta
                event_usage = result_payload.get("usage") if isinstance(result_payload.get("usage"), dict) else {}
                if event_usage:
                    usage = event_usage
                event_stop_reason = str(result_payload.get("stop_reason") or "").strip()
                if event_stop_reason:
                    stop_reason = event_stop_reason

        final_text = ""
        for item in reversed(snapshots):
            text = str(item or "").strip()
            if text:
                final_text = text
                break
        if not final_text:
            raise AgentSDKRuntimeError("empty_response", "Agent SDK completed with no text output", retriable=False)
        sdk_meta["app_session_id"] = app_session_id
        sdk_meta["prior_sdk_session"] = prior_sdk_session
        return {
            "text": final_text,
            "response_id": sdk_meta.get("session_id"),
            "stop_reason": stop_reason or "end_turn",
            "usage": usage,
            "sdk_meta": sdk_meta,
            "mcp_init_status": mcp_init_status,
        }

    def _extract_builtin_tool_events(self, event: Any) -> List[Dict[str, Any]]:
        content = getattr(event, "content", None)
        if not isinstance(content, list):
            return []
        output: List[Dict[str, Any]] = []
        for block in content:
            payload: Dict[str, Any] = {}
            if isinstance(block, dict):
                payload = dict(block)
            else:
                for field in ("type", "name", "id", "tool_use_id", "input", "is_error", "error"):
                    if hasattr(block, field):
                        payload[field] = getattr(block, field)
            block_type = str(payload.get("type") or "").strip().lower()
            if block_type not in {"tool_use", "tool_result"}:
                continue

            tool_name = str(payload.get("name") or "").strip()
            if tool_name.startswith(f"mcp__{self.server_alias}__"):
                continue

            if block_type == "tool_use":
                output.append(
                    {
                        "type": "builtin_tool_use",
                        "tool_name": tool_name,
                        "tool_use_id": str(payload.get("id") or "").strip(),
                        "input": payload.get("input") if isinstance(payload.get("input"), dict) else {},
                    }
                )
                continue

            result_text = ""
            content_rows = payload.get("content")
            if isinstance(content_rows, list):
                for row in content_rows:
                    if not isinstance(row, dict):
                        continue
                    if str(row.get("type") or "").strip() != "text":
                        continue
                    result_text = str(row.get("text") or "").strip()
                    if result_text:
                        break
            output.append(
                {
                    "type": "builtin_tool_result",
                    "tool_name": tool_name,
                    "tool_use_id": str(payload.get("tool_use_id") or "").strip(),
                    "is_error": bool(payload.get("is_error", False)),
                    "text_preview": result_text[:220],
                    "error": payload.get("error") if isinstance(payload.get("error"), dict) else {},
                }
            )
        return output

    def _extract_assistant_snapshot(self, event: Any) -> str:
        if SdkAssistantMessage is not None and isinstance(event, SdkAssistantMessage):
            return self._extract_text_from_blocks(getattr(event, "content", []))
        content = getattr(event, "content", None)
        if not isinstance(content, list):
            return ""
        event_type = str(getattr(event, "type", "") or "").strip().lower()
        class_name = type(event).__name__.lower()
        if event_type == "assistant" or "assistant" in class_name:
            return self._extract_text_from_blocks(content)
        return ""

    def _extract_text_from_blocks(self, blocks: Any) -> str:
        if not isinstance(blocks, list):
            return ""
        text_parts: List[str] = []
        for block in blocks:
            if SdkTextBlock is not None and isinstance(block, SdkTextBlock):
                piece = str(getattr(block, "text", "") or "").strip()
                if piece:
                    text_parts.append(piece)
                continue
            if isinstance(block, dict):
                if str(block.get("type") or "").strip() != "text":
                    continue
                piece = str(block.get("text") or "").strip()
                if piece:
                    text_parts.append(piece)
                continue
            block_type = str(getattr(block, "type", "") or "").strip()
            if block_type and block_type != "text":
                continue
            piece = str(getattr(block, "text", "") or "").strip()
            if piece:
                text_parts.append(piece)
        return "".join(text_parts).strip()

    def _extract_result_payload(self, event: Any) -> Dict[str, Any]:
        is_result = SdkResultMessage is not None and isinstance(event, SdkResultMessage)
        if not is_result:
            class_name = type(event).__name__.lower()
            is_result = "result" in class_name and hasattr(event, "session_id")
        if not is_result:
            return {}
        meta = {
            "session_id": str(getattr(event, "session_id", "") or "").strip(),
            "duration_ms": getattr(event, "duration_ms", None),
            "duration_api_ms": getattr(event, "duration_api_ms", None),
            "num_turns": getattr(event, "num_turns", None),
            "total_cost_usd": getattr(event, "total_cost_usd", None),
            "is_error": bool(getattr(event, "is_error", False)),
            "subtype": str(getattr(event, "subtype", "") or "").strip(),
        }
        stop_reason = str(getattr(event, "stop_reason", "") or "").strip() or meta["subtype"]
        usage = getattr(event, "usage", None)
        return {
            "result_text": str(getattr(event, "result", "") or "").strip(),
            "meta": meta,
            "usage": usage if isinstance(usage, dict) else {},
            "stop_reason": stop_reason,
        }

    def _extract_mcp_init_status(self, event: Any) -> List[Dict[str, Any]]:
        payload: Dict[str, Any] = {}
        if isinstance(event, dict):
            payload = dict(event)
        else:
            for field in ("type", "subtype", "mcp_servers", "data", "payload"):
                if hasattr(event, field):
                    payload[field] = getattr(event, field)

        event_type = str(payload.get("type") or getattr(event, "type", "") or "").strip().lower()
        if event_type != "system":
            return []
        subtype = str(payload.get("subtype") or getattr(event, "subtype", "") or "").strip().lower()
        if subtype and "init" not in subtype:
            return []

        rows: Any = payload.get("mcp_servers")
        if not isinstance(rows, list):
            data_block = payload.get("data")
            if isinstance(data_block, dict) and isinstance(data_block.get("mcp_servers"), list):
                rows = data_block.get("mcp_servers")
        if not isinstance(rows, list):
            payload_block = payload.get("payload")
            if isinstance(payload_block, dict) and isinstance(payload_block.get("mcp_servers"), list):
                rows = payload_block.get("mcp_servers")
        if not isinstance(rows, list):
            return []

        output: List[Dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = str(
                row.get("name")
                or row.get("server_name")
                or row.get("id")
                or row.get("server")
                or ""
            ).strip()
            status = str(
                row.get("status")
                or row.get("state")
                or row.get("result")
                or row.get("connection")
                or "unknown"
            ).strip()
            error = row.get("error")
            error_text = ""
            if isinstance(error, dict):
                error_text = str(error.get("message") or error.get("code") or "").strip()
            elif error:
                error_text = str(error).strip()
            if not name and not status and not error_text:
                continue
            output.append(
                {
                    "name": name,
                    "status": status or "unknown",
                    "error": error_text,
                }
            )
        return output

    def _tool_result_text(self, result: Any) -> str:
        if isinstance(result, dict):
            content = result.get("content")
            if isinstance(content, list):
                for item in content:
                    if not isinstance(item, dict):
                        continue
                    if str(item.get("type") or "").strip() != "text":
                        continue
                    text = str(item.get("text") or "").strip()
                    if text:
                        return text
            return json.dumps(result, ensure_ascii=True)[:5000]
        if isinstance(result, list):
            return json.dumps(result, ensure_ascii=True)[:5000]
        return str(result or "").strip() or "Tool completed with empty result."

    @staticmethod
    def _emit_event(event_sink: Any | None, event: Dict[str, Any]) -> None:
        if event_sink is None or not isinstance(event, dict):
            return
        try:
            event_sink(event)
        except Exception:
            return
