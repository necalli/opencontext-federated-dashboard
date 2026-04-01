from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import inspect
import json
import os
import re
import shutil
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .opencontext_mcp_client import MCPClientError, OpenContextMCPClient
from .skill_packages import tool_allowed
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
        self._session_map: Dict[str, str] = {}

        self.client_factory = client_factory or OpenContextMCPClient
        self.tool_router = tool_router or ToolRouter(client_factory=self.client_factory)

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
        if not active_servers:
            raise AgentSDKRuntimeError(
                "no_servers",
                "No enabled MCP servers are available for Agent SDK runtime",
                retriable=False,
            )

        scoped = skill_context if isinstance(skill_context, dict) else {}
        allowed_patterns = (
            scoped.get("allowed_tool_patterns") if isinstance(scoped.get("allowed_tool_patterns"), list) else []
        )
        selected_skills = scoped.get("selected_skills") if isinstance(scoped.get("selected_skills"), list) else []
        visualization_requested = self._is_visualization_request(prompt)

        catalog = self.tool_router.build_catalog(active_servers)
        available_tools = self._catalog_tools_for_sdk(catalog.tools, allowed_patterns)
        if not available_tools:
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
        visualization_artifacts: List[Dict[str, Any]] = []
        wrapped_tools, allowed_tool_names, internal_to_allowed = self._build_wrapped_tools(
            available_tools=available_tools,
            active_servers=active_servers,
            tool_events=tool_events,
            event_sink=event_sink,
        )
        if not wrapped_tools:
            raise AgentSDKRuntimeError("no_tools", "No tools could be wrapped for Agent SDK runtime", retriable=False)
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
        event_sink: Any | None = None,
    ) -> Dict[str, Any]:
        try:
            return asyncio.run(
                self._run_sdk_query_async(
                    options=options,
                    message=message,
                    app_session_id=app_session_id,
                    prior_sdk_session=prior_sdk_session,
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
        event_sink: Any | None = None,
    ) -> Dict[str, Any]:
        if ClaudeSDKClient is None:
            raise AgentSDKRuntimeError("agent_sdk_unavailable", "ClaudeSDKClient unavailable", retriable=False)

        snapshots: List[str] = []
        sdk_meta: Dict[str, Any] = {}
        usage: Dict[str, Any] = {}
        stop_reason = "end_turn"
        last_stream_text = ""

        async with ClaudeSDKClient(options=options) as client:
            await client.query(prompt=message)
            async for event in client.receive_response():
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
        }

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
