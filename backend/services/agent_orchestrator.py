from __future__ import annotations

import os
import uuid
from typing import Any, Dict, List

from .anthropic_agent_sdk_runtime import AnthropicAgentSDKRuntime
from .agent_runtime import AgentRuntime, DeterministicMCPRuntime
from .anthropic_mcp_connector import AnthropicMCPConnectorRuntime
from .server_registry import ServerRegistryService
from .skill_packages import SkillPackageRegistry


class AgentOrchestrator:
    def __init__(self, registry: ServerRegistryService) -> None:
        self.registry = registry
        self.skill_registry = SkillPackageRegistry()
        self.runtime = AgentRuntime(
            agent_sdk_runtime=AnthropicAgentSDKRuntime(),
            connector_runtime=AnthropicMCPConnectorRuntime(),
            deterministic_runtime=DeterministicMCPRuntime(),
        )
        self.max_history = max(2, int(os.getenv("AGENT_SESSION_HISTORY", "12") or 12))
        self.default_system_prompt = str(
            os.getenv(
                "AGENT_SYSTEM_PROMPT",
                (
                    "You are an MCP orchestration assistant. Prefer direct facts from MCP tool outputs. "
                    "Route tool usage to the best-matching MCP server based on user intent: "
                    "for NYC/New York/Socrata requests, prefer nyc-opengov tools, especially get_data__nyc_opengov; "
                    "for New York State / NYS / MTA requests, prefer nys-opengov tools, especially get_data__nys_opengov from data.ny.gov; "
                    "for Boston/CKAN requests, prefer ckan__* tools from opencontext-main. "
                    "For Airbnb, rental listing, listing ingest, listing review, photo, or rental comparison requests, "
                    "prefer rental-dashboard tools and preserve that rental context across short follow-up turns. "
                    "Do not treat an empty local review result as proof that Airbnb has no reviews. "
                    "Use generic get_data only when alias-specific tools are unavailable. "
                    "If user intent is ambiguous across servers, ask a clarifying question before deep analysis."
                ),
            )
        ).strip()
        self.sessions: Dict[str, List[Dict[str, Any]]] = {}
        self.session_skill_ids: Dict[str, List[str]] = {}

    def run_turn(
        self,
        *,
        message: str,
        session_id: str | None,
        prefer_connector: bool,
        runtime_preference: str = "",
        event_sink: Any | None = None,
    ) -> Dict[str, Any]:
        prompt = str(message or "").strip()
        if not prompt:
            raise ValueError("message is required")

        current_session_id = str(session_id or "").strip() or str(uuid.uuid4())
        history = list(self.sessions.get(current_session_id, []))
        servers = [row for row in self.registry.list_servers() if bool(row.get("enabled", True))]
        self.skill_registry.refresh()
        skill_context = self.skill_registry.resolve_for_message(
            prompt,
            sticky_skill_ids=self.session_skill_ids.get(current_session_id, []),
        )

        result = self.runtime.run(
            message=prompt,
            servers=servers,
            history=history,
            session_id=current_session_id,
            prefer_connector=prefer_connector,
            system_prompt=self.default_system_prompt,
            skill_context=skill_context,
            runtime_preference=runtime_preference,
            event_sink=event_sink,
        )

        assistant_message = str(result.get("message") or "").strip()
        updated = history + [
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": assistant_message},
        ]
        if len(updated) > self.max_history:
            updated = updated[-self.max_history :]
        self.sessions[current_session_id] = updated
        selected_skill_ids = (
            skill_context.get("selected_skill_ids")
            if isinstance(skill_context.get("selected_skill_ids"), list)
            else []
        )
        if selected_skill_ids:
            self.session_skill_ids[current_session_id] = [
                str(item or "").strip() for item in selected_skill_ids if str(item or "").strip()
            ]

        meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
        meta["session_id"] = current_session_id
        meta["history_size"] = len(updated)
        meta["server_count"] = len(servers)

        return {
            "message": assistant_message,
            "meta": meta,
            "session_id": current_session_id,
        }
